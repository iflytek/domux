from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path


CASE_DIR = Path(__file__).resolve().parents[1]
DATASET = CASE_DIR / "data" / "scenarios.jsonl"
FREEZE = CASE_DIR / "data" / "freeze.json"
PROTOCOL = CASE_DIR / "data" / "protocol.json"
SNAPSHOT_MANIFEST = CASE_DIR / "data" / "snapshot_manifest.json"
FIELDS = {"action", "device", "attribute", "value", "unit", "room", "floor"}
sys.path.insert(0, str(CASE_DIR))

from clarify_commit import (  # noqa: E402
    DomuxInstruction,
    EntityRegistry,
    EntitySpec,
    SessionContext,
    build_plan,
    controlled_projection,
    ground_domux_request,
    projection_matches,
    resolve_clarification_submission,
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(row: object) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw_lines = [line for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
        cls.rows = [json.loads(line) for line in cls.raw_lines]
        cls.freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        cls.protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))

    def test_file_and_protocol_hashes_match_freeze(self) -> None:
        self.assertEqual(sha256(DATASET.read_bytes()), self.freeze["full_sha256"])
        self.assertEqual(sha256(PROTOCOL.read_bytes()), self.freeze["protocol_sha256"])
        self.assertEqual(
            sha256(SNAPSHOT_MANIFEST.read_bytes()), self.freeze["snapshot_manifest_sha256"]
        )
        self.assertTrue(all(line == canonical(row) for line, row in zip(self.raw_lines, self.rows)))

    def test_split_counts_and_unique_base_ids(self) -> None:
        self.assertEqual(len(self.rows), 64)
        self.assertEqual(len({row["base_id"] for row in self.rows}), 64)
        self.assertEqual(Counter(row["split"] for row in self.rows), {"dev": 16, "eval": 48})
        eval_rows = [row for row in self.rows if row["split"] == "eval"]
        self.assertEqual(Counter(row["category"] for row in eval_rows), {
            "duplicate_entity": 12,
            "missing_slot": 12,
            "context_reference": 12,
            "negation_correction": 12,
        })
        eval_bytes = "".join(canonical(row) + "\n" for row in eval_rows).encode("utf-8")
        self.assertEqual(sha256(eval_bytes), self.freeze["evaluation_sha256"])

    def test_paired_commands_are_distinct_and_not_cross_split_duplicates(self) -> None:
        commands: dict[str, str] = {}
        for row in self.rows:
            self.assertNotEqual(row["clear_command"], row["ambiguous_command"])
            for key in ("clear_command", "ambiguous_command"):
                normalized = " ".join(row[key].casefold().split())
                self.assertNotIn(normalized, commands, f"duplicate command across {commands.get(normalized)}")
                commands[normalized] = f"{row['base_id']}:{key}"

    def test_inventory_candidates_and_controlled_deltas_are_closed(self) -> None:
        for row in self.rows:
            with self.subTest(base_id=row["base_id"]):
                inventory = {entity["entity_id"]: entity for entity in row["inventory"]}
                states = row["initial_states"]
                candidates = row["candidate_entity_ids"]
                self.assertEqual(set(inventory), set(states))
                self.assertTrue(1 <= len(candidates) <= 3)
                self.assertEqual(len(candidates), len(set(candidates)))
                self.assertTrue(set(candidates) <= set(inventory))
                self.assertIn(row["expected_target_entity"], candidates)
                self.assertIn(row["unrelated_entity_id"], inventory)
                self.assertNotIn(row["unrelated_entity_id"], candidates)
                self.assertEqual(row["expected_delta"]["before"], states[row["expected_target_entity"]])
                self.assertNotEqual(row["expected_delta"]["before"], row["expected_delta"]["after"])
                self.assertEqual(set(row["confirmed_instruction"]), FIELDS)
                self.assertTrue(set(row["state_dependencies"]) <= set(inventory))
                self.assertTrue(all(entity["domain"] in {"light", "cover", "climate"} for entity in inventory.values()))

    def test_expected_target_metadata_matches_confirmed_instruction(self) -> None:
        for row in self.rows:
            target = next(item for item in row["inventory"] if item["entity_id"] == row["expected_target_entity"])
            plan = row["confirmed_instruction"]
            with self.subTest(base_id=row["base_id"]):
                self.assertEqual(target["room"], plan["room"])
                self.assertEqual(target["floor"], plan["floor"])
                expected_domain = {"Light": "light", "Curtain": "cover", "AC": "climate"}[plan["device"]]
                self.assertEqual(target["domain"], expected_domain)

    def test_context_and_unresolved_correction_strata_are_honest(self) -> None:
        eval_rows = [row for row in self.rows if row["split"] == "eval"]
        context = [row for row in eval_rows if row["category"] == "context_reference"]
        corrections = [row for row in eval_rows if row["category"] == "negation_correction"]
        self.assertEqual(len(context), 12)
        self.assertTrue(all(row["state_dependencies"] for row in context))
        uncertainty = re.compile(
            r"\b(perhaps|confirm|which|not sure|ask|maybe|not decided)\b|do not choose|—or|or did",
            re.IGNORECASE,
        )
        self.assertTrue(all(uncertainty.search(row["ambiguous_command"]) for row in corrections))

    def test_no_obvious_secret_private_path_or_high_risk_domain(self) -> None:
        text = DATASET.read_text(encoding="utf-8")
        banned = (
            r"hf_[A-Za-z0-9]{20,}",
            r"(?i)authorization\s*[:=]",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            r"/(?:home|data)/[A-Za-z0-9._-]+/",
            r"\b(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)",
        )
        for pattern in banned:
            self.assertIsNone(re.search(pattern, text), pattern)
        self.assertFalse(any(word in text.casefold() for word in (
            "lock.", "alarm.", "camera.", "gas.", "smoke.", "door.", "medical.",
        )))

    def test_protocol_pilot_is_development_only(self) -> None:
        split_by_id = {row["base_id"]: row["split"] for row in self.rows}
        pilot_ids = self.protocol["pilot_gate"]["base_ids"]
        self.assertEqual(len(pilot_ids), 12)
        self.assertEqual(len(set(pilot_ids)), 12)
        self.assertTrue(all(split_by_id[base_id] == "dev" for base_id in pilot_ids))

    def test_all_frozen_rows_round_trip_through_ground_resolve_and_plan(self) -> None:
        for row in self.rows:
            with self.subTest(base_id=row["base_id"]):
                registry = EntityRegistry(EntitySpec(**item) for item in row["inventory"])
                context = SessionContext(tuple(row["session_context"]["last_referenced_entities"]))
                confirmed = DomuxInstruction(**row["confirmed_instruction"])
                grounded = ground_domux_request(
                    row["ambiguous_command"], confirmed.to_pipe(), registry, context,
                )
                self.assertTrue(grounded.clarification.required)
                resolved = resolve_clarification_submission(
                    grounded,
                    answer=row["clarification_answer"],
                    confirmed_instruction=confirmed,
                    registry=registry,
                )
                self.assertEqual(resolved.chosen.entity_id, row["expected_target_entity"])
                before = row["initial_states"][resolved.chosen.entity_id]
                self.assertEqual(before["entity_id"], resolved.chosen.entity_id)
                plan = build_plan(confirmed, resolved.chosen, before)
                expected = controlled_projection(row["expected_delta"]["after"], resolved.chosen.domain)
                self.assertTrue(projection_matches(expected, plan.expected_projection))


if __name__ == "__main__":
    unittest.main()
