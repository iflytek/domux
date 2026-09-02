#!/usr/bin/env python3
"""Run a reproducible, isolated Home Assistant REST acceptance check.

The runner creates exactly one task-labelled container and one task-labelled
named volume. Synthetic credentials and Home Assistant tokens remain in process
memory and are deliberately excluded from the deterministic JSON artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import secrets
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import clarify_commit as clarify_commit_module
from clarify_commit import (
    DomuxInstruction,
    EntityRegistry,
    EntitySpec,
    HomeAssistantRESTAdapter,
    PreparedActionStore,
    controlled_projection,
    digest_json,
    ground_domux_request,
    projection_matches,
    resolve_clarification_submission,
    resolve_unique_request,
    state_binding,
)


IMAGE_REPOSITORY = "ghcr.io/home-assistant/home-assistant"
IMAGE_DIGEST = "sha256:8e9751cb66d3ba6624f5360a7d31b0c6821f7f5b3fb8ba0d10d58f0f481c540c"
IMAGE_REFERENCE = f"{IMAGE_REPOSITORY}@{IMAGE_DIGEST}"
HOME_ASSISTANT_VERSION = "2026.8.3"
PLATFORM = "linux/amd64"

RUN_LABEL = "io.github.iflytek.domux.ha-acceptance-run"
CONTAINER_PREFIX = "domux-ha-acceptance"
VOLUME_PREFIX = "domux-ha-acceptance-config"
CONTAINER_PORT = 8123
CPU_LIMIT = 1.5
NANO_CPUS = 1_500_000_000
MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
PIDS_LIMIT = 512
PULL_TIMEOUT_SECONDS = 30 * 60
COMMAND_TIMEOUT_SECONDS = 60
READINESS_TIMEOUT_SECONDS = 240
ENTITY_TIMEOUT_SECONDS = 120
STATE_TIMEOUT_SECONDS = 30

ONBOARDING_STEPS = ("user", "core_config", "analytics", "integration")
CASE_DIR = Path(__file__).resolve().parent
V1_DOMUX_EVIDENCE_PATH = CASE_DIR / "evidence" / "v1" / "domux_raw.jsonl"
V1_DOMUX_EVIDENCE_ARTIFACT = "evidence/v1/domux_raw.jsonl"
V1_DOMUX_EVIDENCE_SHA256 = "c0561bc72042dc7415d322fea90649866355dc44d2547f246d87cd87d367e966"
SCENARIO_EVIDENCE_PATH = CASE_DIR / "data" / "scenarios.jsonl"
SCENARIO_EVIDENCE_ARTIFACT = "data/scenarios.jsonl"
SCENARIO_EVIDENCE_SHA256 = "0e27842c62d9cd4e4b1467b43e3ebcd346c79c0125c4f40cce97d363c821a0a0"
HA_REGISTRY_PROFILE = "semantic_target_mapping_subset_not_full_scenario_inventory"
EXECUTION_SOURCE_PATHS = ("clarify_commit.py", "ha_acceptance.py")
EXECUTION_INPUT_PATHS = ("data/scenarios.jsonl", "evidence/v1/domux_raw.jsonl")
ENTITY_IDS = {
    "living_room_light": "light.ceiling_lights",
    "study_light": "light.bed_light",
    "cover": "cover.hall_window",
    "climate": "climate.hvac",
}

CONFIGURATION_YAML = """homeassistant:
  name: Domux HA Acceptance
  latitude: 0
  longitude: 0
  elevation: 0
  unit_system: metric
  time_zone: UTC
  currency: USD

http:
api:
auth:
onboarding:
demo:

logger:
  default: warning
