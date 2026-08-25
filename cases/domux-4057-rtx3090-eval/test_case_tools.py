#!/usr/bin/env python3

from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_failures import build_analysis, classify_failure  # noqa: E402
from common import (  # noqa: E402
    format_valid,
    parse_instructions,
    percentile_nearest_rank,
    redact_text,
    stratified_samples,
)
from render_case import validate_artifacts  # noqa: E402


class CommonHelpersTest(unittest.TestCase):
    def test_parse_and_format(self) -> None:
        output = "turnOn|Light|*|*|*|Living Room|*\nset|AC|temperature|22|Celsius|Bedroom|*"
        self.assertTrue(format_valid(output))
        self.assertEqual(len(parse_instructions(output)), 2)
        self.assertFalse(format_valid("not structured"))

    def test_nearest_rank_percentile(self) -> None:
        self.assertEqual(percentile_nearest_rank(range(1, 101), 95), 95.0)

    def test_stratified_selection(self) -> None:
        samples = [
            {"idx": index, "category": category, "query": str(index)}
            for category in ("a", "b", "c", "d")
            for index in range(10)
        ]
        selected = stratified_samples(samples, 12)
        self.assertEqual(Counter(item["category"] for item in selected), Counter({"a": 3, "b": 3, "c": 3, "d": 3}))

    def test_redaction(self) -> None:
        fake_hf_token = "hf_" + "1234567890"
        fake_api_key = "sk-" + "abcdefghij"
        source = (
            f"Authorization: Bearer secret-token {fake_hf_token} {fake_api_key} "
            "/home/alice/.cache C:\\Users\\alice\\.cache 10.1.2.3 127.0.0.1"
        )
        redacted = redact_text(source)
        self.assertNotIn("secret-token", redacted)
        self.assertNotIn(fake_hf_token, redacted)
        self.assertNotIn(fake_api_key, redacted)
        self.assertNotIn("alice", redacted)
        self.assertNotIn("10.1.2.3", redacted)
        self.assertIn("127.0.0.1", redacted)


class FailureAnalysisTest(unittest.TestCase):
    def test_field_and_multi_intent_clusters(self) -> None:
        item = {
            "idx": 1,
            "category": "multi_intent",
            "query": "example",
            "model_output": "turnOff|Light|*|*|*|Bedroom|*",
            "gold": "turnOn|Light|*|*|*|Living Room|*\nset|AC|temperature|22|Celsius|Bedroom|*",
            "format_valid": True,
            "result_correct": False,
            "error": None,
        }
        clusters = classify_failure(item)
        self.assertIn("missing_intent", clusters)
        self.assertIn("action_mismatch", clusters)
        self.assertIn("room_mismatch", clusters)

    def test_analysis_counts_overlapping_clusters(self) -> None:
        results = [
            {
                "idx": 1,
                "category": "omitted_attribute",
                "query": "example",
                "model_output": "bad output",
                "gold": "turnOn|Light|*|*|*|*|*",
                "format_valid": False,
                "result_correct": False,
                "error": None,
            },
            {
                "idx": 2,
                "category": "single_intent",
                "query": "example 2",
                "model_output": "turnOn|Light|*|*|*|*|*",
                "gold": "turnOn|Light|*|*|*|*|*",
                "format_valid": True,
                "result_correct": True,
                "error": None,
            },
        ]
        analysis = build_analysis(results)
        self.assertEqual(analysis["total_samples"], 2)
        self.assertEqual(analysis["failed_samples"], 1)
        names = {cluster["name"] for cluster in analysis["clusters"]}
        self.assertIn("format_invalid", names)
        self.assertIn("omitted_attribute_failure", names)
        counts = {cluster["name"]: cluster["count"] for cluster in analysis["clusters"]}
        self.assertEqual(counts["api_error"], 0)
        self.assertEqual(counts["extra_intent"], 0)


class RenderGateTest(unittest.TestCase):
    def test_rejects_incomplete_real_run(self) -> None:
        download = {"revision": "a" * 40}
        smoke = {"all_passed": True}
        run = {"completed_samples": 4056, "api_error_count": 0}
        summary = {"overall": {"total": 4057}}
        latency = {"errors": [], "overall": {"count": 300}}
        with self.assertRaisesRegex(ValueError, "4,057"):
            validate_artifacts(download, smoke, run, summary, latency)

    def test_accepts_complete_artifact_gate(self) -> None:
        validate_artifacts(
            {"revision": "a" * 40},
            {"all_passed": True},
            {"completed_samples": 4057, "api_error_count": 0},
            {"overall": {"total": 4057}},
            {"errors": [], "overall": {"count": 300}},
        )


if __name__ == "__main__":
    unittest.main()
