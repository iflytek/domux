"""CI must inspect the shipped fixture, not just rebuild a separate valid copy."""
import unittest
from pathlib import Path

from challenge_report import inventory
from generate_challenge import build_challenge
from run_support import load_jsonl


class FixtureSnapshotTests(unittest.TestCase):
    def test_shipped_challenge_matches_generator_and_is_separate_from_shipped_regression(self):
        root = Path(__file__).resolve().parents[1]
        challenge = load_jsonl(root/'data/challenge-v1.jsonl')
        self.assertEqual(build_challenge(), challenge)
        coverage = inventory(load_jsonl(root/'data/seniorsafe.jsonl'), challenge)
        self.assertEqual([], coverage['text_overlap'])
        self.assertEqual([], coverage['base_id_overlap'])


if __name__ == '__main__':
    unittest.main()
