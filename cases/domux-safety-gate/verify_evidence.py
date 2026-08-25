#!/usr/bin/env python3
"""Verify that public raw outputs, report, and dataset form one evidence chain."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluate_safety import evaluate, read_jsonl


VARIABLE_FIELDS = {"gate_latency_us_mean", "gate_latency_us_p95"}


def stable_report(report: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in report.items() if key not in VARIABLE_FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    stored = json.loads(args.report.read_text(encoding="utf-8"))
    recomputed = evaluate(read_jsonl(args.dataset), read_jsonl(args.responses))
    if stable_report(stored) != stable_report(recomputed):
        raise SystemExit(
            "evidence mismatch: stored report disagrees with raw outputs or dataset "
            "outside of runtime-variable gate latency"
        )
    print(json.dumps({
        "status": "ok",
        "sample_count": recomputed["sample_count"],
        "decision_accuracy": recomputed["decision_accuracy"],
        "macro_f1": recomputed["macro_f1"],
        "format_compliance": recomputed["format_compliance"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