"""


class AcceptanceError(RuntimeError):
    """An expected, credential-safe acceptance failure."""


@dataclass(frozen=True)
class HttpResult:
    """A parsed HTTP result."""

    status: int
    payload: Any


@dataclass(frozen=True)
class PreparedRuntime:
    """Internal runtime details that must not be serialized as evidence."""

    base_url: str
    image: dict[str, Any]


@dataclass(frozen=True)
class DomuxEvidenceKey:
    """Stable identity of one immutable v1 model-output record."""

    base_id: str
    variant: str


@dataclass(frozen=True)
class ExpectedDomuxEvidence:
    """Pinned field digests for one required v1 command/output pair."""

    line_number: int
    query_sha256: str
    raw_output_sha256: str


@dataclass(frozen=True)
class ExpectedFrozenScenario:
    """Pinned identity of one exact row in the pre-model scenario freeze."""

    binding_sha256: str
    line_number: int
    row_sha256: str
    target_entity_id: str


@dataclass(frozen=True)
class RecordedDomuxEvidence:
    """One cryptographically verified command/output pair from v1 evidence."""

    artifact_sha256: str
    base_id: str
    command: str
    line_number: int
    query_sha256: str
    raw_output: str
    raw_output_sha256: str
    variant: str

    @property
    def key(self) -> DomuxEvidenceKey:
        return DomuxEvidenceKey(self.base_id, self.variant)

    def provenance(self) -> dict[str, object]:
        """Return path-safe evidence provenance for the acceptance artifact."""

        return {
            "artifact": V1_DOMUX_EVIDENCE_ARTIFACT,
            "artifact_sha256": self.artifact_sha256,
            "base_id": self.base_id,
            "line_number": self.line_number,
            "pair_verified": True,
            "query_sha256": self.query_sha256,
            "raw_output_sha256": self.raw_output_sha256,
            "validation": "whole_artifact_and_per_field_sha256",
            "variant": self.variant,
        }


@dataclass(frozen=True)
class FrozenScenarioEvidence:
    """Validated scenario gold and target semantics for one base case."""

    ambiguous_command: str
    artifact_sha256: str
    base_id: str
    candidate_entity_ids: tuple[str, ...]
    clarification_answer: str
    clear_command: str
    confirmed_instruction: DomuxInstruction
    expected_target_entity_id: str
    line_number: int
    row_sha256: str
    target_inventory_semantics: Mapping[str, object]

    def command_for_variant(self, variant: str) -> str:
        if variant == "ambiguous":
            return self.ambiguous_command
        if variant == "clear":
            return self.clear_command
        raise AcceptanceError("HA case references an unsupported scenario variant")

    def confirmed_instruction_mapping(self) -> dict[str, str]:
        return {
            "action": self.confirmed_instruction.action,
            "attribute": self.confirmed_instruction.attribute,
            "device": self.confirmed_instruction.device,
            "floor": self.confirmed_instruction.floor,
            "room": self.confirmed_instruction.room,
            "unit": self.confirmed_instruction.unit,
            "value": self.confirmed_instruction.value,
        }

    def binding_payload(self) -> dict[str, object]:
        target_semantics = dict(self.target_inventory_semantics)
        target_semantics["aliases"] = list(target_semantics["aliases"])
        return {
            "ambiguous_command": self.ambiguous_command,
            "base_id": self.base_id,
            "candidate_entity_ids": list(self.candidate_entity_ids),
            "clarification_answer": self.clarification_answer,
            "clear_command": self.clear_command,
            "confirmed_instruction": self.confirmed_instruction_mapping(),
            "expected_target_entity_id": self.expected_target_entity_id,
            "target_inventory_semantics": target_semantics,
        }

    def binding_sha256(self) -> str:
        return digest_json(self.binding_payload())

    def provenance(
        self,
        *,
        variant: str,
        ha_demo_entity_id: str,
        ha_matching_candidate_count: int,
        used_for_resolution: bool,
    ) -> dict[str, object]:
        """Return machine-readable scenario lineage and inventory limitation."""

        target_semantics = dict(self.target_inventory_semantics)
        target_semantics["aliases"] = list(target_semantics["aliases"])
        confirmed = self.confirmed_instruction_mapping()
        return {
            "artifact": SCENARIO_EVIDENCE_ARTIFACT,
            "artifact_sha256": self.artifact_sha256,
            "base_id": self.base_id,
            "binding_sha256": self.binding_sha256(),
            "clarification_answer": self.clarification_answer,
            "clarification_answer_sha256": _sha256_text(self.clarification_answer),
            "confirmed_instruction": confirmed,
            "confirmed_instruction_sha256": _sha256_text(
                self.confirmed_instruction.to_pipe()
            ),
            "expected_target_entity_id": self.expected_target_entity_id,
            "frozen_candidate_count": len(self.candidate_entity_ids),
            "ha_matching_candidate_count": ha_matching_candidate_count,
            "ha_registry_profile": HA_REGISTRY_PROFILE,
            "inventory_limitation": {
                "full_scenario_inventory_reproduced": False,
                "profile": HA_REGISTRY_PROFILE,
            },
            "line_number": self.line_number,
            "post_clarification_model_call": False,
            "row_sha256": self.row_sha256,
            "scenario_target_to_ha_demo_entity": {
                "ha_demo_entity_id": ha_demo_entity_id,
                "scenario_target_entity_id": self.expected_target_entity_id,
                "semantic_fields_match": True,
            },
            "source": "frozen_synthetic_scenario_gold",
            "target_inventory_semantics": target_semantics,
            "used_for_resolution": used_for_resolution,
            "variant": variant,
            "variant_command_sha256": _sha256_text(self.command_for_variant(variant)),
        }


REQUIRED_DOMUX_EVIDENCE = {
    DomuxEvidenceKey("eval-duplicate_entity-01", "ambiguous"): ExpectedDomuxEvidence(
        line_number=2,
        query_sha256=("f27717f08a911d7db2dcafcec7dc4fb5363b9cff40840596c17d548101b6fdcf"),
        raw_output_sha256=("c5fee12f0a6f2de9ca00f1a3d64485625ddcceba18e34a799ddbfe38196dd76e"),
    ),
    DomuxEvidenceKey("eval-duplicate_entity-02", "clear"): ExpectedDomuxEvidence(
        line_number=3,
        query_sha256=("cd4494727bc97d234d9c50f345468ab88b3571230ef610b5ef367d307f30e784"),
        raw_output_sha256=("1435c4aa085aa8a5470b0cafc14629f15ea12f5ea0fdb255fe0f3fcb90a85edf"),
    ),
    DomuxEvidenceKey("eval-duplicate_entity-03", "clear"): ExpectedDomuxEvidence(
        line_number=5,
        query_sha256=("c892af8646f0e3d0e52226ddf5d5c0ac4fed0d977ced9592e693b2abaf88962b"),
        raw_output_sha256=("37282efde74972be3bc4bdab6351683ed9be31dcefbff39e1527ce81a0da58a4"),
    ),
    DomuxEvidenceKey("eval-duplicate_entity-04", "clear"): ExpectedDomuxEvidence(
        line_number=7,
        query_sha256=("01411704354a221b89abc4c31c1d577fa7aecce126d9fd3db966c68bcfe27973"),
        raw_output_sha256=("6caea436fc1c6256aa56198c93270913e9323f6a0935267c55389eef078e57a4"),
    ),
}

REQUIRED_FROZEN_SCENARIOS = {
    "eval-duplicate_entity-01": ExpectedFrozenScenario(
        binding_sha256="39c46c2421f31df71a05882b7fdf43a0c79ce658250c70ee4ecb53c4fff80518",
        line_number=17,
        row_sha256="8b8f6498aef6bb4cb7fd5af3ac9adbef3e2cf0013c7a22451fc339e5f85cd871",
        target_entity_id="light.eval_de_01_living",
    ),
    "eval-duplicate_entity-02": ExpectedFrozenScenario(
        binding_sha256="98e9f02e52d1c81f6a7a18a785885ab64ad93c57a3bd50de8f4f7a68b437ba83",
        line_number=18,
        row_sha256="24f7b968a30cba095932b3f5da47ce8f8fc65d521546a830de1b6ea149db7fb4",
        target_entity_id="cover.eval_de_02_upstairs_hall",
    ),
    "eval-duplicate_entity-03": ExpectedFrozenScenario(
        binding_sha256="4e149daeff09b5307563d5e96661e417f9349f9cc3129f80877ed2c7d4ac46c3",
        line_number=19,
        row_sha256="ef0be54be7aaae140c846076fe9568dbea981fd6a96575b12cb6633057756bdf",
        target_entity_id="climate.eval_de_03_bedroom_second",
    ),
    "eval-duplicate_entity-04": ExpectedFrozenScenario(
        binding_sha256="675d850e8d4ccb07709daa3d2b644a6c03f12aa5c239cf62dce62aa72e542b42",
        line_number=20,
        row_sha256="d9b32826a3e0229d5943e69a8ba3192635194f5ba87120945a6a92274e24c78e",
        target_entity_id="light.eval_de_04_study",
    ),
}


@dataclass(frozen=True)
class SutCase:
    """One v1-recorded Domux request and expected server-side service shape."""

    name: str
    evidence_key: DomuxEvidenceKey
    domain: str
    entity_id: str
    expected_service: str
    expected_service_data: Mapping[str, object]
    expected_before: Mapping[str, object]
    expected_after: Mapping[str, object]


@dataclass(frozen=True)
class TargetDriftCase:
    """One prepared action invalidated by a real out-of-band HA state change."""

    name: str
    evidence_key: DomuxEvidenceKey
    domain: str
    entity_id: str
    expected_service: str
    expected_service_data: Mapping[str, object]
    expected_bound_state: Mapping[str, object]
    mutation_service: str
    mutation_service_data: Mapping[str, object]
    expected_drifted_state: Mapping[str, object]


SUT_CASES = (
    SutCase(
        name="recorded_ambiguous_light_off",
        evidence_key=DomuxEvidenceKey("eval-duplicate_entity-01", "ambiguous"),
        domain="light",
        entity_id="light.ceiling_lights",
        expected_service="turn_off",
        expected_service_data={
            "entity_id": "light.ceiling_lights",
        },
        expected_before={"brightness": 178, "state": "on"},
        expected_after={"brightness": None, "state": "off"},
    ),
    SutCase(
        name="recorded_unique_cover_position",
        evidence_key=DomuxEvidenceKey("eval-duplicate_entity-02", "clear"),
        domain="cover",
        entity_id="cover.hall_window",
        expected_service="set_cover_position",
        expected_service_data={
            "entity_id": "cover.hall_window",
            "position": 20,
        },
        expected_before={"current_position": 80, "state": "open"},
        expected_after={"current_position": 20, "state": "open"},
    ),
    SutCase(
        name="recorded_unique_climate_temperature",
        evidence_key=DomuxEvidenceKey("eval-duplicate_entity-03", "clear"),
        domain="climate",
        entity_id="climate.hvac",
        expected_service="set_temperature",
        expected_service_data={
            "entity_id": "climate.hvac",
            "temperature": 22.0,
        },
        expected_before={"state": "cool", "temperature": 24},
        expected_after={"state": "cool", "temperature": 22},
    ),
)

TARGET_DRIFT_CASE = TargetDriftCase(
    name="recorded_study_light_state_drift_rejected",
    evidence_key=DomuxEvidenceKey("eval-duplicate_entity-04", "clear"),
    domain="light",
    entity_id="light.bed_light",
    expected_service="turn_on",
    expected_service_data={
        "brightness_pct": 35.0,
        "entity_id": "light.bed_light",
    },
    expected_bound_state={"brightness": 166, "state": "on"},
    mutation_service="turn_on",
    mutation_service_data={
        "brightness_pct": 25.0,
        "entity_id": "light.bed_light",
    },
    expected_drifted_state={"brightness": 64, "state": "on"},
)


def sut_registry() -> EntityRegistry:
    """Return the fixed allow-list corresponding to pinned HA demo entities."""

    return EntityRegistry(
        (
            EntitySpec(
                "light.bed_light",
                "light",
                "Light",
                "Study",
                "Ground Floor",
            ),
            EntitySpec(
                "light.ceiling_lights",
                "light",
                "Light",
                "Living Room",
                "Ground Floor",
            ),
            EntitySpec(
                "cover.hall_window",
                "cover",
                "Curtain",
                "Hall",
                "First Floor",
            ),
            EntitySpec(
                "climate.hvac",
                "climate",
                "AC",
                "Bedroom",
                "Second Floor",
            ),
        )
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _stable_file_binding(relative: str) -> dict[str, object]:
    """Hash one regular, non-symlink case file without a read-time change."""

    path = CASE_DIR / relative
    try:
        if path.is_symlink() or not path.is_file():
            raise AcceptanceError(f"execution file is not regular: {relative}")
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise AcceptanceError(f"execution file is unavailable: {relative}") from exc
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
        before.st_size,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
        after.st_size,
    )
    if before_identity != after_identity or len(payload) != after.st_size:
        raise AcceptanceError(f"execution file changed while hashing: {relative}")
    return {"sha256": _sha256_bytes(payload), "size_bytes": len(payload)}


def _verify_module_origins() -> None:
    """Reject execution when imported project modules came from another tree."""

    expected = {
        "clarify_commit.py": getattr(clarify_commit_module, "__file__", None),
        "ha_acceptance.py": __file__,
    }
    for relative, origin in expected.items():
        if not isinstance(origin, str):
            raise AcceptanceError(f"execution module origin is unavailable: {relative}")
        try:
            resolved = Path(origin).resolve(strict=True)
            required = (CASE_DIR / relative).resolve(strict=True)
        except OSError as exc:
            raise AcceptanceError(
                f"execution module origin is unavailable: {relative}"
            ) from exc
        if resolved != required:
            raise AcceptanceError(f"execution module origin changed: {relative}")


def capture_execution_bindings() -> dict[str, dict[str, dict[str, object]]]:
    """Capture exact repository inputs and Python sources used by the HA run."""

    groups: dict[str, dict[str, dict[str, object]]] = {}
    for group_name, relative_paths in (
        ("inputs", EXECUTION_INPUT_PATHS),
        ("sources", EXECUTION_SOURCE_PATHS),
    ):
        bindings: dict[str, dict[str, object]] = {}
        for relative in relative_paths:
            bindings[relative] = _stable_file_binding(relative)
        groups[group_name] = bindings
    return groups


def finalize_execution_source_bindings(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, object]:
    """Create provenance only when every execution file stayed byte-stable."""

    if before != after:
        raise AcceptanceError("execution sources or inputs changed during acceptance")
    bundle_payload = json.dumps(
        before,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "binding_bundle_sha256": _sha256_bytes(bundle_payload),
        "digest_algorithm": "sha256",
        "host_python": {
            "implementation": sys.implementation.name,
            "version": ".".join(str(value) for value in sys.version_info[:3]),
        },
        "inputs": dict(before["inputs"]),
        "module_origins_verified": True,
        "path_base": "case_directory",
        "pre_post_execution_match": True,
        "schema_version": 1,
        "sources": dict(before["sources"]),
    }


def collect_execution_source_bindings() -> dict[str, object]:
    """Collect a same-instant binding, primarily for isolated unit checks."""

    _verify_module_origins()
    bindings = capture_execution_bindings()
    return finalize_execution_source_bindings(bindings, bindings)


def _configured_evidence_keys() -> tuple[DomuxEvidenceKey, ...]:
    return tuple(case.evidence_key for case in SUT_CASES) + (TARGET_DRIFT_CASE.evidence_key,)


def load_recorded_domux_evidence(
    path: Path = V1_DOMUX_EVIDENCE_PATH,
) -> dict[DomuxEvidenceKey, RecordedDomuxEvidence]:
    """Load and verify the four exact v1 command/output pairs used by HA.

    Verification is deliberately independent of the mutable current evidence:
    the complete v1 JSONL byte stream, each command/output field digest, and the
    four required record identities are pinned in this runner.
    """

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AcceptanceError("v1 Domux evidence artifact is unavailable") from exc
    artifact_sha256 = _sha256_bytes(payload)
    if artifact_sha256 != V1_DOMUX_EVIDENCE_SHA256:
        raise AcceptanceError("v1 Domux evidence artifact digest does not match")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise AcceptanceError("v1 Domux evidence artifact is not UTF-8") from exc

    records: dict[DomuxEvidenceKey, RecordedDomuxEvidence] = {}
    seen_keys: set[DomuxEvidenceKey] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise AcceptanceError("v1 Domux evidence contains a blank record")
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AcceptanceError("v1 Domux evidence contains invalid JSON") from exc
        if not isinstance(item, Mapping):
            raise AcceptanceError("v1 Domux evidence record is not an object")
        base_id = item.get("base_id")
        variant = item.get("variant")
        if not isinstance(base_id, str) or not isinstance(variant, str):
            raise AcceptanceError("v1 Domux evidence record identity is invalid")
        key = DomuxEvidenceKey(base_id, variant)
        if key in seen_keys:
            raise AcceptanceError("v1 Domux evidence contains a duplicate record key")
        seen_keys.add(key)
        expected = REQUIRED_DOMUX_EVIDENCE.get(key)
        if expected is None:
            continue
        if line_number != expected.line_number:
            raise AcceptanceError("required v1 Domux evidence line number does not match")

        command = item.get("command")
        raw_output = item.get("raw_output")
        query_sha256 = item.get("query_sha256")
        raw_output_sha256 = item.get("raw_output_sha256")
        if (
            item.get("status") != "ok"
            or not isinstance(command, str)
            or not isinstance(raw_output, str)
            or not isinstance(query_sha256, str)
            or not isinstance(raw_output_sha256, str)
        ):
            raise AcceptanceError("required v1 Domux evidence record is incomplete")
        calculated_query_sha256 = _sha256_text(command)
        calculated_raw_output_sha256 = _sha256_text(raw_output)
        if (
            query_sha256 != calculated_query_sha256
            or query_sha256 != expected.query_sha256
            or raw_output_sha256 != calculated_raw_output_sha256
            or raw_output_sha256 != expected.raw_output_sha256
        ):
            raise AcceptanceError("required v1 Domux command/output field digest does not match")
        records[key] = RecordedDomuxEvidence(
            artifact_sha256=artifact_sha256,
            base_id=base_id,
            command=command,
            line_number=line_number,
            query_sha256=query_sha256,
            raw_output=raw_output,
            raw_output_sha256=raw_output_sha256,
            variant=variant,
        )

    configured_keys = _configured_evidence_keys()
    if len(configured_keys) != len(set(configured_keys)) or set(configured_keys) != set(
        REQUIRED_DOMUX_EVIDENCE
    ):
        raise AcceptanceError("HA cases do not reference the four pinned v1 records")
    missing = set(REQUIRED_DOMUX_EVIDENCE) - set(records)
    if missing:
        raise AcceptanceError("required v1 Domux evidence record is missing")
    return {key: records[key] for key in configured_keys}


def _evidence_for_case(
    evidence: Mapping[DomuxEvidenceKey, RecordedDomuxEvidence],
    key: DomuxEvidenceKey,
) -> RecordedDomuxEvidence:
    record = evidence.get(key)
    expected = REQUIRED_DOMUX_EVIDENCE.get(key)
    if (
        not isinstance(record, RecordedDomuxEvidence)
        or expected is None
        or record.key != key
        or record.artifact_sha256 != V1_DOMUX_EVIDENCE_SHA256
        or record.line_number != expected.line_number
        or record.query_sha256 != expected.query_sha256
        or record.raw_output_sha256 != expected.raw_output_sha256
        or _sha256_text(record.command) != record.query_sha256
        or _sha256_text(record.raw_output) != record.raw_output_sha256
    ):
        raise AcceptanceError("HA case is not bound to verified v1 Domux evidence")
    return record


def load_frozen_scenario_evidence(
    path: Path = SCENARIO_EVIDENCE_PATH,
) -> dict[str, FrozenScenarioEvidence]:
    """Load the exact pre-model scenario rows needed by the HA acceptance."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise AcceptanceError("frozen scenario artifact is unavailable") from exc
    artifact_sha256 = _sha256_bytes(payload)
    if artifact_sha256 != SCENARIO_EVIDENCE_SHA256:
        raise AcceptanceError("frozen scenario artifact digest does not match")

    scenarios: dict[str, FrozenScenarioEvidence] = {}
    seen_base_ids: set[str] = set()
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        if not raw_line:
            raise AcceptanceError("frozen scenario artifact contains a blank record")
        try:
            item = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AcceptanceError("frozen scenario artifact contains invalid JSON") from exc
        if not isinstance(item, Mapping) or not isinstance(item.get("base_id"), str):
            raise AcceptanceError("frozen scenario record identity is invalid")
        base_id = item["base_id"]
        if base_id in seen_base_ids:
            raise AcceptanceError("frozen scenario artifact contains a duplicate base id")
        seen_base_ids.add(base_id)
        expected = REQUIRED_FROZEN_SCENARIOS.get(base_id)
        if expected is None:
            continue
        row_sha256 = _sha256_bytes(raw_line)
        if line_number != expected.line_number or row_sha256 != expected.row_sha256:
            raise AcceptanceError("required frozen scenario row digest does not match")

        clear_command = item.get("clear_command")
        ambiguous_command = item.get("ambiguous_command")
        clarification_answer = item.get("clarification_answer")
        confirmed = item.get("confirmed_instruction")
        expected_target = item.get("expected_target_entity")
        candidate_entity_ids = item.get("candidate_entity_ids")
        inventory = item.get("inventory")
        if (
            item.get("schema_version") != 1
            or item.get("split") != "eval"
            or item.get("category") != "duplicate_entity"
            or item.get("ambiguity_expected") is not True
            or not isinstance(clear_command, str)
            or not isinstance(ambiguous_command, str)
            or not isinstance(clarification_answer, str)
            or not isinstance(confirmed, Mapping)
            or not isinstance(expected_target, str)
            or not isinstance(candidate_entity_ids, list)
            or not all(isinstance(value, str) for value in candidate_entity_ids)
            or not isinstance(inventory, list)
        ):
            raise AcceptanceError("required frozen scenario record is incomplete")
        if (
            expected_target != expected.target_entity_id
            or len(candidate_entity_ids) != len(set(candidate_entity_ids))
            or expected_target not in candidate_entity_ids
        ):
            raise AcceptanceError("frozen scenario target or candidate set is unexpected")

        confirmed_fields = (
            "action",
            "device",
            "attribute",
            "value",
            "unit",
            "room",
            "floor",
        )
        if set(confirmed) != set(confirmed_fields) or not all(
            isinstance(confirmed.get(field), str) for field in confirmed_fields
        ):
            raise AcceptanceError("frozen confirmed instruction is invalid")
        confirmed_instruction = DomuxInstruction.from_fields(
            tuple(confirmed[field] for field in confirmed_fields)
        )

        target_items = [
            value
            for value in inventory
            if isinstance(value, Mapping) and value.get("entity_id") == expected_target
        ]
        if len(target_items) != 1:
            raise AcceptanceError("frozen target inventory entry is missing or duplicated")
        target = target_items[0]
        target_fields = {"aliases", "device", "domain", "entity_id", "floor", "room"}
        aliases = target.get("aliases")
        if (
            set(target) != target_fields
            or not isinstance(aliases, list)
            or not all(isinstance(value, str) for value in aliases)
            or not all(
                isinstance(target.get(field), str)
                for field in ("device", "domain", "entity_id", "floor", "room")
            )
        ):
            raise AcceptanceError("frozen target inventory semantics are invalid")
        scenario = FrozenScenarioEvidence(
            ambiguous_command=ambiguous_command,
            artifact_sha256=artifact_sha256,
            base_id=base_id,
            candidate_entity_ids=tuple(candidate_entity_ids),
            clarification_answer=clarification_answer,
            clear_command=clear_command,
            confirmed_instruction=confirmed_instruction,
            expected_target_entity_id=expected_target,
            line_number=line_number,
            row_sha256=row_sha256,
            target_inventory_semantics={
                "aliases": tuple(aliases),
                "device": target["device"],
                "domain": target["domain"],
                "floor": target["floor"],
                "room": target["room"],
            },
        )
        if scenario.binding_sha256() != expected.binding_sha256:
            raise AcceptanceError("required frozen scenario binding digest does not match")
        scenarios[base_id] = scenario

    missing = set(REQUIRED_FROZEN_SCENARIOS) - set(scenarios)
    if missing:
        raise AcceptanceError("required frozen scenario record is missing")
    return {base_id: scenarios[base_id] for base_id in REQUIRED_FROZEN_SCENARIOS}


