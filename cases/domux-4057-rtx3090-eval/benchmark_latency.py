#!/usr/bin/env python3
"""Measure sequential end-to-end latency with a fixed, stratified sample."""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone

from common import (
    ARTIFACTS_DIR,
    ensure_artifact_dirs,
    env_int,
    load_samples,
    percentile_nearest_rank,
    request_completion,
    stratified_samples,
)


def summarize(latencies: list[float]) -> dict[str, float | int]:
    total = sum(latencies)
    return {
        "count": len(latencies),
        "median_seconds": round(statistics.median(latencies), 6),
        "p95_seconds_nearest_rank": round(percentile_nearest_rank(latencies, 95), 6),
        "mean_seconds": round(statistics.fmean(latencies), 6),
        "throughput_requests_per_second": round(len(latencies) / total, 4) if total else 0.0,
    }


def main() -> int:
    ensure_artifact_dirs()
    warmup_count = env_int("DOMUX_LATENCY_WARMUP", 20, maximum=1000)
    measured_count = env_int("DOMUX_LATENCY_SAMPLES", 100, maximum=4057)
    repeats = env_int("DOMUX_LATENCY_REPEATS", 3, maximum=20)
    samples = load_samples()
    measured = stratified_samples(samples, measured_count)
    offset = (measured_count + 3) // 4
    warmups = stratified_samples(samples, warmup_count, offset_per_category=offset)

    print(f"Warm-up: {warmup_count} sequential requests")
    warmup_errors = []
    for sample in warmups:
        _, _, error = request_completion(sample["query"])
        if error:
            warmup_errors.append({"idx": sample["idx"], "error": error})
    if warmup_errors:
        print(f"ERROR: {len(warmup_errors)} warm-up requests failed", file=sys.stderr)
        return 1

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    runs = []
    all_latencies: list[float] = []
    all_errors = []
    for repeat in range(1, repeats + 1):
        print(f"Measured run {repeat}/{repeats}: {measured_count} sequential requests")
        latencies: list[float] = []
        errors = []
        for sample in measured:
            _, latency, error = request_completion(sample["query"])
            if error:
                errors.append({"idx": sample["idx"], "error": error})
            else:
                latencies.append(latency)
        all_latencies.extend(latencies)
        all_errors.extend({"repeat": repeat, **error} for error in errors)
        runs.append({"repeat": repeat, **(summarize(latencies) if latencies else {}), "error_count": len(errors)})

    payload = {
        "started_at_utc": started_at.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "wall_clock_seconds": round(time.perf_counter() - started, 3),
        "latency_definition": "sequential end-to-end HTTP request latency; not TTFT",
        "concurrency": 1,
        "temperature": 0.0,
        "warmup_samples": warmup_count,
        "measured_samples_per_repeat": measured_count,
        "repeats": repeats,
        "selection": "deterministic round-robin across the four official categories",
        "selected_dataset_indices": [sample["idx"] for sample in measured],
        "runs": runs,
        "overall": summarize(all_latencies) if all_latencies else {},
        "latencies_seconds": [round(value, 6) for value in all_latencies],
        "errors": all_errors,
    }
    output_path = ARTIFACTS_DIR / "latency_summary.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["overall"], ensure_ascii=False, indent=2))
    print(f"Latency evidence: {output_path}")
    return 1 if all_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
