#!/usr/bin/env python3
"""Run the SeniorSafe dataset against an OpenAI-compatible Domux endpoint."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from normalize import normalize_text, safety_decision
from protocol import parse_output
from run_support import RunJournal, finish_record, load_jsonl, provenance, select_rows
from validate_data import validate


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
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
        if not isinstance(content, str) or choice.get("finish_reason") not in (None, "stop"):
            raise ValueError("non-text or incomplete model response")
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("malformed model response") from exc
    return content, latency_ms


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
    started = time.perf_counter()
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
    except (OSError, TimeoutError, KeyError, ValueError, IndexError, TypeError) as exc:
        error = type(exc).__name__  # Do not persist endpoint URLs or server response secrets.

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
    }


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
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if not args.base_url:
        parser.error("set --base-url or DOMUX_BASE_URL")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.revision):
        parser.error("set a tested 40-character --revision or DOMUX_REVISION")
    if args.timeout <= 0 or args.max_tokens <= 0 or args.warmup < 0:
        parser.error("timeout/max-tokens must be positive and warmup non-negative")
    if not args.run_id:
        parser.error("set --run-id or DOMUX_RUN_ID")
    if args.output is None:
        args.output = root / "artifacts" / f"{args.pipeline}_outputs.jsonl"
    if args.environment_output is None:
        args.environment_output = root / "artifacts" / f"{args.pipeline}_environment.json"

    rows = load_jsonl(args.data)
    errors = validate(rows)
    if errors:
        parser.error("; ".join(errors))
    rows = select_rows(rows, args.limit)
    settings = {"backend": "openai-compatible", "model": args.model, "temperature": 0.,
                "max_tokens": args.max_tokens, "timeout": args.timeout, "warmup": args.warmup}
    metadata = {**provenance(rows, settings), "revision": args.revision, "run_id": args.run_id,
                "pipeline": args.pipeline, "sample_count": len(rows),
                "backend": "openai-compatible", "python": sys.version, "platform": platform.platform(),
                "model_revision_verified": False}
    journal = RunJournal(args.output, args.environment_output, rows, metadata, args.resume)

    # Warm-up uses the first request but does not write results. The caller can
    # set zero for smoke tests or a non-serving environment.
    for row in ([] if journal.completed else rows[: args.warmup]):
        request_text = str(row["text"])
        if args.pipeline == "normalized":
            request_text, _ = normalize_text(request_text)
        try:
            call_model(args.base_url, args.api_key, args.model, request_text, args.timeout, args.max_tokens)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            journal.finish({"warmup_failed": True})
            return 1

    for index, row in enumerate(rows[len(journal.completed):], start=len(journal.completed) + 1):
        result = evaluate_record(row, args.pipeline, args.base_url, args.api_key, args.model,
                                 args.revision, args.run_id, args.timeout, args.max_tokens)
        journal.append(result)
        print(f"[{args.pipeline}] {index}/{len(rows)} {row['id']} error={result['error'] is not None}", flush=True)
    return journal.finish({"recorded_at": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    raise SystemExit(main())
