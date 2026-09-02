#!/usr/bin/env python3
"""Run the upstream 4,057-sample evaluation without hard-coded credentials."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

from common import (
    ARTIFACTS_DIR,
    DATASET_PATH,
    RAW_ARTIFACTS_DIR,
    REPO_ROOT,
    api_config,
    ensure_artifact_dirs,
    env_int,
)


def main() -> int:
    ensure_artifact_dirs()
    sys.path.insert(0, str(REPO_ROOT))
    from eval import run_eval

    config = api_config()
    workers = env_int("DOMUX_MAX_WORKERS", 20, maximum=20)
    warmup_samples = env_int("DOMUX_EVAL_WARMUP_SAMPLES", 5, minimum=0, maximum=1000)
    raw_results = RAW_ARTIFACTS_DIR / "eval_results.jsonl"
    summary_file = ARTIFACTS_DIR / "eval_summary.json"
    metadata_file = ARTIFACTS_DIR / "eval_run_metadata.json"

    run_eval.API_KEY = config["api_key"]
    run_eval.BASE_URL = config["base_url"]
    run_eval.MODEL = config["model"]
    run_eval.INPUT_FILE = str(DATASET_PATH)
    run_eval.OUTPUT_FILE = str(raw_results)
    run_eval.SUMMARY_FILE = str(summary_file)
    run_eval.MAX_WORKERS = workers
    run_eval.MAX_TOKENS = config["max_tokens"]
    run_eval.WARMUP_SAMPLES = warmup_samples

    original_call = run_eval.call_model_api

    def configured_call(query: str, timeout: int = config["timeout"]):
        return original_call(query, timeout=timeout)

    run_eval.call_model_api = configured_call
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    run_eval.main()
    finished_at = datetime.now(timezone.utc)

    results: list[dict] = []
    if raw_results.exists():
        with raw_results.open(encoding="utf-8") as handle:
            results = [json.loads(line) for line in handle if line.strip()]
    error_count = sum(1 for item in results if item.get("error"))
    metadata = {
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": finished_at.isoformat(),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "model": config["model"],
        "endpoint": "local OpenAI-compatible /v1/chat/completions",
        "temperature": 0.0,
        "max_tokens": config["max_tokens"],
        "max_workers": workers,
        "request_timeout_seconds": config["timeout"],
        "latency_warmup_samples": warmup_samples,
        "dataset_expected_samples": 4057,
        "completed_samples": len(results),
        "api_error_count": error_count,
        "latency_definition": f"end-to-end request latency under concurrency {workers}; not TTFT",
    }
    metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if len(results) != 4057:
        print(f"ERROR: expected 4057 results, got {len(results)}", file=sys.stderr)
        return 1
    if error_count:
        print(f"ERROR: {error_count} API requests failed; preserve the evidence and rerun", file=sys.stderr)
        return 1
    print(f"Evaluation complete: {len(results)} samples, 0 API errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
