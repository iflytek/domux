#!/usr/bin/env python3

import json
import unittest
from pathlib import Path

from evaluate_safety import evaluate


DATASET = Path(__file__).with_name("example_safety_commands.jsonl")
VALID_OUTPUT = "turnOn|Light|*|*|*|Living Room|*"


class EvaluationTest(unittest.TestCase):
    def test_policy_matches_hand_labels_with_valid_fixture_outputs(self) -> None:
        dataset = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
        responses = [{"id": row["id"], "raw_output": VALID_OUTPUT} for row in dataset]
        report = evaluate(dataset, responses)
        self.assertEqual(report["sample_count"], 48)
        self.assertEqual(report["decision_accuracy"], 1.0)
        self.assertEqual(report["safety_intercept_recall"], 1.0)
        self.assertEqual(report["unsafe_pass_rate"], 0.0)
        self.assertEqual(report["false_intervention_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
