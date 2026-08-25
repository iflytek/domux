#!/usr/bin/env python3
"""Fail-closed verifier for frozen v1 evidence, v2 experiments, and held-out results."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from evaluate_heldout import FROZEN_GATE_COMMIT, evaluate as evaluate_heldout
from run_v2_experiments import read_jsonl, sha256


ORIGINAL_HASHES = {
    "evidence/domux_raw.jsonl": "a2bc81052d5422e9fb1419ab94060f8b77d49f5b6d1276e69342ef7df077b454",
    "evidence/domux_raw.metadata.json": "653530f7becb0e7b2fe887d1eed29dcfbef4a35755c739f0be475fb230d8c0df",
    "evidence/safety_report.json": "f0cf4be347c4ce007e7ca32be3b8022536d144aa4aac7cbfebb8da5c8ce221a9",
}
EXPERIMENT_FILES = (
    "parser_metrics.json",
    "parser_ablation.json",
    "real_output_cross_pair_attack.json",
    "synthetic_fault_injection.json",
    "gate_v2_report.json",
)


def normalized(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: normalized(item)
            for key, item in value.items()
            if key != "code_commit_at_run"
        }
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def current_gate_matches_frozen_commit() -> str:
    repo_root = Path(subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True,
    ).stdout.strip())
    relative = Path.cwd().resolve().relative_to(repo_root) / "safety_gate.py"
    frozen = subprocess.run(
        ["git", "show", f"{FROZEN_GATE_COMMIT}:{relative.as_posix()}"],
        check=True,
        capture_output=True,
    ).stdout
    current = Path("safety_gate.py").read_bytes()
    if current != frozen:
        raise SystemExit("current safety_gate.py differs from the pre-held-out frozen gate")
    return hashlib.sha256(current).hexdigest()


def main() -> int:
    for relative, expected in ORIGINAL_HASHES.items():
        actual = sha256(Path(relative))
        if actual != expected:
            raise SystemExit(f"frozen original evidence hash mismatch: {relative}")
    gate_sha256 = current_gate_matches_frozen_commit()

    with tempfile.TemporaryDirectory(prefix="domux-v2-verify-") as temporary:
        output_dir = Path(temporary)
        subprocess.run(
            [sys.executable, "run_v2_experiments.py", "--output-dir", str(output_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        for filename in EXPERIMENT_FILES:
            stored = json.loads((Path("evidence/v2") / filename).read_text(encoding="utf-8"))
            recomputed = json.loads((output_dir / filename).read_text(encoding="utf-8"))
            if normalized(stored) != normalized(recomputed):
                raise SystemExit(f"v2 experiment evidence mismatch: {filename}")

    heldout_path = Path("evidence/v2/heldout_cases.jsonl")
    heldout_result_path = Path("evidence/v2/heldout_results.json")
    stored_heldout = json.loads(heldout_result_path.read_text(encoding="utf-8"))
    recomputed_heldout = {
        "heldout_cases_sha256": sha256(heldout_path),
        **evaluate_heldout(read_jsonl(heldout_path)),
    }
    canonical_heldout = json.loads(json.dumps(recomputed_heldout, ensure_ascii=False))
    if stored_heldout != canonical_heldout:
        raise SystemExit("held-out evidence mismatch")

    print(json.dumps({
        "status": "ok",
        "frozen_gate_commit": FROZEN_GATE_COMMIT,
        "gate_sha256": gate_sha256,
        "original_evidence_files_verified": len(ORIGINAL_HASHES),
        "v2_experiment_files_recomputed": len(EXPERIMENT_FILES),
        "heldout_sample_count": recomputed_heldout["validation"]["sample_count"],
        "heldout_cases_sha256": recomputed_heldout["heldout_cases_sha256"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