def _scenario_for_case(
    scenarios: Mapping[str, FrozenScenarioEvidence],
    case: SutCase | TargetDriftCase,
) -> FrozenScenarioEvidence:
    scenario = scenarios.get(case.evidence_key.base_id)
    expected = REQUIRED_FROZEN_SCENARIOS.get(case.evidence_key.base_id)
    if (
        not isinstance(scenario, FrozenScenarioEvidence)
        or expected is None
        or scenario.base_id != case.evidence_key.base_id
        or scenario.artifact_sha256 != SCENARIO_EVIDENCE_SHA256
        or scenario.line_number != expected.line_number
        or scenario.row_sha256 != expected.row_sha256
        or scenario.binding_sha256() != expected.binding_sha256
        or scenario.expected_target_entity_id != expected.target_entity_id
    ):
        raise AcceptanceError("HA case is not bound to a verified frozen scenario")
    return scenario


def _configured_cases() -> tuple[SutCase | TargetDriftCase, ...]:
    return (*SUT_CASES, TARGET_DRIFT_CASE)


def validate_acceptance_evidence(
    recorded_evidence: Mapping[DomuxEvidenceKey, RecordedDomuxEvidence],
    scenario_evidence: Mapping[str, FrozenScenarioEvidence],
) -> None:
    """Validate model, scenario-gold, and HA semantic mapping before Docker."""

    if set(recorded_evidence) != set(REQUIRED_DOMUX_EVIDENCE):
        raise AcceptanceError("acceptance did not receive exactly four v1 records")
    if set(scenario_evidence) != set(REQUIRED_FROZEN_SCENARIOS):
        raise AcceptanceError("acceptance did not receive exactly four frozen scenarios")
    registry = sut_registry()
    for case in _configured_cases():
        record = _evidence_for_case(recorded_evidence, case.evidence_key)
        scenario = _scenario_for_case(scenario_evidence, case)
        if scenario.command_for_variant(case.evidence_key.variant) != record.command:
            raise AcceptanceError("v1 command does not match its frozen scenario variant")

        target_semantics = dict(scenario.target_inventory_semantics)
        ha_entity = registry.get(case.entity_id)
        expected_semantics = {
            "aliases": tuple(ha_entity.aliases),
            "device": ha_entity.device,
            "domain": ha_entity.domain,
            "floor": ha_entity.floor,
            "room": ha_entity.room,
        }
        if target_semantics != expected_semantics or ha_entity.domain != case.domain:
            raise AcceptanceError("scenario target semantics do not match the HA mapping")

        grounded = ground_domux_request(record.command, record.raw_output, registry)
        if case.evidence_key.variant == "ambiguous":
            if not grounded.clarification.required:
                raise AcceptanceError("frozen ambiguous case unexpectedly resolved uniquely")
            resolved = resolve_clarification_submission(
                grounded,
                answer=scenario.clarification_answer,
                confirmed_instruction=scenario.confirmed_instruction,
                registry=registry,
            )
        elif case.evidence_key.variant == "clear":
            if grounded.clarification.required:
                raise AcceptanceError("frozen clear case unexpectedly requires clarification")
            resolved = resolve_unique_request(grounded, registry)
        else:
            raise AcceptanceError("HA case references an unsupported evidence variant")
        if resolved.chosen.entity_id != case.entity_id:
            raise AcceptanceError("frozen scenario maps to an unexpected HA demo entity")


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_single_inspect(stdout: bytes | str, resource: str) -> dict[str, Any]:
    try:
        payload = json.loads(_decode(stdout))
    except json.JSONDecodeError as exc:
        raise AcceptanceError(f"docker {resource} inspect returned invalid JSON") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise AcceptanceError(f"docker {resource} inspect returned an unexpected shape")
    return payload[0]


