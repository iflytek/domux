#!/usr/bin/env python3
"""Run the SeniorSafe dataset against an OpenAI-compatible Domux endpoint."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from normalize import normalize_text, safety_decision


def load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def call_model(base_url: str, api_key: str, model: str, text: str, timeout: float, max_tokens: int) -> tuple[str, float]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": text}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    latency_ms = (time.perf_counter() - started) * 1000
    return str(body["choices"][0]["message"]["content"]), latency_ms


def parse_output(output: str) -> tuple[list[list[str]], bool]:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    parsed = [line.split("|") for line in lines]
    return parsed, bool(parsed) and all(len(fields) == 7 for fields in parsed)


def canonical_set(output: str) -> set[tuple[str, ...]]:
    parsed, valid = parse_output(output)
    if not valid:
        return set()
    return {tuple(field.strip() for field in row) for row in parsed}


def evaluate_record(
    row: dict[str, object],
    pipeline: str,
    base_url: str,
    api_key: str,
    model: str,
    revision: str,
    run_id: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, object]:
    source_text = str(row["text"])
    normalized_text: str | None = None
    edits: list[dict[str, str]] = []
    request_text = source_text
    if pipeline == "normalized":
        normalized_text, edits = normalize_text(source_text)
        request_text = normalized_text
    decision, safety_reasons = safety_decision(source_text)

    raw_output = ""
    latency_ms = 0.0
    error: str | None = None
    try:
        raw_output, latency_ms = call_model(base_url, api_key, model, request_text, timeout, max_tokens)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, ValueError) as exc:
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
    }


def write_environment(path: Path, args: argparse.Namespace, sample_count: int) -> None:
    environment = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "revision": args.revision,
        "model": args.model,
        "pipeline": args.pipeline,
        "sample_count": sample_count,
        "python": sys.version,
        "platform": platform.platform(),
        "runtime": os.environ.get("DOMUX_RUNTIME", "unknown; set DOMUX_RUNTIME"),
        "hardware": os.environ.get("DOMUX_HARDWARE", "unknown; set DOMUX_HARDWARE"),
        "precision": os.environ.get("DOMUX_PRECISION", "unknown; set DOMUX_PRECISION"),
        "base_url_redacted": "configured" if args.base_url else "missing",
        "temperature": 0.0,
        "max_tokens": args.max_tokens,
        "timeout_seconds": args.timeout,
        "warmup_samples": args.warmup,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(environment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=root / "data" / "seniorsafe.jsonl")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--environment-output", type=Path)
    parser.add_argument("--pipeline", choices=("raw", "normalized"), required=True)
    parser.add_argument("--base-url", default=os.environ.get("DOMUX_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("DOMUX_API_KEY", ""))
    parser.add_argument("--model", default=os.environ.get("DOMUX_MODEL", "domux"))
    parser.add_argument("--revision", default=os.environ.get("DOMUX_REVISION", ""))
    parser.add_argument("--run-id", default=os.environ.get("DOMUX_RUN_ID", ""))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    if not args.base_url:
        parser.error("set --base-url or DOMUX_BASE_URL")
    if not args.revision or len(args.revision) != 40:
        parser.error("set a tested 40-character --revision or DOMUX_REVISION")
    if not args.run_id:
        parser.error("set --run-id or DOMUX_RUN_ID")
    if args.output is None:
        args.output = root / "artifacts" / f"{args.pipeline}_outputs.jsonl"
    if args.environment_output is None:
        args.environment_output = root / "artifacts" / f"{args.pipeline}_environment.json"

    rows = load_jsonl(args.data)
    if args.limit is not None:
        rows = rows[: args.limit]

    # Warm-up uses the first request but does not write results. The caller can
    # set zero for smoke tests or a non-serving environment.
    for row in rows[: args.warmup]:
        request_text = str(row["text"])
        if args.pipeline == "normalized":
            request_text, _ = normalize_text(request_text)
        call_model(args.base_url, args.api_key, args.model, request_text, args.timeout, args.max_tokens)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for index, row in enumerate(rows, start=1):
            result = evaluate_record(
                row,
                args.pipeline,
                args.base_url,
                args.api_key,
                args.model,
                args.revision,
                args.run_id,
                args.timeout,
                args.max_tokens,
            )
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            print(f"[{args.pipeline}] {index}/{len(rows)} {row['id']} error={result['error'] is not None}")

    write_environment(args.environment_output, args, len(rows))
    print(f"[seniorsafe] wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
