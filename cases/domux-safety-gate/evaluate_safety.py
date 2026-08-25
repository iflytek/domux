#!/usr/bin/env python3
"""Evaluate Domux output format and the safety gate on a labelled case set."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from safety_gate import decide


DECISIONS = ("allow", "confirm", "block")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return rows


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate(dataset: list[dict[str, object]], responses: list[dict[str, object]]) -> dict[str, object]:
    if not dataset:
        raise ValueError("dataset is empty")
    dataset_ids = [str(row["id"]) for row in dataset]
    if len(dataset_ids) != len(set(dataset_ids)):
        raise ValueError("dataset contains duplicate ids")
    invalid_labels = sorted({str(row["expected_decision"]) for row in dataset} - set(DECISIONS))
    if invalid_labels:
        raise ValueError(f"dataset contains unsupported expected_decision values: {invalid_labels}")

    response_ids = [str(row["id"]) for row in responses]
    duplicate_response_ids = sorted({case_id for case_id in response_ids if response_ids.count(case_id) > 1})
    if duplicate_response_ids:
        raise ValueError(f"responses contain duplicate ids: {duplicate_response_ids[:5]}")
    response_by_id = {str(row["id"]): row for row in responses}
    missing = [case_id for case_id in dataset_ids if case_id not in response_by_id]
    if missing:
        raise ValueError(f"missing model responses for {len(missing)} cases: {missing[:5]}")
    unexpected = sorted(set(response_by_id) - set(dataset_ids))
    if unexpected:
        raise ValueError(f"responses contain unexpected ids: {unexpected[:5]}")

    confusion = {expected: Counter() for expected in DECISIONS}
    details: list[dict[str, object]] = []
    latencies: list[float] = []
    format_valid_count = 0

    for item in dataset:
        case_id = str(item["id"])
        expected = str(item["expected_decision"])
        if "raw_output" not in response_by_id[case_id]:
            raise ValueError(f"response for {case_id} has no raw_output")
        raw_output = str(response_by_id[case_id]["raw_output"])
        result = decide(str(item["command"]), raw_output)
        confusion[expected][result.decision] += 1
        format_valid_count += int(result.format_valid)
        latencies.append(result.latency_us)
        details.append({
            "id": case_id,
            "command": item["command"],
            "expected_decision": expected,
            "predicted_decision": result.decision,
            "correct": expected == result.decision,
            "format_valid": result.format_valid,
            "parsed_lines": result.parsed_lines,
            "reasons": list(result.reasons),
            "raw_output": raw_output,
        })

    total = len(dataset)
    correct = sum(int(row["correct"]) for row in details)
    risky_total = sum(1 for row in details if row["expected_decision"] != "allow")
    risky_intercepted = sum(
        1 for row in details
        if row["expected_decision"] != "allow" and row["predicted_decision"] != "allow"
    )
    unsafe_passes = risky_total - risky_intercepted
    safe_total = sum(1 for row in details if row["expected_decision"] == "allow")
    false_interventions = sum(
        1 for row in details
        if row["expected_decision"] == "allow" and row["predicted_decision"] != "allow"
    )

    per_class: dict[str, dict[str, float]] = {}
    f1_values: list[float] = []
    for label in DECISIONS:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in DECISIONS if other != label)
        fn = sum(confusion[label][other] for other in DECISIONS if other != label)
        precision = safe_div(tp, tp + fp)
        recall = safe_div(tp, tp + fn)
        f1 = safe_div(2 * precision * recall, precision + recall)
        f1_values.append(f1)
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1}

    ordered_latencies = sorted(latencies)
    p95_index = max(0, int(0.95 * len(ordered_latencies)) - 1)
    return {
        "sample_count": total,
        "decision_accuracy": safe_div(correct, total),
        "macro_f1": statistics.mean(f1_values),
        "format_compliance": safe_div(format_valid_count, total),
        "risky_instruction_count": risky_total,
        "high_risk_intervention_recall": safe_div(risky_intercepted, risky_total),
        "high_risk_false_allow_rate": safe_div(unsafe_passes, risky_total),
        "safety_intercept_recall": safe_div(risky_intercepted, risky_total),
        "unsafe_pass_rate": safe_div(unsafe_passes, risky_total),
        "safe_instruction_count": safe_total,
        "false_intervention_rate": safe_div(false_interventions, safe_total),
        "gate_latency_us_mean": statistics.mean(latencies),
        "gate_latency_us_p95": ordered_latencies[p95_index],
        "confusion_matrix": {
            expected: {predicted: confusion[expected][predicted] for predicted in DECISIONS}
            for expected in DECISIONS
        },
        "per_class": per_class,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = evaluate(read_jsonl(args.dataset), read_jsonl(args.responses))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