def configuration_archive() -> bytes:
    """Build a deterministic in-memory tar archive for ``docker cp -``."""

    content = CONFIGURATION_YAML.encode("utf-8")
    info = tarfile.TarInfo("configuration.yaml")
    info.size = len(content)
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class DockerCli:
    """A minimal Docker CLI adapter with ownership-checked cleanup."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        run_id: str | None = None,
    ) -> None:
        self._runner = runner
        self.run_id = run_id or secrets.token_hex(8)
        if not re.fullmatch(r"[a-f0-9]{12,64}", self.run_id):
            raise ValueError("run_id must be 12-64 lowercase hexadecimal characters")
        self.container_name = f"{CONTAINER_PREFIX}-{self.run_id}"
        self.volume_name = f"{VOLUME_PREFIX}-{self.run_id}"
        self._container_may_exist = False
        self._volume_may_exist = False

    def _invoke(
        self,
        arguments: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int = COMMAND_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["docker", *arguments]
        try:
            return self._runner(
                command,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            operation = arguments[0] if arguments else "command"
            raise AcceptanceError(f"docker {operation} timed out") from None
        except OSError:
            raise AcceptanceError("docker CLI could not be executed") from None

    def _run(
        self,
        arguments: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        timeout: int = COMMAND_TIMEOUT_SECONDS,
    ) -> str:
        completed = self._invoke(arguments, input_bytes=input_bytes, timeout=timeout)
        if completed.returncode != 0:
            operation = arguments[0] if arguments else "command"
            raise AcceptanceError(f"docker {operation} failed")
        return _decode(completed.stdout)

    def _optional_inspect(self, resource: str, name: str) -> dict[str, Any] | None:
        completed = self._invoke([resource, "inspect", name])
        if completed.returncode == 0:
            return _parse_single_inspect(completed.stdout, resource)
        stderr = _decode(completed.stderr).lower()
        if completed.returncode == 1 and (
            "no such" in stderr or "not found" in stderr
        ):
            return None
        raise AcceptanceError(f"docker {resource} inspect failed")

    def _validate_volume_ownership(self, inspect: Mapping[str, Any]) -> None:
        labels = inspect.get("Labels")
        if not isinstance(labels, dict) or labels.get(RUN_LABEL) != self.run_id:
            raise AcceptanceError("refusing to manage a volume without the task run label")
        if inspect.get("Name") != self.volume_name:
            raise AcceptanceError("volume identity does not match the task resource")

    def _validate_container_ownership(self, inspect: Mapping[str, Any]) -> None:
        config = inspect.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if not isinstance(labels, dict) or labels.get(RUN_LABEL) != self.run_id:
            raise AcceptanceError("refusing to manage a container without the task run label")
        name = inspect.get("Name")
        if name not in {self.container_name, f"/{self.container_name}"}:
            raise AcceptanceError("container identity does not match the task resource")

    def _inspect_image(self) -> dict[str, Any]:
        raw = self._run(["image", "inspect", IMAGE_REFERENCE])
        inspect = _parse_single_inspect(raw, "image")
        labels = inspect.get("Config", {}).get("Labels", {})
        repo_digests = inspect.get("RepoDigests", [])
        if inspect.get("Os") != "linux" or inspect.get("Architecture") != "amd64":
            raise AcceptanceError("pinned image does not resolve to linux/amd64")
        if not isinstance(labels, dict) or labels.get("io.hass.version") != HOME_ASSISTANT_VERSION:
            raise AcceptanceError("pinned image Home Assistant version label is unexpected")
        if not isinstance(repo_digests, list) or IMAGE_REFERENCE not in repo_digests:
            raise AcceptanceError("official repository digest is absent after pull")
        if inspect.get("Config", {}).get("Healthcheck") is not None:
            raise AcceptanceError("pinned image unexpectedly defines a Docker healthcheck")
        return {
            "architecture": "amd64",
            "docker_healthcheck": False,
            "manifest_digest": IMAGE_DIGEST,
            "operating_system": "linux",
            "repository": IMAGE_REPOSITORY,
            "version": HOME_ASSISTANT_VERSION,
        }

    def _validate_runtime(self, inspect: Mapping[str, Any]) -> None:
        self._validate_container_ownership(inspect)
        config = inspect.get("Config", {})
        host = inspect.get("HostConfig", {})
        if not isinstance(config, dict) or config.get("Image") != IMAGE_REFERENCE:
            raise AcceptanceError("container does not use the pinned official image")
        if not isinstance(host, dict):
            raise AcceptanceError("container host configuration is missing")
        restart = host.get("RestartPolicy", {})
        if not isinstance(restart, dict) or restart.get("Name") != "no":
            raise AcceptanceError("container restart policy is not disabled")
        if host.get("NanoCpus") != NANO_CPUS:
            raise AcceptanceError("container CPU limit differs from the acceptance profile")
        if host.get("Memory") != MEMORY_LIMIT_BYTES:
            raise AcceptanceError("container memory limit differs from the acceptance profile")
        if host.get("PidsLimit") != PIDS_LIMIT:
            raise AcceptanceError("container PID limit differs from the acceptance profile")
        if host.get("Privileged") is not False:
            raise AcceptanceError("container must not be privileged")
        if host.get("NetworkMode") not in {"bridge", "default"}:
            raise AcceptanceError("container must use an isolated bridge network")

        port_bindings = host.get("PortBindings", {})
        expected_key = f"{CONTAINER_PORT}/tcp"
        if not isinstance(port_bindings, dict) or set(port_bindings) != {expected_key}:
            raise AcceptanceError("container has unexpected published ports")
        bindings = port_bindings[expected_key]
        if (
            not isinstance(bindings, list)
            or len(bindings) != 1
            or bindings[0].get("HostIp") != "127.0.0.1"
            or bindings[0].get("HostPort") not in {"", "0"}
        ):
            raise AcceptanceError("container port is not a random loopback-only binding")

        mounts = inspect.get("Mounts")
        if not isinstance(mounts, list) or len(mounts) != 1:
            raise AcceptanceError("container must have exactly one mount")
        mount = mounts[0]
        if (
            not isinstance(mount, dict)
            or mount.get("Type") != "volume"
            or mount.get("Name") != self.volume_name
            or mount.get("Destination") != "/config"
            or mount.get("RW") is not True
        ):
            raise AcceptanceError("container config mount is not the task named volume")

    def prepare(self) -> PreparedRuntime:
        """Pull, validate, create, configure, and start the isolated runtime."""

        self._run(
            ["pull", "--platform", PLATFORM, IMAGE_REFERENCE],
            timeout=PULL_TIMEOUT_SECONDS,
        )
        image = self._inspect_image()

        self._volume_may_exist = True
        created_volume = self._run(
            [
                "volume",
                "create",
                "--label",
                f"{RUN_LABEL}={self.run_id}",
                self.volume_name,
            ]
        ).strip()
        if created_volume != self.volume_name:
            raise AcceptanceError("docker returned an unexpected volume identity")
        volume_inspect = self._optional_inspect("volume", self.volume_name)
        if volume_inspect is None:
            raise AcceptanceError("task named volume disappeared after creation")
        self._validate_volume_ownership(volume_inspect)

        self._container_may_exist = True
        self._run(
            [
                "create",
                "--name",
                self.container_name,
                "--label",
                f"{RUN_LABEL}={self.run_id}",
                "--restart=no",
                f"--cpus={CPU_LIMIT}",
                "--memory=2g",
                f"--pids-limit={PIDS_LIMIT}",
                "--env",
                "TZ=UTC",
                "--mount",
                f"type=volume,source={self.volume_name},target=/config",
                "--publish",
                f"127.0.0.1::{CONTAINER_PORT}",
                IMAGE_REFERENCE,
            ]
        )
        container_inspect = self._optional_inspect("container", self.container_name)
        if container_inspect is None:
            raise AcceptanceError("task container disappeared after creation")
        self._validate_runtime(container_inspect)

        self._run(
            ["cp", "-", f"{self.container_name}:/config"],
            input_bytes=configuration_archive(),
        )
        self._run(["start", self.container_name])
        binding = self._run(
            ["port", self.container_name, f"{CONTAINER_PORT}/tcp"]
        ).strip()
        match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", binding)
        if match is None:
            raise AcceptanceError("Docker returned a non-loopback or malformed port binding")
        host_port = int(match.group(1))
        if not 1024 <= host_port <= 65535:
            raise AcceptanceError("Docker-assigned host port is not a high port")
        return PreparedRuntime(
            base_url=f"http://127.0.0.1:{host_port}",
            image=image,
        )

    def cleanup(self) -> None:
        """Remove only this run's label-verified container and named volume."""

        failures: list[str] = []
        if self._container_may_exist:
            try:
                inspect = self._optional_inspect("container", self.container_name)
                if inspect is not None:
                    self._validate_container_ownership(inspect)
                    self._run(["rm", "--force", self.container_name])
                if self._optional_inspect("container", self.container_name) is not None:
                    raise AcceptanceError("task container still exists after removal")
                self._container_may_exist = False
            except AcceptanceError:
                failures.append("container")

        if self._volume_may_exist:
            try:
                inspect = self._optional_inspect("volume", self.volume_name)
                if inspect is not None:
                    self._validate_volume_ownership(inspect)
                    self._run(["volume", "rm", self.volume_name])
                if self._optional_inspect("volume", self.volume_name) is not None:
                    raise AcceptanceError("task volume still exists after removal")
                self._volume_may_exist = False
            except AcceptanceError:
                failures.append("volume")

        if failures:
            resources = " and ".join(failures)
            raise AcceptanceError(f"failed to clean the task-owned {resources}")


