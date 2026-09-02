from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


CASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE_DIR))

from evaluate import (  # noqa: E402
    B0,
    B1,
    B2,
    EvaluationInputError,
    REGISTERED_SNAPSHOT_FILES,
    REGISTERED_SNAPSHOT_MANIFEST_SHA256,
    _load_evidence,
    _read_jsonl,
    _run_b0_trial,
    _validate_rows,
    _validated_model_run,
    analyze_probe,
    canonical_json,
    evaluate_to_directory,
    exact_mcnemar,
    holm_adjust,
    main,
    wilson_interval,
)


DATASET = CASE_DIR / "data" / "scenarios.jsonl"
PROTOCOL = CASE_DIR / "data" / "protocol.json"
FREEZE = CASE_DIR / "data" / "freeze.json"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def minimal_complete_evidence(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for row in rows:
        for variant in ("clear", "ambiguous"):
            command = str(row[f"{variant}_command"])
            fields = dict(row["confirmed_instruction"])
            if variant == "ambiguous":
                inventory = {item["entity_id"]: item for item in row["inventory"]}
                candidates = [inventory[entity_id] for entity_id in row["candidate_entity_ids"]]
                domains = {item["domain"] for item in candidates}
                if len(domains) > 1:
                    fields["device"] = "*"
                    fields["attribute"] = "*"
                if len({item["room"] for item in candidates}) > 1:
                    fields["room"] = "*"
                if len({item["floor"] for item in candidates}) > 1:
                    fields["floor"] = "*"
                if len(candidates) == 1 and fields["action"] == "set":
                    fields["value"] = "*"
            raw_output = "|".join(
                str(fields[slot])
                for slot in ("action", "device", "attribute", "value", "unit", "room", "floor")
            )
            evidence.append({
                "base_id": row["base_id"],
                "variant": variant,
                "command": command,
                "query_sha256": sha256_text(command),
                "status": "ok",
                "raw_output": raw_output,
                "raw_output_sha256": sha256_text(raw_output),
                "latency_ms": 1.0,
            })
    return evidence


def write_frozen_run(
    directory: Path,
    rows: list[dict[str, object]],
    *,
    inference_error: tuple[str, str] | None = None,
) -> tuple[Path, Path]:
    evidence = minimal_complete_evidence(rows)
    if inference_error is not None:
        for index, item in enumerate(evidence):
            if (item["base_id"], item["variant"]) == inference_error:
                evidence[index] = {
                    **item,
                    "status": "error",
                    "raw_output": "",
                    "raw_output_sha256": sha256_text(""),
                    "latency_ms": None,
                    "error_type": "RuntimeError",
                }
                break
        else:
            raise AssertionError("requested synthetic inference-error key was not found")
    evidence_path = directory / "raw.jsonl"
    evidence_path.write_text(
        "".join(canonical_json(item) + "\n" for item in evidence),
        encoding="utf-8",
    )

    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    model = protocol["model"]
    metadata = {
        "model_id": model["id"],
        "tested_revision": model["revision"],
        "precision": model["primary_precision"],
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "raw_evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "sample_count": 96,
        "base_count": 48,
        "warmup_count": model["warmup"]["count"],
        "generation": {
            "do_sample": model["do_sample"],
            "temperature": model["temperature"],
            "max_new_tokens": model["max_new_tokens"],
            "seed": model["seed"],
        },
        "offline_inference": True,
        "snapshot_revision_verified": True,
        "snapshot_manifest_sha256": REGISTERED_SNAPSHOT_MANIFEST_SHA256,
        "snapshot_files": REGISTERED_SNAPSHOT_FILES,
        "runner_sha256": hashlib.sha256((CASE_DIR / "run_model.py").read_bytes()).hexdigest(),
        "grounding_policy_sha256": hashlib.sha256(
            (CASE_DIR / "clarify_commit.py").read_bytes()
        ).hexdigest(),
        "evaluator_sha256": hashlib.sha256((CASE_DIR / "evaluate.py").read_bytes()).hexdigest(),
        "run_mode": "formal",
        "selective_reruns": 0,
        "sample_failures": sum(item["status"] != "ok" for item in evidence),
        "runtime": "synthetic-test-runtime",
        "gpu": "synthetic-test-gpu",
        "visible_gpu_count": 1,
    }
    metadata_path = directory / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    return evidence_path, metadata_path


class StatisticalTests(unittest.TestCase):
    def test_wilson_interval_uses_the_fixed_base_denominator(self) -> None:
        interval = wilson_interval(48, 48)
        self.assertAlmostEqual(interval["lower"], 0.925899870333882)
        self.assertEqual(interval["upper"], 1.0)
        zero = wilson_interval(0, 48)
        self.assertEqual(zero["lower"], 0.0)
        self.assertAlmostEqual(zero["upper"], 0.0741001296661179)

    def test_exact_two_sided_mcnemar_uses_discordant_base_pairs(self) -> None:
        result = exact_mcnemar(B0, B1, [False] * 48, [True] * 48)
        self.assertEqual(result["paired_bases"], 48)
        self.assertEqual(result["b_only_success"], 48)
        self.assertEqual(result["discordant_pairs"], 48)
        self.assertEqual(result["risk_difference_b_minus_a"], 1.0)
        self.assertAlmostEqual(result["exact_two_sided_p"], 2 / (2 ** 48))

    def test_holm_adjustment_is_monotone_in_sorted_p_values(self) -> None:
        comparisons = [
            {"comparison_id": "larger", "exact_two_sided_p": 0.04},
            {"comparison_id": "smaller", "exact_two_sided_p": 0.01},
        ]
        adjusted = {item["comparison_id"]: item for item in holm_adjust(comparisons)}
        self.assertEqual(adjusted["smaller"]["holm_adjusted_p"], 0.02)
        self.assertEqual(adjusted["larger"]["holm_adjusted_p"], 0.04)
        self.assertTrue(adjusted["smaller"]["reject_at_0_05"])
        self.assertTrue(adjusted["larger"]["reject_at_0_05"])


class FrozenEvidenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = _validate_rows(_read_jsonl(DATASET, "dataset"))

    def test_loader_requires_all_48_paired_probes(self) -> None:
        evidence = minimal_complete_evidence(self.rows)
        indexed = _load_evidence(evidence, self.rows)
        self.assertEqual(len(indexed), 96)
        with self.assertRaisesRegex(EvaluationInputError, "incomplete"):
            _load_evidence(evidence[:-1], self.rows)

    def test_loader_rejects_duplicate_or_edited_frozen_evidence(self) -> None:
        evidence = minimal_complete_evidence(self.rows)
        with self.assertRaisesRegex(EvaluationInputError, "duplicate"):
            _load_evidence([*evidence, dict(evidence[0])], self.rows)

        edited = [dict(item) for item in evidence]
        edited[0]["command"] = "post-freeze edit"
        with self.assertRaisesRegex(EvaluationInputError, "differs"):
            _load_evidence(edited, self.rows)

        bad_hash = [dict(item) for item in evidence]
        bad_hash[0]["raw_output_sha256"] = "0" * 64
        with self.assertRaisesRegex(EvaluationInputError, "raw-output hash"):
            _load_evidence(bad_hash, self.rows)

    def test_inference_error_is_valid_evidence_not_an_omitted_base(self) -> None:
        evidence = minimal_complete_evidence(self.rows)
        evidence[0] = {
            **evidence[0],
            "status": "error",
            "raw_output": "",
            "raw_output_sha256": sha256_text(""),
            "latency_ms": None,
            "error_type": "RuntimeError",
        }
        indexed = _load_evidence(evidence, self.rows)
        self.assertEqual(len(indexed), 96)
        self.assertEqual(indexed[(str(self.rows[0]["base_id"]), "clear")]["status"], "error")

    def test_metadata_must_bind_the_exact_raw_evidence_hash(self) -> None:
        protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        model = protocol["model"]
        dataset_sha256 = hashlib.sha256(DATASET.read_bytes()).hexdigest()
        evidence_sha256 = "a" * 64
        metadata = {
            "model_id": model["id"],
            "tested_revision": model["revision"],
            "precision": model["primary_precision"],
            "dataset_sha256": dataset_sha256,
            "raw_evidence_sha256": evidence_sha256,
            "sample_count": 96,
            "base_count": 48,
            "warmup_count": model["warmup"]["count"],
            "generation": {
                "do_sample": model["do_sample"],
                "temperature": model["temperature"],
                "max_new_tokens": model["max_new_tokens"],
                "seed": model["seed"],
            },
            "offline_inference": True,
            "snapshot_revision_verified": True,
            "snapshot_manifest_sha256": REGISTERED_SNAPSHOT_MANIFEST_SHA256,
            "snapshot_files": REGISTERED_SNAPSHOT_FILES,
            "runner_sha256": hashlib.sha256((CASE_DIR / "run_model.py").read_bytes()).hexdigest(),
            "grounding_policy_sha256": hashlib.sha256(
                (CASE_DIR / "clarify_commit.py").read_bytes()
            ).hexdigest(),
            "evaluator_sha256": hashlib.sha256(
                (CASE_DIR / "evaluate.py").read_bytes()
            ).hexdigest(),
            "run_mode": "formal",
            "selective_reruns": 0,
            "sample_failures": 0,
        }
        validated = _validated_model_run(
            metadata,
            protocol,
            dataset_sha256,
            evidence_sha256,
        )
        self.assertTrue(validated["evidence_binding"]["match"])
        self.assertTrue(validated["code_binding"]["match"])
        with self.assertRaisesRegex(EvaluationInputError, "raw_evidence_sha256"):
            _validated_model_run(metadata, protocol, dataset_sha256, "b" * 64)
        tampered = dict(metadata)
        tampered["grounding_policy_sha256"] = "0" * 64
        with self.assertRaisesRegex(EvaluationInputError, "grounding_policy_sha256"):
            _validated_model_run(tampered, protocol, dataset_sha256, evidence_sha256)

    def test_runtime_grounding_detects_every_gold_ambiguity_without_using_its_label(self) -> None:
        indexed = _load_evidence(minimal_complete_evidence(self.rows), self.rows)
        for row in self.rows:
            base_id = str(row["base_id"])
            with self.subTest(base_id=base_id):
                clear = analyze_probe(row, "clear", indexed[(base_id, "clear")])
                ambiguous = analyze_probe(row, "ambiguous", indexed[(base_id, "ambiguous")])
                self.assertTrue(clear.is_unique)
                self.assertTrue(ambiguous.is_clarification)
                self.assertIn(row["expected_target_entity"], ambiguous.candidate_ids)
                self.assertLessEqual(len(ambiguous.candidate_ids), 3)

    def test_unique_but_non_executable_model_output_abstains(self) -> None:
        cases = (
            ("brightness", "150", "Set the {device} in {room} on {floor} to 150 percent brightness."),
            ("mode", "Turbo", "Set the {device} in {room} on {floor} to Turbo mode."),
        )
        for attribute, value, command_template in cases:
            source = next(
                row for row in self.rows
                if row["confirmed_instruction"]["attribute"] == attribute
            )
            row = dict(source)
            fields = dict(row["confirmed_instruction"])
            fields["value"] = value
            row["clear_command"] = command_template.format(
                device=fields["device"],
                room=fields["room"],
                floor=fields["floor"],
            )
            raw_output = "|".join(
                str(fields[slot])
                for slot in ("action", "device", "attribute", "value", "unit", "room", "floor")
            )
            with self.subTest(attribute=attribute):
                outcome = analyze_probe(
                    row,
                    "clear",
                    {"status": "ok", "raw_output": raw_output, "latency_ms": 1.0},
                )
                self.assertEqual(outcome.disposition, "safe_abstain")
                self.assertEqual(outcome.reason, "non_executable_instruction")

    def test_b0_records_a_wrong_unique_dispatch_on_an_ambiguous_base(self) -> None:
        row = next(
            item for item in self.rows
            if item["base_id"] == "eval-duplicate_entity-01"
        )
        # Construct a unique grounding outcome for the wrong candidate, then
        # execute it against the original ambiguous evaluation base.  This
        # isolates B0's no-clarification failure mode without weakening the
        # production source-grounding checks merely to make a model hallucination
        # look unique.
        grounding_row = dict(row)
        grounding_row["ambiguous_command"] = (
            "Turn off the Bedroom light on the Ground Floor."
        )
        raw_output = "turnOff|Light|*|*|*|Bedroom|Ground Floor"
        outcome = analyze_probe(
            grounding_row,
            "ambiguous",
            {"status": "ok", "raw_output": raw_output, "latency_ms": 1.0},
        )
        self.assertTrue(outcome.is_unique)

        trial = _run_b0_trial(row, outcome)

        self.assertEqual(trial["interpretation"], "b0_unclarified_dispatch_on_gold_ambiguous_probe")
        self.assertEqual(trial["sut_calls"], 1)
        self.assertTrue(trial["dispatched"])
        self.assertTrue(trial["wrong_target_transition"])
        self.assertFalse(trial["exact_delta_success"])
        self.assertFalse(trial["oracle_pass"])

    def test_b0_retains_malformed_raw_evidence_when_it_safely_abstains(self) -> None:
        row = self.rows[0]
        malformed = "not-a-seven-slot-domux-record"
        outcome = analyze_probe(
            row,
            "ambiguous",
            {"status": "ok", "raw_output": malformed, "latency_ms": 1.0},
        )
        self.assertEqual(outcome.disposition, "safe_abstain")

        trial = _run_b0_trial(row, outcome)

        self.assertEqual(trial["raw_output"], malformed)
        self.assertTrue(trial["safe_abstention"])
        self.assertTrue(trial["oracle_pass"])


class FailureDenominatorAndCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = _validate_rows(_read_jsonl(DATASET, "dataset"))

    def test_inference_error_stays_in_all_formal_denominators(self) -> None:
        failed_base = str(self.rows[0]["base_id"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence, metadata = write_frozen_run(
                root,
                self.rows,
                inference_error=(failed_base, "ambiguous"),
            )
            report = evaluate_to_directory(
                dataset_path=DATASET,
                protocol_path=PROTOCOL,
                freeze_path=FREEZE,
                evidence_path=evidence,
                metadata_path=metadata,
                output_dir=root / "direct",
            )
            language = report["metrics"]["language"]
            self.assertEqual(language["sensitivity"]["denominator"], 48)
            self.assertEqual(language["sensitivity"]["successes"], 47)
            b2 = report["metrics"]["execution"][B2]
            self.assertEqual(b2["ambiguous_clean_exact_delta_success"]["successes"], 47)
            self.assertEqual(b2["ambiguous_clean_exact_delta_success"]["denominator"], 48)
            for mutation, metric in b2["mutation_oracle_rates"].items():
                expected_denominator = 12 if mutation == "context_state_change" else 48
                self.assertEqual(metric["denominator"], expected_denominator)
            self.assertEqual(b2["universal_guard_rate"]["denominator"], 48)
            self.assertEqual(b2["universal_guard_rate"]["successes"], 47)
            b0 = report["metrics"]["execution"][B0]
            self.assertEqual(b0["safe_abstention_rate"]["denominator"], 48)
            self.assertEqual(b0["safe_abstention_rate"]["successes"], 47)
            self.assertEqual(report["quality_gate"]["result"], "fail")
            self.assertTrue((root / "direct" / "report.json").is_file())
            self.assertTrue((root / "direct" / "trials.jsonl").is_file())

            with redirect_stdout(io.StringIO()):
                return_code = main([
                    "--from-frozen-evidence", str(evidence),
                    "--evidence-metadata", str(metadata),
                    "--dataset", str(DATASET),
                    "--protocol", str(PROTOCOL),
                    "--freeze", str(FREEZE),
                    "--output-dir", str(root / "cli"),
                ])
            self.assertEqual(return_code, 1)
            self.assertTrue((root / "cli" / "report.json").is_file())

    def test_formal_evaluation_refuses_missing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence, _metadata = write_frozen_run(root, self.rows)
            with self.assertRaisesRegex(EvaluationInputError, "requires model-run metadata"):
                evaluate_to_directory(
                    dataset_path=DATASET,
                    protocol_path=PROTOCOL,
                    freeze_path=FREEZE,
                    evidence_path=evidence,
                    metadata_path=None,
                    output_dir=root / "result",
                )


if __name__ == "__main__":
    unittest.main()
