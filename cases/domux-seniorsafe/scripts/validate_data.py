#!/usr/bin/env python3
"""Validate SeniorSafe JSONL schema, pairs, and Domux seven-field gold output."""

from __future__ import annotations

import argparse
import json
import hashlib
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


def load_spec(path: Path | None) -> dict:
    if path is None:
        return {"records": 80, "pairs": 40, "group_counts": {"clean": 40, **{g: 5 for g in GROUPS - {"clean"}}},
                "id_pattern": ID_RE.pattern, "sources": sorted(SOURCES),
                "unique_text": False, "allow_unscored_empty_gold": False}
    spec = json.loads(path.read_text(encoding="utf-8"))
    required = {"records", "pairs", "group_counts", "id_pattern", "sources", "unique_text", "allow_unscored_empty_gold"}
    if not isinstance(spec, dict) or set(spec) != required:
        raise ValueError("invalid dataset specification fields")
    if any(type(spec[k]) is not int or spec[k] <= 0 for k in ("records", "pairs")):
        raise ValueError("spec counts must be positive integers")
    counts = spec["group_counts"]
    if (not isinstance(counts, dict) or "clean" not in counts
            or any(not isinstance(g, str) or not g or type(n) is not int or n <= 0 for g, n in counts.items())
            or sum(counts.values()) != spec["records"] or counts["clean"] != spec["pairs"]
            or spec["records"] != 2 * spec["pairs"]):
        raise ValueError("inconsistent spec group counts")
    if (not isinstance(spec["sources"], list) or not spec["sources"]
            or any(not isinstance(s, str) or not s for s in spec["sources"])
            or any(type(spec[k]) is not bool for k in ("unique_text", "allow_unscored_empty_gold"))
            or not isinstance(spec["id_pattern"], str)):
        raise ValueError("invalid spec options")
    re.compile(spec["id_pattern"])
    return spec


def verify_freeze(path: Path, root: Path, required: list[Path] | None = None) -> str:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("freeze manifest must list files")
    resolved = set()
    root = root.resolve()
    for name, digest in files.items():
        target = (root / name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError(f"invalid frozen path: {name}")
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"frozen file changed: {name}")
        resolved.add(target)
    if any(p.resolve() not in resolved for p in required or []):
        raise ValueError("freeze manifest omits required input or inference file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_contract(data: Path, spec_path: Path | None, freeze: Path | None) -> tuple[dict, dict]:
    spec = load_spec(spec_path)
    settings = {}
    if spec_path:
        settings["data_spec_sha256"] = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    if freeze:
        root = Path(__file__).resolve().parents[1]
        required = [data, *[root / "scripts" / name for name in
                    ("normalize.py", "protocol.py", "run_support.py", "run_eval.py", "run_transformers_cpu.py", "validate_data.py", "score.py")]]
        if spec_path:
            required.append(spec_path)
        settings["freeze_sha256"] = verify_freeze(freeze, root, required)
    return spec, settings


def validate_run_contract(freeze: Path | None, settings: dict, revision: str) -> None:
    if freeze is None:
        return
    planned = json.loads(freeze.read_text(encoding='utf-8')).get('run')
    if not isinstance(planned, dict) or planned.get('revision') != revision:
        raise ValueError('run revision differs from freeze')
    expected = planned.get('settings')
    if not isinstance(expected, dict) or not expected or any(settings.get(k) != v for k, v in expected.items()):
        raise ValueError('run settings differ from freeze')


def validate(rows: list[dict[str, object]], spec: dict | None = None) -> list[str]:
    spec = load_spec(None) if spec is None else spec
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
        empty_gold_allowed = spec['allow_unscored_empty_gold'] and row['evaluate_parse'] is False and row['expected_decision'] != 'execute'
        text_keys = REQUIRED - {'evaluate_parse'} - ({'gold'} if empty_gold_allowed else set())
        if any(not isinstance(row[key], str) or not row[key].strip() for key in text_keys) or not isinstance(row['gold'], str):
            errors.append(f'{prefix}: text fields must be non-empty strings')
            continue
        row_id = str(row["id"])
        if row_id in ids:
            errors.append(f"{prefix}: duplicate id {row_id}")
        ids.add(row_id)
        if not re.fullmatch(spec['id_pattern'], row_id):
            errors.append(f"{prefix}: invalid id {row_id}")
        if row["group"] not in spec['group_counts']:
            errors.append(f"{prefix}: invalid group {row['group']}")
        if row["language"] not in LANGUAGES:
            errors.append(f"{prefix}: invalid language {row['language']}")
        if row["risk"] not in RISKS:
            errors.append(f"{prefix}: invalid risk {row['risk']}")
        if row["expected_decision"] not in DECISIONS:
            errors.append(f"{prefix}: invalid decision {row['expected_decision']}")
        if row["source"] not in spec['sources']:
            errors.append(f"{prefix}: invalid source {row['source']}")
        if not isinstance(row["evaluate_parse"], bool):
            errors.append(f"{prefix}: evaluate_parse must be boolean")
        if not str(row["text"]).strip():
            errors.append(f"{prefix}: text is empty")
        if not (empty_gold_allowed and row['gold'] == '') and not parse_output(row['gold'])[1]:
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
        scored_pair = [row for row in pair if row['evaluate_parse']] if spec['allow_unscored_empty_gold'] else pair
        if len({str(row["gold"]) for row in scored_pair}) > 1:
            errors.append(f"{base_id}: clean and noisy gold differ")

    if len(rows) != spec['records']:
        errors.append(f"dataset: expected {spec['records']} records, found {len(rows)}")
    if len(pairs) != spec['pairs']:
        errors.append(f"dataset: expected {spec['pairs']} pairs, found {len(pairs)}")
    counts = Counter(str(row.get('group')) for row in rows if isinstance(row, dict))
    for group, expected in spec['group_counts'].items():
        if counts[group] != expected:
            errors.append(f"dataset: expected {expected} {group} records, found {counts[group]}")
    if spec['unique_text']:
        texts = [' '.join(str(row.get('text', '')).casefold().split()) for row in rows if isinstance(row, dict)]
        if len(texts) != len(set(texts)):
            errors.append('dataset: duplicate text')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--freeze', type=Path)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "seniorsafe.jsonl",
    )
    args = parser.parse_args()
    rows = load_jsonl(args.path)
    spec, _ = input_contract(args.path, args.spec, args.freeze)
    errors = validate(rows, spec)
    if errors:
        for error in errors:
            print(f"[seniorsafe] ERROR: {error}")
        return 1
    print(f"[seniorsafe] valid: {len(rows)} records, {len(rows) // 2} pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
