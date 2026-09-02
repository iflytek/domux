#!/usr/bin/env python3

import json
import unittest
from collections import Counter
from pathlib import Path


DATASET = Path(__file__).with_name("example_safety_commands.jsonl")


class DatasetTest(unittest.TestCase):
    def test_schema_ids_and_balance(self) -> None:
        rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
        self.assertEqual(len(rows), 48)
        self.assertEqual(len({row["id"] for row in rows}), 48)
        self.assertEqual(
            Counter(row["expected_decision"] for row in rows),
            Counter({"allow": 16, "confirm": 16, "block": 16}),
        )
        for row in rows:
            self.assertEqual(set(row), {"id", "command", "expected_decision", "rationale"})
            self.assertTrue(row["command"].strip())
            self.assertTrue(row["rationale"].strip())


if __name__ == "__main__":
    unittest.main()