class HomeAssistantApi:
    """Small Home Assistant HTTP REST/onboarding/auth client."""

    def __init__(
        self,
        base_url: str,
        *,
        opener: Callable[..., Any] = urlopen,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._opener = opener
        self._monotonic = monotonic
        self._sleep = sleep
        self.direct_service_calls: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        form_body: Mapping[str, str] | None = None,
        token: str | None = None,
        expected: Iterable[int] = (200,),
        timeout: float = 30,
    ) -> HttpResult:
        """Call one HA endpoint without including request data in errors."""

        if json_body is not None and form_body is not None:
            raise ValueError("request cannot contain both JSON and form data")
        headers = {"Accept": "application/json"}
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form_body is not None:
            body = urlencode(form_body).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"

        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                status = response.status
                raw = response.read()
        except HTTPError as exc:
            status = exc.code
            raw = exc.read()
        except (URLError, TimeoutError, OSError):
            raise AcceptanceError(f"{method} {path} failed") from None

        payload: Any = None
        if raw:
            try:
                payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = raw.decode("utf-8", errors="replace")
        expected_statuses = set(expected)
        if status not in expected_statuses:
            raise AcceptanceError(f"{method} {path} returned unexpected HTTP {status}")
        return HttpResult(status=status, payload=payload)

    def call_service(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        token: str,
        *,
        evidence_phase: str,
    ) -> HttpResult:
        """Call the official HA REST service endpoint with an explicit audit phase."""

        if evidence_phase not in {"setup", "external_fault_injection"}:
            raise ValueError("unsupported direct-service evidence phase")
        path = f"/api/services/{domain}/{service}"
        result = self.request(
            "POST",
            path,
            json_body=data,
            token=token,
        )
        self.direct_service_calls.append(
            _direct_service_event(
                evidence_phase,
                domain,
                service,
                path,
                result.status,
            )
        )
        return result

    def wait_for_readiness(self) -> HttpResult:
        deadline = self._monotonic() + READINESS_TIMEOUT_SECONDS
        while True:
            try:
                result = self.request(
                    "GET", "/api/onboarding", expected=(200,), timeout=5
                )
                if isinstance(result.payload, list):
                    return result
            except AcceptanceError:
                pass
            if self._monotonic() >= deadline:
                raise AcceptanceError("Home Assistant onboarding endpoint was not ready")
            self._sleep(1)

    def wait_for_entities(self, entity_ids: Iterable[str], token: str) -> None:
        expected = set(entity_ids)
        deadline = self._monotonic() + ENTITY_TIMEOUT_SECONDS
        while True:
            result = self.request("GET", "/api/states", token=token)
            if isinstance(result.payload, list):
                actual = {
                    item.get("entity_id")
                    for item in result.payload
                    if isinstance(item, dict)
                }
                if expected <= actual:
                    return
            if self._monotonic() >= deadline:
                raise AcceptanceError("required Home Assistant demo entities were not loaded")
            self._sleep(1)

    def wait_for_state(
        self, entity_id: str, desired: str, token: str
    ) -> dict[str, Any]:
        deadline = self._monotonic() + STATE_TIMEOUT_SECONDS
        while True:
            result = self.request(
                "GET", f"/api/states/{entity_id}", token=token
            )
            if isinstance(result.payload, dict) and result.payload.get("state") == desired:
                return result.payload
            if self._monotonic() >= deadline:
                raise AcceptanceError(f"{entity_id} did not reach the expected state")
            self._sleep(0.25)

    def wait_for_projection(
        self,
        entity_id: str,
        token: str,
        *,
        state: str,
        attributes: Mapping[str, object],
    ) -> dict[str, Any]:
        """Wait for fields changed by a direct-REST setup or fault injection."""

        deadline = self._monotonic() + STATE_TIMEOUT_SECONDS
        while True:
            result = self.request(
                "GET", f"/api/states/{entity_id}", token=token
            )
            if isinstance(result.payload, dict):
                actual_attributes = result.payload.get("attributes")
                if isinstance(actual_attributes, Mapping):
                    attributes_match = all(
                        _scalar_matches(actual_attributes.get(key), expected)
                        for key, expected in attributes.items()
                    )
                    if result.payload.get("state") == state and attributes_match:
                        return result.payload
            if self._monotonic() >= deadline:
                raise AcceptanceError(
                    f"{entity_id} did not reach its deterministic setup projection"
                )
            self._sleep(0.25)


def _onboarding_state(payload: Any, expected_done: bool) -> dict[str, bool]:
    if not isinstance(payload, list):
        raise AcceptanceError("onboarding response is not a list")
    states: dict[str, bool] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise AcceptanceError("onboarding response contains a non-object")
        step = item.get("step")
        done = item.get("done")
        if step not in ONBOARDING_STEPS or not isinstance(done, bool) or step in states:
            raise AcceptanceError("onboarding response contains unexpected step data")
        states[step] = done
    if set(states) != set(ONBOARDING_STEPS):
        raise AcceptanceError("onboarding response does not contain the four pinned steps")
    if any(states[step] is not expected_done for step in ONBOARDING_STEPS):
        state_name = "complete" if expected_done else "fresh"
        raise AcceptanceError(f"onboarding state is not {state_name}")
    return {step: states[step] for step in ONBOARDING_STEPS}


def _service_call(
    api: HomeAssistantApi,
    domain: str,
    service: str,
    entity_id: str,
    token: str,
    *,
    evidence_phase: str,
    extra: Mapping[str, Any] | None = None,
) -> int:
    body: dict[str, Any] = {"entity_id": entity_id}
    if extra:
        body.update(extra)
    return api.call_service(
        domain,
        service,
        body,
        token,
        evidence_phase=evidence_phase,
    ).status


def _direct_service_event(
    phase: str,
    domain: str,
    service: str,
    request_path: str,
    http_status: int,
) -> dict[str, object]:
    """Build one credential-free record for a successful direct REST call."""

    return {
        "domain": domain,
        "http_status": http_status,
        "phase": phase,
        "request_path": request_path,
        "service": service,
    }


