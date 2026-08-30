#!/usr/bin/env python3
"""Validate SeniorSafe JSONL schema, pairs, and Domux seven-field gold output."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from protocol import parse_output


GROUPS = {
    "clean",
    "elderly_style",
    "self_correction",
    "repetition",
    "negation",
    "ambiguous_reference",
    "asr_error",
    "code_switching",
    "high_risk_ambiguity",
}
LANGUAGES = {"zh-CN", "en", "zh-en-mixed"}
RISKS = {"low", "medium", "high"}
DECISIONS = {"execute", "clarify", "reject"}
SOURCES = {"human-authored-synthetic", "rule-generated-synthetic"}
REQUIRED = {
    "id",
    "base_id",
    "group",
    "language",
    "text",
    "gold",
    "risk",
    "expected_decision",
    "evaluate_parse",
    "source",
    "notes",
}
ID_RE = re.compile(r"^ss-\d{3}-(?:clean|[a-z_]+)$")


def load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"line {line_no}: record must be an object")
        rows.append(row)
    return rows


def validate(rows: list[dict[str, object]]) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    pairs: dict[str, list[dict[str, object]]] = defaultdict(list)

    for index, row in enumerate(rows, start=1):
        prefix = f"record {index}"
        if not isinstance(row, dict):
            errors.append(f'{prefix}: record must be an object')
            continue
        missing = REQUIRED - row.keys()
        if row.keys() - REQUIRED:
            errors.append(f'{prefix}: unexpected fields {sorted(row.keys() - REQUIRED)}')
        if missing:
            errors.append(f"{prefix}: missing fields {sorted(missing)}")
            continue
        if any(not isinstance(row[key], str) or not row[key].strip() for key in REQUIRED - {'evaluate_parse'}):
            errors.append(f'{prefix}: text fields must be non-empty strings')
            continue
        row_id = str(row["id"])
        if row_id in ids:
            errors.append(f"{prefix}: duplicate id {row_id}")
        ids.add(row_id)
        if not ID_RE.fullmatch(row_id):
            errors.append(f"{prefix}: invalid id {row_id}")
        if row["group"] not in GROUPS:
            errors.append(f"{prefix}: invalid group {row['group']}")
        if row["language"] not in LANGUAGES:
            errors.append(f"{prefix}: invalid language {row['language']}")
        if row["risk"] not in RISKS:
            errors.append(f"{prefix}: invalid risk {row['risk']}")
        if row["expected_decision"] not in DECISIONS:
            errors.append(f"{prefix}: invalid decision {row['expected_decision']}")
        if row["source"] not in SOURCES:
            errors.append(f"{prefix}: invalid source {row['source']}")
        if not isinstance(row["evaluate_parse"], bool):
            errors.append(f"{prefix}: evaluate_parse must be boolean")
        if not str(row["text"]).strip():
            errors.append(f"{prefix}: text is empty")
        if not parse_output(row['gold'])[1]:
            errors.append(f'{prefix}: gold must contain valid non-empty seven-field commands')
        if row_id != f"{row['base_id']}-{row['group']}":
            errors.append(f'{prefix}: id must match base_id and group')
        pairs[str(row["base_id"])].append(row)

    for base_id, pair in sorted(pairs.items()):
        if len(pair) != 2:
            errors.append(f"{base_id}: expected two records, found {len(pair)}")
            continue
        groups = {str(row["group"]) for row in pair}
        if "clean" not in groups or len(groups) != 2:
            errors.append(f"{base_id}: pair must contain clean and one noisy group")
        if len({str(row["gold"]) for row in pair}) != 1:
            errors.append(f"{base_id}: clean and noisy gold differ")

    if len(rows) != 80:
        errors.append(f"dataset: expected 80 records, found {len(rows)}")
    if len(pairs) != 40:
        errors.append(f"dataset: expected 40 pairs, found {len(pairs)}")
    noisy_counts = Counter(str(row["group"]) for row in rows if row["group"] != "clean")
    for group in GROUPS - {"clean"}:
        if noisy_counts[group] != 5:
            errors.append(f"dataset: expected 5 {group} records, found {noisy_counts[group]}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "seniorsafe.jsonl",
    )
    args = parser.parse_args()
    rows = load_jsonl(args.path)
    errors = validate(rows)
    if errors:
        for error in errors:
            print(f"[seniorsafe] ERROR: {error}")
        return 1
    print(f"[seniorsafe] valid: {len(rows)} records, {len(rows) // 2} pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
