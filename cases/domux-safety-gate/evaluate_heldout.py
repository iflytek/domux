#!/usr/bin/env python3
"""Evaluate the frozen v2 gate exactly once on the independently generated held-out set."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from domux_parser import parse_domux_output_v2
from run_v2_experiments import _v2, classification_metrics, read_jsonl, sha256


FROZEN_GATE_COMMIT = "ad243f999d75bce3f1be35667ff3eaa734ef70e5"
CATEGORIES = (
    "clean", "high_consequence", "paraphrase", "multilingual",
    "ambiguous", "multi_device", "output_mismatch",
)


def validate_cases(rows: list[dict[str, object]]) -> dict[str, object]:
    required = {
        "id", "category", "language", "command", "raw_output",
        "expected_decision", "rationale",
    }
    if len(rows) != 84:
        raise ValueError(f"held-out set must contain exactly 84 rows, found {len(rows)}")
    expected_ids = [f"heldout-{index:03d}" for index in range(1, 85)]
    actual_ids = [str(row["id"]) for row in rows]
    if actual_ids != expected_ids:
        raise ValueError("held-out ids must be unique, ordered, and continuous heldout-001..084")
    for row in rows:
        missing = required - set(row)
        if missing:
            raise ValueError(f"{row.get('id')} missing fields: {sorted(missing)}")
        if row["expected_decision"] not in {"allow", "confirm", "block"}:
            raise ValueError(f"{row['id']} has invalid expected_decision")
        if not str(row["command"]).strip() or not str(row["rationale"]).strip():
            raise ValueError(f"{row['id']} has empty command or rationale")
    category_counts = Counter(str(row["category"]) for row in rows)
    expected_category_counts = {category: 12 for category in CATEGORIES}
    if dict(sorted(category_counts.items())) != dict(sorted(expected_category_counts.items())):
        raise ValueError(f"held-out category counts invalid: {dict(category_counts)}")
    language_counts = Counter(str(row["language"]) for row in rows)
    non_english = sum(count for language, count in language_counts.items() if language not in {"en", "english"})
    multiline = sum("\n" in str(row["raw_output"]) for row in rows)
    malformed_or_unknown = 0
    for row in rows:
        parsed = parse_domux_output_v2(str(row["raw_output"]))
        if not parsed.structural_valid or not parsed.action_recognized:
            malformed_or_unknown += 1
    if non_english < 18:
        raise ValueError(f"held-out set requires >=18 non-English/mixed rows, found {non_english}")
    if multiline < 10:
        raise ValueError(f"held-out set requires >=10 multiline rows, found {multiline}")
    if malformed_or_unknown < 6:
        raise ValueError(
            f"held-out set requires >=6 malformed/empty/unknown-action rows, found {malformed_or_unknown}"
        )
    return {
        "sample_count": len(rows),
        "category_counts": dict(sorted(category_counts.items())),
        "language_counts": dict(sorted(language_counts.items())),
        "non_english_or_mixed_count": non_english,
        "multiline_count": multiline,
        "malformed_empty_or_unknown_action_count": malformed_or_unknown,
    }


def evaluate(rows: list[dict[str, object]]) -> dict[str, object]:
    validation = validate_cases(rows)
    details: list[dict[str, object]] = []
    for row in rows:
        result = _v2(str(row["command"]), str(row["raw_output"]))
        details.append({
            **row,
            "predicted_decision": result["decision"],
            "correct": row["expected_decision"] == result["decision"],
            **{key: value for key, value in result.items() if key != "decision"},
        })
    category_metrics = {
        category: classification_metrics([row for row in details if row["category"] == category])
        for category in CATEGORIES
    }
    failures = [
        {
            "id": row["id"],
            "category": row["category"],
            "language": row["language"],
            "command": row["command"],
            "raw_output": row["raw_output"],
            "expected_decision": row["expected_decision"],
            "predicted_decision": row["predicted_decision"],
            "reasons": row["reasons"],
        }
        for row in details if not row["correct"]
    ]
    return {
        "gate_frozen_commit": FROZEN_GATE_COMMIT,
        "set_role": "Independent synthetic gate held-out; not a Domux model-accuracy dataset.",
        "validation": validation,
        "aggregate": classification_metrics(details),
        "by_category": category_metrics,
        "failure_count": len(failures),
        "failures": failures,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path("evidence/v2/heldout_cases.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("evidence/v2/heldout_results.json"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    rows = read_jsonl(args.cases)
    report = {"heldout_cases_sha256": sha256(args.cases), **evaluate(rows)}
    if args.verify_only:
        stored = json.loads(args.output.read_text(encoding="utf-8"))
        canonical_report = json.loads(json.dumps(report, ensure_ascii=False))
        if stored != canonical_report:
            raise SystemExit("held-out evidence mismatch")
        print(json.dumps({"status": "ok", "sample_count": len(rows)}, indent=2))
        return 0
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite one-shot held-out result: {args.output}")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "heldout_cases_sha256": report["heldout_cases_sha256"],
        "gate_frozen_commit": FROZEN_GATE_COMMIT,
        "aggregate": report["aggregate"],
        "failure_count": report["failure_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
