#!/usr/bin/env python3
"""Run a pinned Domux snapshot directly with Transformers on CPU."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import torch
from huggingface_hub import snapshot_download
from transformers import AutoModelForMultimodalLM, AutoProcessor

from normalize import normalize_text, safety_decision
from run_support import RunJournal, finish_record, load_jsonl, provenance, select_rows
from validate_data import input_contract, validate, validate_run_contract


def resolve_snapshot(repo_id: str, revision: str, local_dir: Path | None) -> Path:
    if local_dir is not None:
        if not local_dir.is_dir():
            raise FileNotFoundError(f"snapshot directory does not exist: {local_dir}")
        if local_dir.name != revision or local_dir.parent.name != "snapshots" or local_dir.parent.parent.name != "models--" + repo_id.replace("/", "--"):
            raise ValueError("--snapshot must be the pinned Hugging Face cache snapshot for this repo/revision")
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
    generated = outputs[0][prompt_tokens:]
    eos = model.generation_config.eos_token_id
    eos_ids = eos if isinstance(eos, list) else [eos]
    if len(generated) >= max_new_tokens and int(generated[-1]) not in eos_ids:
        raise ValueError("generation reached token limit without EOS")
    decoded = processor.decode(generated, skip_special_tokens=True)
    return decoded.strip(), latency_ms


def choose_rows(rows: list[dict[str, object]], limit: int | None, sample_ids: str) -> list[dict[str, object]]:
    return select_rows(rows, limit, sample_ids)


def evaluate(
    row: dict[str, object],
    pipeline: str,
    processor: object,
    model: object,
    revision: str,
    run_id: str,
    max_new_tokens: int,
) -> dict[str, object]:
    started = time.perf_counter()
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
    except (RuntimeError, ValueError, KeyError, IndexError, TypeError, OSError) as exc:
        error = type(exc).__name__

    result = finish_record(row, raw_output, latency_ms, error, decision)
    return {
        **row,
        "request_text": request_text,
        **result,
        "total_latency_ms": round((time.perf_counter() - started) * 1000, 3),
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
    parser.add_argument("--data-spec", type=Path)
    parser.add_argument("--freeze", type=Path, help="Verify frozen files before model access")
    parser.add_argument("--pipeline", choices=("raw", "normalized"), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--environment-output", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dtype", choices=("bfloat16", "float32"), default="bfloat16")
    parser.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-ids", default="")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.revision):
        parser.error("--revision must be a full 40-character Hugging Face SHA")
    if args.threads <= 0 or args.max_new_tokens <= 0 or not args.run_id.strip():
        parser.error("threads/max-new-tokens must be positive and run-id non-empty")
    if args.output is None:
        args.output = root / "artifacts" / f"{args.pipeline}_outputs.jsonl"
    if args.environment_output is None:
        args.environment_output = root / "artifacts" / f"{args.pipeline}_environment.json"

    torch.set_num_threads(args.threads)
    dataset = load_jsonl(args.data)
    spec, contract_settings = input_contract(args.data, args.data_spec, args.freeze)
    errors = validate(dataset, spec)
    if errors:
        parser.error("; ".join(errors))
    rows = choose_rows(dataset, args.limit, args.sample_ids)
    settings = {"backend": "transformers-cpu", "repo_id": args.repo_id, "dtype": args.dtype,
                "threads": args.threads, "max_new_tokens": args.max_new_tokens, "do_sample": False}
    settings.update(contract_settings)
    validate_run_contract(args.freeze, settings, args.revision)
    metadata = {**provenance(rows, settings), "revision": args.revision, "run_id": args.run_id,
                "pipeline": args.pipeline, "sample_count": len(rows), "backend": "transformers-cpu"}
    journal = RunJournal(args.output, args.environment_output, rows, metadata, args.resume)
    if len(journal.completed) == len(rows):
        return journal.finish()
    snapshot = resolve_snapshot(args.repo_id, args.revision, args.snapshot)
    processor, model, load_seconds, rss_delta = load_model(snapshot, args.dtype)

    for index, row in enumerate(rows[len(journal.completed):], start=len(journal.completed) + 1):
        result = evaluate(row, args.pipeline, processor, model, args.revision, args.run_id, args.max_new_tokens)
        journal.append(result)
        print(f"[{args.pipeline}] {index}/{len(rows)} {row['id']} latency_ms={result['latency_ms']} error={result['error'] is not None}", flush=True)

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
    return journal.finish(environment)


if __name__ == "__main__":
    raise SystemExit(main())