def _scalar_matches(actual: object, expected: object) -> bool:
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        return math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=0.01)
    return actual == expected


def _assert_projection_subset(
    actual: Mapping[str, object] | None,
    expected: Mapping[str, object],
    *,
    label: str,
) -> None:
    if actual is None or any(
        key not in actual or not _scalar_matches(actual[key], value)
        for key, value in expected.items()
    ):
        raise AcceptanceError(f"{label} does not match the pinned controlled projection")


def normalize_setup_state(
    api: HomeAssistantApi, access_token: str
) -> dict[str, Any]:
    """Normalize deterministic demo state using direct REST outside the SUT."""

    calls: list[dict[str, object]] = []

    def setup_call(
        domain: str,
        service: str,
        entity_id: str,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        status = _service_call(
            api,
            domain,
            service,
            entity_id,
            access_token,
            evidence_phase="setup",
            extra=extra,
        )
        calls.append({"domain": domain, "http": status, "service": service})

    setup_call(
        "light",
        "turn_on",
        ENTITY_IDS["living_room_light"],
        {"brightness_pct": 70, "color_temp_kelvin": 3000},
    )
    setup_call(
        "light",
        "turn_on",
        ENTITY_IDS["study_light"],
        {"brightness_pct": 65, "color_temp_kelvin": 3000},
    )
    setup_call(
        "cover",
        "set_cover_position",
        ENTITY_IDS["cover"],
        {"position": 80},
    )
    setup_call(
        "climate",
        "set_hvac_mode",
        ENTITY_IDS["climate"],
        {"hvac_mode": "cool"},
    )
    setup_call(
        "climate",
        "set_temperature",
        ENTITY_IDS["climate"],
        {"temperature": 24},
    )

    api.wait_for_projection(
        ENTITY_IDS["living_room_light"],
        access_token,
        state="on",
        attributes={"brightness": 178, "color_temp_kelvin": 3000},
    )
    api.wait_for_projection(
        ENTITY_IDS["study_light"],
        access_token,
        state="on",
        attributes={"brightness": 166, "color_temp_kelvin": 3000},
    )
    api.wait_for_projection(
        ENTITY_IDS["cover"],
        access_token,
        state="open",
        attributes={"current_position": 80},
    )
    api.wait_for_projection(
        ENTITY_IDS["climate"],
        access_token,
        state="cool",
        attributes={"temperature": 24},
    )
    return {
        "classification": "direct_rest_state_normalization",
        "dispatches": calls,
        "included_in_sut_dispatch_count": False,
        "purpose": "setup_only",
    }


def mutate_target_state(
    api: HomeAssistantApi,
    access_token: str,
    case: TargetDriftCase,
) -> dict[str, object]:
    """Apply and observe one explicit out-of-band state-drift injection."""

    data = dict(case.mutation_service_data)
    if data.pop("entity_id", None) != case.entity_id:
        raise AcceptanceError("target-drift mutation entity does not match its case")
    status = _service_call(
        api,
        case.domain,
        case.mutation_service,
        case.entity_id,
        access_token,
        evidence_phase="external_fault_injection",
        extra=data,
    )
    expected = dict(case.expected_drifted_state)
    state = expected.pop("state", None)
    if not isinstance(state, str):
        raise AcceptanceError("target-drift expected state is missing")
    api.wait_for_projection(
        case.entity_id,
        access_token,
        state=state,
        attributes=expected,
    )
    return {
        "classification": "out_of_band_fault_injection",
        "data": dict(case.mutation_service_data),
        "domain": case.domain,
        "http_status": status,
        "included_in_sut_dispatch_count": False,
        "observed_path": f"/api/states/{case.entity_id}",
        "request_path": f"/api/services/{case.domain}/{case.mutation_service}",
        "service": case.mutation_service,
        "transport": "home_assistant_rest_api",
    }


def create_rest_adapter(base_url: str, token: str) -> HomeAssistantRESTAdapter:
    """Create the real SUT adapter with bounded loopback polling."""

    return HomeAssistantRESTAdapter(
        base_url,
        token,
        timeout_seconds=10,
        poll_seconds=15,
    )


def _scenario_clarification(
    case: SutCase,
    scenario: FrozenScenarioEvidence,
) -> tuple[str | None, DomuxInstruction | None]:
    if case.evidence_key.variant == "ambiguous":
        return scenario.clarification_answer, scenario.confirmed_instruction
    return None, None


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise AcceptanceError(f"{label} is not an object")
    return dict(value)


def _run_target_drift_case(
    adapter: HomeAssistantRESTAdapter,
    registry: EntityRegistry,
    store: PreparedActionStore,
    recorded_evidence: Mapping[DomuxEvidenceKey, RecordedDomuxEvidence],
    scenario_evidence: Mapping[str, FrozenScenarioEvidence],
    mutate_target: Callable[[TargetDriftCase], Mapping[str, object]],
) -> dict[str, Any]:
    """Prepare, mutate the real target out of band, and prove zero dispatch."""

    case = TARGET_DRIFT_CASE
    evidence = _evidence_for_case(recorded_evidence, case.evidence_key)
    scenario = _scenario_for_case(scenario_evidence, case)
    grounded = ground_domux_request(
        evidence.command,
        evidence.raw_output,
        registry,
    )
    if grounded.clarification.required:
        raise AcceptanceError("target-drift case unexpectedly requires clarification")
    resolved = resolve_unique_request(grounded, registry)
    if resolved.chosen.entity_id != case.entity_id:
        raise AcceptanceError("target-drift case resolved to an unexpected entity")

    prepared = store.prepare(
        actor_id="ha-acceptance-actor",
        session_id="ha-acceptance-session",
        grounded=grounded,
        registry=registry,
        adapter=adapter,
    )
    snapshot = store.snapshot(prepared.nonce)
    plan = _mapping(snapshot.get("plan"), "target-drift prepared plan")
    state_entity_ids = snapshot.get("state_entity_ids")
    prepared_state_digest = snapshot.get("state_digest")
    if (
        state_entity_ids != [case.entity_id]
        or not isinstance(prepared_state_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", prepared_state_digest) is None
    ):
        raise AcceptanceError("target-drift prepared state binding is unexpected")
    service_data = _mapping(
        plan.get("service_data"),
        "target-drift prepared service data",
    )
    if (
        prepared.entity_id != case.entity_id
        or plan.get("entity_id") != case.entity_id
        or plan.get("domain") != case.domain
        or plan.get("service") != case.expected_service
        or service_data != dict(case.expected_service_data)
    ):
        raise AcceptanceError("target-drift prepared action has an unexpected shape")

    before_binding = state_binding(
        adapter,
        registry,
        state_entity_ids,
        for_planning=True,
    )
    before_binding_digest = digest_json(before_binding)
    if before_binding_digest != prepared_state_digest:
        raise AcceptanceError(
            "target-drift planning state changed before the explicit mutation"
        )
    bound_state = controlled_projection(adapter.get_state(case.entity_id), case.domain)
    _assert_projection_subset(
        bound_state,
        case.expected_bound_state,
        label="target-drift bound state",
    )
    dispatch_count_before_mutation = len(adapter.sut_calls)
    mutation = dict(mutate_target(case))
    if len(adapter.sut_calls) != dispatch_count_before_mutation:
        raise AcceptanceError("out-of-band target mutation was counted as a SUT dispatch")
    drifted_state = controlled_projection(
        adapter.get_state(case.entity_id),
        case.domain,
    )
    _assert_projection_subset(
        drifted_state,
        case.expected_drifted_state,
        label="target-drift mutated state",
    )
    after_binding = state_binding(
        adapter,
        registry,
        state_entity_ids,
        for_planning=True,
    )
    after_binding_digest = digest_json(after_binding)
    if after_binding_digest == prepared_state_digest:
        raise AcceptanceError("target-drift mutation did not change the planning state")

    dispatch_count_before_commit = len(adapter.sut_calls)
    rejected = store.commit(
        prepared.confirmation(),
        registry=registry,
        adapter=adapter,
    )
    dispatch_delta = len(adapter.sut_calls) - dispatch_count_before_commit
    if (
        rejected.accepted
        or rejected.dispatched
        or rejected.acknowledged
        or rejected.outcome_unknown
        or rejected.reason != "state_changed"
        or rejected.status != "INVALIDATED"
        or dispatch_delta != 0
    ):
        raise AcceptanceError("target drift was not rejected with zero SUT dispatch")

    expected_mutation = {
        "classification": "out_of_band_fault_injection",
        "data": dict(case.mutation_service_data),
        "domain": case.domain,
        "http_status": 200,
        "included_in_sut_dispatch_count": False,
        "observed_path": f"/api/states/{case.entity_id}",
        "request_path": f"/api/services/{case.domain}/{case.mutation_service}",
        "service": case.mutation_service,
        "transport": "home_assistant_rest_api",
    }
    if mutation != expected_mutation:
        raise AcceptanceError("target-drift mutation evidence is unexpected")
    return {
        "binding": {
            "after_external_mutation_state_digest": after_binding_digest,
            "before_external_mutation_state_digest": before_binding_digest,
            "changed_after_external_mutation": True,
            "matched_before_external_mutation": True,
            "prepared_state_digest": prepared_state_digest,
        },
        "case": case.name,
        "controlled_after_external_mutation": drifted_state,
        "controlled_before_external_mutation": bound_state,
        "domain": case.domain,
        "domux_evidence": evidence.provenance(),
        "external_mutation": mutation,
        "grounding": {
            "candidate_ids": [
                candidate.entity_id for candidate in grounded.candidates
            ],
            "clarification_required": grounded.clarification.required,
            "resolution": "resolve_unique_request",
            "selected_entity_id": resolved.chosen.entity_id,
        },
        "ha_registry_profile": HA_REGISTRY_PROFILE,
        "outcome": "REJECTED_BEFORE_DISPATCH",
        "rejection": {
            "acknowledged": rejected.acknowledged,
            "accepted": rejected.accepted,
            "dispatched": rejected.dispatched,
            "outcome_unknown": rejected.outcome_unknown,
            "reason": rejected.reason,
            "status": rejected.status,
            "sut_dispatch_delta": dispatch_delta,
        },
        "service_shape": {
            "data": service_data,
            "domain": case.domain,
            "service": case.expected_service,
        },
        "scenario_provenance": scenario.provenance(
            variant=case.evidence_key.variant,
            ha_demo_entity_id=case.entity_id,
            ha_matching_candidate_count=len(grounded.candidates),
            used_for_resolution=False,
        ),
    }


def run_sut_cases(
    adapter: HomeAssistantRESTAdapter,
    *,
    recorded_evidence: Mapping[DomuxEvidenceKey, RecordedDomuxEvidence],
    scenario_evidence: Mapping[str, FrozenScenarioEvidence],
    mutate_target: Callable[[TargetDriftCase], Mapping[str, object]],
) -> dict[str, Any]:
    """Run grounding through one-time commit against the real REST adapter."""

    if not isinstance(adapter, HomeAssistantRESTAdapter):
        raise AcceptanceError("SUT adapter must be HomeAssistantRESTAdapter")
    if adapter.sut_calls:
        raise AcceptanceError("SUT adapter already contains dispatch history")
    validate_acceptance_evidence(recorded_evidence, scenario_evidence)

    registry = sut_registry()
    store = PreparedActionStore(ttl_seconds=30)
    reports: list[dict[str, Any]] = []
    for case in SUT_CASES:
        evidence = _evidence_for_case(recorded_evidence, case.evidence_key)
        scenario = _scenario_for_case(scenario_evidence, case)
        grounded = ground_domux_request(
            evidence.command,
            evidence.raw_output,
            registry,
        )
        clarification_answer, confirmed = _scenario_clarification(case, scenario)
        if clarification_answer is None:
            if confirmed is not None:
                raise AcceptanceError("unique SUT case unexpectedly has a confirmation")
            resolved = resolve_unique_request(grounded, registry)
            resolution = "resolve_unique_request"
        else:
            if confirmed is None:
                raise AcceptanceError("clarified SUT case is missing its confirmation")
            resolved = resolve_clarification_submission(
                grounded,
                answer=clarification_answer,
                confirmed_instruction=confirmed,
                registry=registry,
            )
            resolution = "resolve_clarification_submission"
        if resolved.chosen.entity_id != case.entity_id:
            raise AcceptanceError("grounding resolved to an unexpected entity")

        prepared = store.prepare(
            actor_id="ha-acceptance-actor",
            session_id="ha-acceptance-session",
            grounded=grounded,
            registry=registry,
            adapter=adapter,
            clarification_answer=clarification_answer,
            confirmed_instruction=confirmed,
        )
        snapshot = store.snapshot(prepared.nonce)
        plan = _mapping(snapshot.get("plan"), "prepared plan")
        service_data = _mapping(plan.get("service_data"), "prepared service data")
        expected_projection = _mapping(
            plan.get("expected_projection"), "prepared expected projection"
        )
        if (
            prepared.entity_id != case.entity_id
            or plan.get("entity_id") != case.entity_id
            or plan.get("domain") != case.domain
            or plan.get("service") != case.expected_service
            or service_data != dict(case.expected_service_data)
        ):
            raise AcceptanceError("prepared action has an unexpected service shape")

        dispatch_count_before = len(adapter.sut_calls)
        committed = store.commit(
            prepared.confirmation(),
            registry=registry,
            adapter=adapter,
        )
        if (
            not committed.accepted
            or not committed.dispatched
            or not committed.acknowledged
            or committed.outcome_unknown
            or committed.reason != "committed"
            or committed.status != "COMMITTED"
            or committed.before_registry_digest is None
            or committed.after_registry_digest is None
        ):
            raise AcceptanceError(
                f"SUT case {case.name} did not commit: "
                f"{committed.status}/{committed.reason}"
            )
        if len(adapter.sut_calls) != dispatch_count_before + 1:
            raise AcceptanceError("commit did not produce exactly one SUT dispatch")
        event = adapter.sut_calls[-1]
        if (
            event.get("kind") != "sut"
            or event.get("domain") != case.domain
            or event.get("service") != case.expected_service
            or event.get("data") != dict(case.expected_service_data)
            or event.get("acknowledged") is not True
            or event.get("outcome") != "observed"
        ):
            raise AcceptanceError("HomeAssistantRESTAdapter dispatch evidence is unexpected")

        controlled_before = _mapping(committed.before, "controlled before state")
        controlled_after = _mapping(committed.after, "controlled after state")
        _assert_projection_subset(
            controlled_before,
            case.expected_before,
            label="controlled before state",
        )
        _assert_projection_subset(
            controlled_after,
            case.expected_after,
            label="controlled after state",
        )
        if not projection_matches(controlled_after, expected_projection):
            raise AcceptanceError("committed state does not match the prepared postcondition")

        dispatch_count_before_replay = len(adapter.sut_calls)
        replay = store.commit(
            prepared.confirmation(),
            registry=registry,
            adapter=adapter,
        )
        replay_dispatch_delta = len(adapter.sut_calls) - dispatch_count_before_replay
        if (
            replay.accepted
            or replay.dispatched
            or replay.reason != "replayed_nonce"
            or replay_dispatch_delta != 0
        ):
            raise AcceptanceError("nonce replay was not rejected with zero dispatch")

        reports.append(
            {
                "case": case.name,
                "controlled_after": controlled_after,
                "controlled_before": controlled_before,
                "domain": case.domain,
                "domux_evidence": evidence.provenance(),
                "grounding": {
                    "candidate_ids": [
                        candidate.entity_id for candidate in grounded.candidates
                    ],
                    "clarification_required": grounded.clarification.required,
                    "resolution": resolution,
                    "selected_entity_id": resolved.chosen.entity_id,
                },
                "ha_registry_profile": HA_REGISTRY_PROFILE,
                "outcome": "COMMITTED",
                "postcondition": {
                    "all_registered_entities_exact": True,
                    "matched_prepared_projection": True,
                    "reason": committed.reason,
                    "status": committed.status,
                },
                "replay": {
                    "accepted": replay.accepted,
                    "dispatched": replay.dispatched,
                    "reason": replay.reason,
                    "sut_dispatch_delta": replay_dispatch_delta,
                },
                "service_shape": {
                    "data": service_data,
                    "domain": case.domain,
                    "service": case.expected_service,
                },
                "scenario_provenance": scenario.provenance(
                    variant=case.evidence_key.variant,
                    ha_demo_entity_id=case.entity_id,
                    ha_matching_candidate_count=len(grounded.candidates),
                    used_for_resolution=clarification_answer is not None,
                ),
            }
        )

    reports.append(
        _run_target_drift_case(
            adapter,
            registry,
            store,
            recorded_evidence,
            scenario_evidence,
            mutate_target,
        )
    )
    if len(adapter.sut_calls) != len(SUT_CASES):
        raise AcceptanceError("SUT dispatch count differs from committed case count")
    return {
        "adapter": "HomeAssistantRESTAdapter",
        "case_count": len(reports),
        "cases": reports,
        "classification": "clarify_commit_sut",
        "domux_evidence": {
            "artifact": V1_DOMUX_EVIDENCE_ARTIFACT,
            "artifact_sha256": V1_DOMUX_EVIDENCE_SHA256,
            "pair_count": len(recorded_evidence),
            "validation": "whole_artifact_and_per_field_sha256",
        },
        "pipeline": [
            "ground_domux_request",
            "resolve_clarification_submission_or_unique",
            "PreparedActionStore.prepare",
            "PreparedActionStore.commit",
            "HomeAssistantRESTAdapter.call_service",
        ],
        "external_fault_injection_count": 1,
        "rejected_before_dispatch_count": 1,
        "scenario_evidence": {
            "artifact": SCENARIO_EVIDENCE_ARTIFACT,
            "artifact_sha256": SCENARIO_EVIDENCE_SHA256,
            "case_count": len(scenario_evidence),
            "ha_registry_profile": HA_REGISTRY_PROFILE,
        },
        "successful_transition_count": len(SUT_CASES),
        "sut_dispatch_total": len(adapter.sut_calls),
    }


def generate_credentials() -> tuple[str, str]:
    """Generate one synthetic local-only account."""

    return f"acceptance_{secrets.token_hex(8)}", secrets.token_urlsafe(32)


def exercise_home_assistant(
    api: HomeAssistantApi,
    *,
    recorded_evidence: Mapping[DomuxEvidenceKey, RecordedDomuxEvidence] | None = None,
    scenario_evidence: Mapping[str, FrozenScenarioEvidence] | None = None,
    credential_factory: Callable[[], tuple[str, str]] = generate_credentials,
    rest_adapter_factory: Callable[
        [str, str], HomeAssistantRESTAdapter
    ] = create_rest_adapter,
) -> dict[str, Any]:
    """Complete onboarding, execute the real SUT pipeline, and revoke auth."""

    if recorded_evidence is None:
        recorded_evidence = load_recorded_domux_evidence()
    if scenario_evidence is None:
        scenario_evidence = load_frozen_scenario_evidence()
    validate_acceptance_evidence(recorded_evidence, scenario_evidence)
    readiness = api.wait_for_readiness()
    initial_steps = _onboarding_state(readiness.payload, expected_done=False)
    unauthenticated = api.request("GET", "/api/", expected=(401,))

    username, password = credential_factory()
    if not username or not password:
        raise AcceptanceError("credential factory returned an empty value")
    client_id = f"{api.base_url}/"
    users = api.request(
        "POST",
        "/api/onboarding/users",
        json_body={
            "name": "Domux Acceptance",
            "username": username,
            "password": password,
            "client_id": client_id,
            "language": "en",
        },
    )
    if not isinstance(users.payload, dict) or not isinstance(
        users.payload.get("auth_code"), str
    ):
        raise AcceptanceError("user onboarding response omitted its auth code")
    user_auth_code = users.payload["auth_code"]

    issued = api.request(
        "POST",
        "/auth/token",
        form_body={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": user_auth_code,
        },
    )
    if not isinstance(issued.payload, dict):
        raise AcceptanceError("token endpoint returned a non-object")
    access_token = issued.payload.get("access_token")
    refresh_token = issued.payload.get("refresh_token")
    token_type = issued.payload.get("token_type")
    expires_in = issued.payload.get("expires_in")
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise AcceptanceError("token endpoint omitted access or refresh token")
    if token_type != "Bearer" or expires_in != 1800:
        raise AcceptanceError("token endpoint returned an unexpected type or lifetime")

    authenticated = api.request("GET", "/api/", token=access_token)
    if not isinstance(authenticated.payload, dict) or authenticated.payload.get(
        "message"
    ) != "API running.":
        raise AcceptanceError("authenticated API health response is unexpected")

    core_config = api.request(
        "POST", "/api/onboarding/core_config", token=access_token, timeout=60
    )
    integration = api.request(
        "POST",
        "/api/onboarding/integration",
        json_body={
            "client_id": client_id,
            "redirect_uri": f"{api.base_url}/auth-callback",
        },
        token=access_token,
    )
    if not isinstance(integration.payload, dict) or not isinstance(
        integration.payload.get("auth_code"), str
    ):
        raise AcceptanceError("integration onboarding response omitted its auth code")
    integration_auth_code = integration.payload["auth_code"]
    analytics = api.request(
        "POST", "/api/onboarding/analytics", token=access_token
    )
    final = api.request("GET", "/api/onboarding")
    final_steps = _onboarding_state(final.payload, expected_done=True)

    api.wait_for_entities(ENTITY_IDS.values(), access_token)
    setup = normalize_setup_state(api, access_token)
    adapter = rest_adapter_factory(api.base_url, access_token)
    sut = run_sut_cases(
        adapter,
        recorded_evidence=recorded_evidence,
        scenario_evidence=scenario_evidence,
        mutate_target=lambda case: mutate_target_state(
            api,
            access_token,
            case,
        ),
    )
    expected_direct_calls = [
        _direct_service_event("setup", "light", "turn_on", "/api/services/light/turn_on", 200),
        _direct_service_event("setup", "light", "turn_on", "/api/services/light/turn_on", 200),
        _direct_service_event(
            "setup",
            "cover",
            "set_cover_position",
            "/api/services/cover/set_cover_position",
            200,
        ),
        _direct_service_event(
            "setup",
            "climate",
            "set_hvac_mode",
            "/api/services/climate/set_hvac_mode",
            200,
        ),
        _direct_service_event(
            "setup",
            "climate",
            "set_temperature",
            "/api/services/climate/set_temperature",
            200,
        ),
        _direct_service_event(
            "external_fault_injection",
            "light",
            "turn_on",
            "/api/services/light/turn_on",
            200,
        ),
    ]
    if api.direct_service_calls != expected_direct_calls:
        raise AcceptanceError("direct REST service-call ledger is unexpected")
    setup_direct_count = sum(
        event["phase"] == "setup" for event in api.direct_service_calls
    )
    external_count = sum(
        event["phase"] == "external_fault_injection"
        for event in api.direct_service_calls
    )
    sut_dispatch_count = len(adapter.sut_calls)
    if (
        setup_direct_count != len(setup["dispatches"])
        or external_count != sut["external_fault_injection_count"]
        or sut_dispatch_count != sut["sut_dispatch_total"]
    ):
        raise AcceptanceError("service-call phase accounting is inconsistent")
    service_call_accounting = {
        "direct_rest_events": [dict(event) for event in api.direct_service_calls],
        "external_fault_injection": external_count,
        "setup_direct_rest": setup_direct_count,
        "sut_dispatches": sut_dispatch_count,
        "total": len(api.direct_service_calls) + sut_dispatch_count,
    }

    revoked = api.request(
        "POST", "/auth/revoke", form_body={"token": refresh_token}
    )
    refresh_after_revoke = api.request(
        "POST",
        "/auth/token",
        form_body={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        expected=(400,),
    )

    result = {
        "auth": {
            "issue_http": issued.status,
            "refresh_after_revoke_http": refresh_after_revoke.status,
            "revoke_http": revoked.status,
            "token_type": token_type,
            "ttl_seconds": expires_in,
        },
        "health": {
            "authenticated_api_http": authenticated.status,
            "message": authenticated.payload["message"],
            "unauthenticated_api_http": unauthenticated.status,
        },
        "onboarding": {
            "final": final_steps,
            "initial": initial_steps,
            "requests": {
                "analytics_http": analytics.status,
                "core_config_http": core_config.status,
                "integration_http": integration.status,
                "users_http": users.status,
            },
        },
        "readiness": {
            "endpoint": "/api/onboarding",
            "http": readiness.status,
        },
        "phases": {
            "service_call_accounting": service_call_accounting,
            "setup": setup,
            "sut": sut,
            "teardown": {
                "classification": "credential_cleanup",
                "refresh_after_revoke_http": refresh_after_revoke.status,
                "refresh_revoke_http": revoked.status,
            },
        },
    }
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    forbidden = (
        username,
        password,
        user_auth_code,
        integration_auth_code,
        access_token,
        refresh_token,
        client_id,
        api.base_url,
    )
    if any(value and value in serialized for value in forbidden):
        raise AcceptanceError("acceptance result contains private runtime material")
    return result


def execute_acceptance(
    docker: DockerCli,
    *,
    recorded_evidence_loader: Callable[
        [], Mapping[DomuxEvidenceKey, RecordedDomuxEvidence]
    ] = load_recorded_domux_evidence,
    scenario_evidence_loader: Callable[
        [], Mapping[str, FrozenScenarioEvidence]
    ] = load_frozen_scenario_evidence,
    api_factory: Callable[[str], HomeAssistantApi] = HomeAssistantApi,
    credential_factory: Callable[[], tuple[str, str]] = generate_credentials,
    rest_adapter_factory: Callable[
        [str, str], HomeAssistantRESTAdapter
    ] = create_rest_adapter,
) -> dict[str, Any]:
    """Run acceptance and always clean exactly the resources owned by this run."""

    try:
        _verify_module_origins()
        bindings_before = capture_execution_bindings()
        recorded_evidence = recorded_evidence_loader()
        scenario_evidence = scenario_evidence_loader()
        validate_acceptance_evidence(recorded_evidence, scenario_evidence)
        runtime = docker.prepare()
        home_assistant = exercise_home_assistant(
            api_factory(runtime.base_url),
            recorded_evidence=recorded_evidence,
            scenario_evidence=scenario_evidence,
            credential_factory=credential_factory,
            rest_adapter_factory=rest_adapter_factory,
        )
        bindings_after = capture_execution_bindings()
        execution_source_bindings = finalize_execution_source_bindings(
            bindings_before,
            bindings_after,
        )
        return {
            "execution_source_bindings": execution_source_bindings,
            "home_assistant": home_assistant,
            "image": runtime.image,
            "isolation": {
                "container_count": 1,
                "cpu_limit": CPU_LIMIT,
                "memory_limit_bytes": MEMORY_LIMIT_BYTES,
                "named_volume_count": 1,
                "pids_limit": PIDS_LIMIT,
                "random_loopback_binding": True,
                "restart_policy": "no",
            },
            "schema_version": 4,
            "status": "passed",
        }
    finally:
        docker.cleanup()


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def atomic_write_json(output: Path, value: object) -> bytes:
    """Atomically publish one deterministic JSON artifact with mode 0600."""

    payload = canonical_json_bytes(value)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
        directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the pinned official Home Assistant acceptance check."
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="destination for the deterministic redacted JSON result",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    docker_factory: Callable[[], DockerCli] = DockerCli,
    api_factory: Callable[[str], HomeAssistantApi] = HomeAssistantApi,
    credential_factory: Callable[[], tuple[str, str]] = generate_credentials,
    rest_adapter_factory: Callable[
        [str, str], HomeAssistantRESTAdapter
    ] = create_rest_adapter,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = execute_acceptance(
            docker_factory(),
            api_factory=api_factory,
            credential_factory=credential_factory,
            rest_adapter_factory=rest_adapter_factory,
        )
        payload = atomic_write_json(args.output, result)
    except (AcceptanceError, OSError, ValueError) as exc:
        print(f"ha_acceptance failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("ha_acceptance failed: unexpected internal error", file=sys.stderr)
        return 1
    sys.stdout.write(payload.decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
