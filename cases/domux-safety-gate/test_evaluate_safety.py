#!/usr/bin/env python3

import json
import unittest
from pathlib import Path

from evaluate_safety import evaluate


DATASET = Path(__file__).with_name("example_safety_commands.jsonl")
VALID_OUTPUT = "turnOn|Light|*|*|*|Living Room|*"


class EvaluationTest(unittest.TestCase):
    def test_policy_oracle_matches_hand_labels_with_valid_fixture_outputs(self) -> None:
        dataset = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
        responses = [{"id": row["id"], "raw_output": VALID_OUTPUT} for row in dataset]
        report = evaluate(dataset, responses)
        self.assertEqual(report["sample_count"], 48)
        self.assertEqual(report["decision_accuracy"], 1.0)
        self.assertEqual(report["high_risk_intervention_recall"], 1.0)
        self.assertEqual(report["high_risk_false_allow_rate"], 0.0)
        self.assertEqual(report["safety_intercept_recall"], 1.0)
        self.assertEqual(report["unsafe_pass_rate"], 0.0)
        self.assertEqual(report["false_intervention_rate"], 0.0)

    def test_rejects_duplicate_missing_and_unexpected_response_ids(self) -> None:
        dataset = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
        responses = [{"id": row["id"], "raw_output": VALID_OUTPUT} for row in dataset]
        responses.append({"id": dataset[0]["id"], "raw_output": VALID_OUTPUT})
        with self.assertRaisesRegex(ValueError, "duplicate ids"):
            evaluate(dataset, responses)

        responses = [{"id": row["id"], "raw_output": VALID_OUTPUT} for row in dataset[1:]]
        with self.assertRaisesRegex(ValueError, "missing model responses"):
            evaluate(dataset, responses)

        responses = [{"id": row["id"], "raw_output": VALID_OUTPUT} for row in dataset]
        responses.append({"id": "not-in-dataset", "raw_output": VALID_OUTPUT})
        with self.assertRaisesRegex(ValueError, "unexpected ids"):
            evaluate(dataset, responses)


if __name__ == "__main__":
    unittest.main()
