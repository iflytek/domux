#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_failures import build_analysis, classify_failure  # noqa: E402
from common import (  # noqa: E402
    format_valid,
    parse_instructions,
    percentile_nearest_rank,
    redact_text,
    stratified_samples,
)
import render_case  # noqa: E402
import render_preview  # noqa: E402
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
            validate_artifacts(download, smoke, run, summary, latency, "")


class RenderProvenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.artifacts = {
            name: json.loads((render_case.ARTIFACTS_DIR / name).read_text(encoding="utf-8"))
            for name in (
                "download_metadata.json", "smoke_results.json", "eval_run_metadata.json",
                "eval_summary.json", "failure_analysis.json", "latency_summary.json",
            )
        }
        self.environment = (render_case.ARTIFACTS_DIR / "environment.txt").read_text(encoding="utf-8")

    def validate(self) -> None:
        validate_artifacts(
            *(self.artifacts[name] for name in (
                "download_metadata.json", "smoke_results.json", "eval_run_metadata.json",
                "eval_summary.json", "latency_summary.json",
            )),
            self.environment,
        )

    def upgrade_metadata(self) -> None:
        self.artifacts["download_metadata.json"].update(
            runtime="vllm-0.31.0", transformers="5.10.4", created_at_utc="2026-10-08T03:09:29+00:00",
        )

    def upgrade_environment(self) -> None:
        self.environment = self.environment.replace("vllm=0.22.0", "vllm=0.31.0").replace(
            "transformers=5.5.1", "transformers=5.10.4",
        ).replace("2026-08-25", "2026-10-08")

    def assert_renderers_reject(self, message: str) -> None:
        for renderer in (render_case, render_preview):
            with self.subTest(renderer=renderer.__name__), \
                 patch.object(renderer, "load_json", side_effect=self.artifacts.__getitem__), \
                 patch.object(Path, "read_text", return_value=self.environment), \
                 patch.object(sys, "argv", ["render_case.py"]), \
                 patch.object(render_case, "write_text_lf") as write:
                with self.assertRaisesRegex(ValueError, message):
                    renderer.main()
                write.assert_not_called()

    def test_accepts_committed_historical_artifacts(self) -> None:
        self.validate()

    def test_accepts_consistent_new_run(self) -> None:
        self.upgrade_metadata()
        self.upgrade_environment()
        for name in ("smoke_results.json", "eval_run_metadata.json", "latency_summary.json"):
            for key in ("created_at_utc", "started_at_utc", "finished_at_utc"):
                if key in self.artifacts[name]:
                    original = datetime.fromisoformat(self.artifacts[name][key])
                    self.artifacts[name][key] = (original + timedelta(days=44)).isoformat()
        self.validate()

    def test_new_download_metadata_cannot_relabel_historical_results(self) -> None:
        self.upgrade_metadata()
        self.assert_renderers_reject("runtime does not match environment")

    def test_new_environment_cannot_relabel_historical_results(self) -> None:
        self.upgrade_metadata()
        self.upgrade_environment()
        self.assert_renderers_reject("smoke result predates")

    def test_partial_pipeline_cannot_reuse_old_evaluation(self) -> None:
        self.upgrade_metadata()
        self.upgrade_environment()
        self.artifacts["smoke_results.json"]["created_at_utc"] = "2026-10-08T03:18:34+00:00"
        self.assert_renderers_reject("evaluation start predates")

    def test_partial_pipeline_cannot_reuse_old_latency(self) -> None:
        self.upgrade_metadata()
        self.upgrade_environment()
        self.artifacts["smoke_results.json"]["created_at_utc"] = "2026-10-08T03:18:34+00:00"
        self.artifacts["eval_run_metadata.json"].update(
            started_at_utc="2026-10-08T03:18:35+00:00", finished_at_utc="2026-10-08T03:19:31+00:00",
        )
        self.assert_renderers_reject("latency start predates")

    def test_rejects_missing_or_timezone_free_timestamps(self) -> None:
        for value in (None, "2026-08-25T03:18:34"):
            with self.subTest(timestamp=value):
                self.artifacts["smoke_results.json"]["created_at_utc"] = value
                with self.assertRaises(ValueError):
                    self.validate()

    def test_rejects_mismatched_transformers_or_cuda_versions(self) -> None:
        for field in ("transformers", "cuda_compiler"):
            with self.subTest(field=field):
                original = self.artifacts["download_metadata.json"][field]
                self.artifacts["download_metadata.json"][field] = "0.0.0"
                with self.assertRaisesRegex(ValueError, f"{field} does not match environment"):
                    self.validate()
                self.artifacts["download_metadata.json"][field] = original


if __name__ == "__main__":
    unittest.main()
