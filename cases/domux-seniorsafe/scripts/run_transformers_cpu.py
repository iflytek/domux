#!/usr/bin/env python3
"""Run a pinned Domux snapshot directly with Transformers on CPU."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import torch
from huggingface_hub import snapshot_download
from transformers import AutoModelForMultimodalLM, AutoProcessor

from normalize import normalize_text, safety_decision
from run_eval import canonical_set, load_jsonl, parse_output


def resolve_snapshot(repo_id: str, revision: str, local_dir: Path | None) -> Path:
    if local_dir is not None:
        if not local_dir.is_dir():
            raise FileNotFoundError(f"snapshot directory does not exist: {local_dir}")
        return local_dir.resolve()
    return Path(snapshot_download(repo_id=repo_id, revision=revision)).resolve()


def load_model(snapshot: Path, dtype_name: str) -> tuple[object, object, float, int]:
    dtype = torch.bfloat16 if dtype_name == "bfloat16" else torch.float32
    process = psutil.Process()
    before_rss = process.memory_info().rss
    started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(snapshot, local_files_only=True)
    model = AutoModelForMultimodalLM.from_pretrained(
        snapshot,
        dtype=dtype,
        device_map="cpu",
        low_cpu_mem_usage=True,
        local_files_only=True,
    )
    model.eval()
    load_seconds = time.perf_counter() - started
    rss_delta = max(0, process.memory_info().rss - before_rss)
    return processor, model, load_seconds, rss_delta


def generate(processor: object, model: object, text: str, max_new_tokens: int) -> tuple[str, float]:
    messages = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    )
    started = time.perf_counter()
    with torch.inference_mode():
        outputs = model.generate(
            **inputs,
            do_sample=False,
            max_new_tokens=max_new_tokens,
            use_cache=True,
        )
    latency_ms = (time.perf_counter() - started) * 1000
    prompt_tokens = inputs["input_ids"].shape[-1]
    decoded = processor.decode(outputs[0][prompt_tokens:], skip_special_tokens=True)
    return decoded.strip(), latency_ms


def choose_rows(rows: list[dict[str, object]], limit: int | None, sample_ids: str) -> list[dict[str, object]]:
    if sample_ids:
        requested = [item.strip() for item in sample_ids.split(",") if item.strip()]
        by_id = {str(row["id"]): row for row in rows}
        missing = [row_id for row_id in requested if row_id not in by_id]
        if missing:
            raise ValueError(f"unknown sample ids: {missing}")
        return [by_id[row_id] for row_id in requested]
    return rows[:limit] if limit is not None else rows


def evaluate(
    row: dict[str, object],
    pipeline: str,
    processor: object,
    model: object,
    revision: str,
    run_id: str,
    max_new_tokens: int,
) -> dict[str, object]:
    source_text = str(row["text"])
    request_text = source_text
    normalized_text: str | None = None
    edits: list[dict[str, str]] = []
    if pipeline == "normalized":
        normalized_text, edits = normalize_text(source_text)
        request_text = normalized_text

    decision, safety_reasons = safety_decision(source_text)
    raw_output = ""
    latency_ms = 0.0
    error: str | None = None
    try:
        raw_output, latency_ms = generate(processor, model, request_text, max_new_tokens)
    except (RuntimeError, ValueError, KeyError) as exc:
        error = f"{type(exc).__name__}: {exc}"

    parsed, format_valid = parse_output(raw_output) if not error else ([], False)
    result_correct = bool(row["evaluate_parse"]) and canonical_set(raw_output) == canonical_set(str(row["gold"]))
    return {
        **row,
        "request_text": request_text,
        "raw_output": raw_output,
        "parsed": parsed,
        "format_valid": format_valid,
        "result_correct": result_correct,
        "latency_ms": round(latency_ms, 3),
        "error": error,
        "normalized_text": normalized_text,
        "normalization_edits": edits,
        "safety_decision": decision,
        "safety_reasons": safety_reasons,
        "revision": revision,
        "run_id": run_id,
        "pipeline": pipeline,
        "backend": "transformers-cpu",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", default="iFlytekOpenSource/Domux")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--data", type=Path, default=root / "data" / "seniorsafe.jsonl")
    parser.add_argument("--pipeline", choices=("raw", "normalized"), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--environment-output", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dtype", choices=("bfloat16", "float32"), default="bfloat16")
    parser.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-ids", default="")
    args = parser.parse_args()

    if len(args.revision) != 40:
        parser.error("--revision must be a full 40-character Hugging Face SHA")
    if args.output is None:
        args.output = root / "artifacts" / f"{args.pipeline}_outputs.jsonl"
    if args.environment_output is None:
        args.environment_output = root / "artifacts" / f"{args.pipeline}_environment.json"

    torch.set_num_threads(args.threads)
    rows = choose_rows(load_jsonl(args.data), args.limit, args.sample_ids)
    snapshot = resolve_snapshot(args.repo_id, args.revision, args.snapshot)
    processor, model, load_seconds, rss_delta = load_model(snapshot, args.dtype)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for index, row in enumerate(rows, start=1):
            result = evaluate(
                row,
                args.pipeline,
                processor,
                model,
                args.revision,
                args.run_id,
                args.max_new_tokens,
            )
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            print(
                f"[{args.pipeline}] {index}/{len(rows)} {row['id']} "
                f"latency_ms={result['latency_ms']} error={result['error'] is not None}",
                flush=True,
            )

    environment = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "repo_id": args.repo_id,
        "revision": args.revision,
        "snapshot_size_bytes": sum(path.stat().st_size for path in snapshot.rglob("*") if path.is_file()),
        "snapshot_path_redacted": snapshot.name,
        "pipeline": args.pipeline,
        "sample_count": len(rows),
        "python": sys.version,
        "platform": platform.platform(),
        "processor_class": type(processor).__name__,
        "model_class": type(model).__name__,
        "torch": torch.__version__,
        "transformers_backend": "cpu",
        "dtype": args.dtype,
        "threads": args.threads,
        "load_seconds": round(load_seconds, 3),
        "model_load_rss_delta_bytes": rss_delta,
        "system_ram_bytes": psutil.virtual_memory().total,
        "max_new_tokens": args.max_new_tokens,
        "do_sample": False,
    }
    args.environment_output.parent.mkdir(parents=True, exist_ok=True)
    args.environment_output.write_text(json.dumps(environment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[seniorsafe] wrote {args.output} and {args.environment_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
