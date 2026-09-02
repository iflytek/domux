#!/usr/bin/env python3
"""Replay frozen Domux outputs through the pre-registered B0/B1/B2 protocol.

The evaluator never calls a model.  It consumes the immutable JSONL emitted by
``run_model.py``, keeps all 48 evaluation base IDs in every eligible
denominator, and creates a fresh in-memory adapter and prepared action for each
mutation trial.  The two output files are deterministic and intentionally omit
timestamps and local paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from clarify_commit import (
    ClarifyPrepareStore,
    DomuxInstruction,
    EntityRegistry,
    EntitySpec,
    GroundedRequest,
    GroundingError,
    InMemoryHAAdapter,
    ParseError,
    PreparedActionStore,
    SessionContext,
    altered_confirmation,
    build_plan,
    canonical_json,
    controlled_projection,
    digest_json,
    ground_domux_request,
    projection_matches,
    resolve_unique_request,
)


CASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET = CASE_DIR / "data" / "scenarios.jsonl"
DEFAULT_PROTOCOL = CASE_DIR / "data" / "protocol.json"
DEFAULT_FREEZE = CASE_DIR / "data" / "freeze.json"
DEFAULT_EVIDENCE = CASE_DIR / "evidence" / "domux_raw.jsonl"
DEFAULT_EVIDENCE_METADATA = CASE_DIR / "evidence" / "domux_metadata.json"

FORMAL_BASE_COUNT = 48
CONTEXT_BASE_COUNT = 12
REGISTERED_SNAPSHOT_MANIFEST_SHA256 = "5a13462b24fc9b00d132c42718e037bc42fc51a3c6752041998e085579f01416"
REGISTERED_SNAPSHOT_FILES = json.loads(
    (CASE_DIR / "data" / "snapshot_manifest.json").read_text(encoding="utf-8")
)["files"]
VARIANTS = ("clear", "ambiguous")
SLOTS = ("action", "device", "attribute", "value", "unit", "room", "floor")

B0 = "B0_unique_or_abstain"
B1 = "B1_clarify_and_prepare"
B2 = "B2_clarify_and_commit"
ARMS = (B0, B1, B2)

MUTATIONS = (
    "clean",
    "replay",
    "expiry",
    "target_drift",
    "session_swap",
    "plan_swap",
    "candidate_change",
    "context_state_change",
    "unrelated_state_change",
)
UNIVERSAL_GUARD_MUTATIONS = (
    "replay",
    "expiry",
    "target_drift",
    "session_swap",
    "plan_swap",
    "candidate_change",
    "unrelated_state_change",
)
B1_INTENTIONAL_GAPS = frozenset({
    "replay",
    "expiry",
    "target_drift",
    "candidate_change",
    "context_state_change",
})
BLOCKING_MUTATIONS = frozenset({
    "expiry",
    "target_drift",
    "session_swap",
    "plan_swap",
    "candidate_change",
    "context_state_change",
})


class EvaluationInputError(ValueError):
    """Frozen inputs do not satisfy the pre-registered evaluation contract."""


class LogicalClock:
    """A deterministic clock; expiry trials advance it without sleeping."""

    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


@dataclass(frozen=True)
class ProbeOutcome:
    base_id: str
    variant: str
    evidence_status: str
    disposition: str
    reason: str
    instructions: tuple[DomuxInstruction, ...]
    candidate_ids: tuple[str, ...]
    grounded: GroundedRequest | None
    raw_output: str
    raw_output_sha256: str
    latency_ms: float | None
    error_type: str | None = None

    @property
    def is_unique(self) -> bool:
        return self.disposition == "unique"

    @property
    def is_clarification(self) -> bool:
        return self.disposition == "clarify"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_float(value: float) -> float:
    """Keep useful precision while making serialized statistics easy to diff."""

    return float(f"{value:.15g}")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"cannot read valid {label} JSON") from exc
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{label} must be a JSON object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvaluationInputError(f"cannot read {label} JSONL") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationInputError(f"{label} line {line_number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise EvaluationInputError(f"{label} line {line_number} is not an object")
        rows.append(value)
    return rows


def _canonical_lines(rows: Iterable[Mapping[str, object]]) -> bytes:
    return "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8")


def _instruction(fields: Mapping[str, object]) -> DomuxInstruction:
    try:
        values = [str(fields[slot]) for slot in SLOTS]
    except KeyError as exc:
        raise EvaluationInputError(f"confirmed instruction is missing {exc.args[0]}") from exc
    return DomuxInstruction.from_fields(values)


def _registry(row: Mapping[str, object]) -> EntityRegistry:
    inventory = row.get("inventory")
    if not isinstance(inventory, list):
        raise EvaluationInputError("scenario inventory must be a list")
    entities: list[EntitySpec] = []
    for item in inventory:
        if not isinstance(item, dict):
            raise EvaluationInputError("inventory entries must be objects")
        try:
            entities.append(EntitySpec(
                entity_id=str(item["entity_id"]),
                domain=str(item["domain"]),
                device=str(item["device"]),
                room=str(item["room"]),
                floor=str(item["floor"]),
                aliases=tuple(str(alias) for alias in item.get("aliases", ())),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationInputError("invalid inventory entry") from exc
    return EntityRegistry(entities)


def _context(row: Mapping[str, object]) -> SessionContext:
    value = row.get("session_context")
    if not isinstance(value, dict):
        raise EvaluationInputError("session_context must be an object")
    recent = value.get("last_referenced_entities", ())
    if not isinstance(recent, list):
        raise EvaluationInputError("last_referenced_entities must be a list")
    return SessionContext(tuple(str(entity_id) for entity_id in recent))


def _projection_snapshot(
    adapter: InMemoryHAAdapter,
    registry: EntityRegistry,
) -> dict[str, dict[str, object]]:
    return {
        entity.entity_id: controlled_projection(adapter.get_state(entity.entity_id), entity.domain)
        for entity in registry.entities
    }


def _fixture_projection(row: Mapping[str, object], registry: EntityRegistry) -> dict[str, dict[str, object]]:
    states = row.get("initial_states")
    if not isinstance(states, dict):
        raise EvaluationInputError("initial_states must be an object")
    return {
        entity.entity_id: controlled_projection(states[entity.entity_id], entity.domain)
        for entity in registry.entities
    }


def _validate_protocol(protocol: Mapping[str, object]) -> None:
    try:
        population = protocol["analysis_population"]
        mutations = protocol["mutations"]
        arms = protocol["arms"]
        model = protocol["model"]
    except KeyError as exc:
        raise EvaluationInputError(f"protocol is missing {exc.args[0]}") from exc
    if not isinstance(population, dict) or population.get("evaluation_bases") != FORMAL_BASE_COUNT:
        raise EvaluationInputError("formal evaluation denominator must be exactly 48 base IDs")
    if population.get("unit") != "base_id" or population.get("paired_probes_per_base") != list(VARIANTS):
        raise EvaluationInputError("protocol must retain the paired base_id analysis unit")
    if not isinstance(arms, dict) or tuple(arms) != ARMS:
        raise EvaluationInputError("protocol B0/B1/B2 arm definitions changed")
    if not isinstance(mutations, dict) or tuple(mutations) != MUTATIONS:
        raise EvaluationInputError("protocol mutation declarations changed")
    if not isinstance(model, dict) or model.get("snapshot_files_sha256") != REGISTERED_SNAPSHOT_MANIFEST_SHA256:
        raise EvaluationInputError("protocol model snapshot manifest changed")
    if model.get("snapshot_manifest_file") != "snapshot_manifest.json":
        raise EvaluationInputError("protocol snapshot manifest path changed")
    for mutation in MUTATIONS:
        expected = CONTEXT_BASE_COUNT if mutation == "context_state_change" else FORMAL_BASE_COUNT
        value = mutations[mutation]
        if not isinstance(value, dict) or value.get("eligible_bases") != expected:
            raise EvaluationInputError(f"protocol denominator changed for {mutation}")
    b1 = arms[B1]
    b2 = arms[B2]
    if not isinstance(b1, dict) or not isinstance(b2, dict):
        raise EvaluationInputError("protocol arm definitions must be objects")
    if any(b1.get(key) is not False for key in ("candidate_revalidation", "state_revalidation", "ttl", "one_time_nonce")):
        raise EvaluationInputError("B1 must omit only the pre-registered temporal guards")
    if any(b2.get(key) is not True for key in ("candidate_revalidation", "state_revalidation", "one_time_nonce")):
        raise EvaluationInputError("B2 guard declarations changed")
    if b2.get("ttl_seconds") != 30:
        raise EvaluationInputError("B2 TTL must remain 30 seconds")


def _validate_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    # Preserve file order here because the freeze manifest hashes the exact
    # canonical evaluation sequence.  Trial output is sorted separately.
    eval_rows = [row for row in rows if row.get("split") == "eval"]
    if len(eval_rows) != FORMAL_BASE_COUNT:
        raise EvaluationInputError("dataset must contain exactly 48 evaluation bases")
    base_ids = [str(row.get("base_id", "")) for row in eval_rows]
    if any(not base_id for base_id in base_ids) or len(set(base_ids)) != FORMAL_BASE_COUNT:
        raise EvaluationInputError("evaluation base IDs must be non-empty and unique")
    category_counts = Counter(str(row.get("category")) for row in eval_rows)
    if category_counts != Counter({
        "duplicate_entity": 12,
        "missing_slot": 12,
        "context_reference": 12,
        "negation_correction": 12,
    }):
        raise EvaluationInputError("evaluation strata must remain balanced at 12 bases each")
    for row in eval_rows:
        base_id = str(row["base_id"])
        for key in ("clear_command", "ambiguous_command", "confirmed_instruction", "expected_target_entity", "expected_delta"):
            if key not in row:
                raise EvaluationInputError(f"scenario {base_id} is missing {key}")
        registry = _registry(row)
        target = str(row["expected_target_entity"])
        registry.get(target)
        candidate_ids = row.get("candidate_entity_ids")
        if not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= 3:
            raise EvaluationInputError(f"scenario {base_id} candidate set is outside one to three")
        if target not in candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
            raise EvaluationInputError(f"scenario {base_id} has an invalid target/candidate relation")
        if set(candidate_ids) - {entity.entity_id for entity in registry.entities}:
            raise EvaluationInputError(f"scenario {base_id} candidate is outside the registry")
        if str(row.get("unrelated_entity_id")) in candidate_ids:
            raise EvaluationInputError(f"scenario {base_id} unrelated entity is candidate-bound")
        states = row.get("initial_states")
        if not isinstance(states, dict) or set(states) != {entity.entity_id for entity in registry.entities}:
            raise EvaluationInputError(f"scenario {base_id} state fixture is not closed")
        expected_delta = row["expected_delta"]
        if not isinstance(expected_delta, dict) or expected_delta.get("entity_id") != target:
            raise EvaluationInputError(f"scenario {base_id} expected delta target changed")
        if expected_delta.get("before") != states[target]:
            raise EvaluationInputError(f"scenario {base_id} expected before-state changed")
    return eval_rows


def _validate_freeze(
    freeze: Mapping[str, object],
    dataset_path: Path,
    protocol_path: Path,
    eval_rows: Sequence[Mapping[str, object]],
) -> None:
    snapshot_manifest_path = protocol_path.parent / "snapshot_manifest.json"
    checks = {
        "full_sha256": _sha256_file(dataset_path),
        "protocol_sha256": _sha256_file(protocol_path),
        "evaluation_sha256": _sha256_bytes(_canonical_lines(eval_rows)),
        "snapshot_manifest_sha256": _sha256_file(snapshot_manifest_path),
    }
    for field, observed in checks.items():
        if freeze.get(field) != observed:
            raise EvaluationInputError(f"frozen input hash mismatch: {field}")
    if freeze.get("evaluation_count") != FORMAL_BASE_COUNT:
        raise EvaluationInputError("freeze evaluation denominator is not 48")


def _load_evidence(
    evidence_rows: Sequence[dict[str, Any]],
    eval_rows: Sequence[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    expected: dict[tuple[str, str], str] = {}
    for row in eval_rows:
        for variant in VARIANTS:
            expected[(str(row["base_id"]), variant)] = str(row[f"{variant}_command"])

    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for item in evidence_rows:
        key = (str(item.get("base_id", "")), str(item.get("variant", "")))
        if key not in expected:
            raise EvaluationInputError("frozen evidence contains an unknown base/variant")
        if key in indexed:
            raise EvaluationInputError("frozen evidence contains a duplicate base/variant")
        command = item.get("command")
        if command != expected[key]:
            raise EvaluationInputError("frozen evidence command differs from the frozen dataset")
        if "query_sha256" in item and item["query_sha256"] != _sha256_text(str(command)):
            raise EvaluationInputError("frozen evidence query hash mismatch")
        status = item.get("status")
        if status not in {"ok", "error"}:
            raise EvaluationInputError("frozen evidence status must be ok or error")
        raw_output = item.get("raw_output", "")
        if not isinstance(raw_output, str):
            raise EvaluationInputError("frozen raw_output must be a string")
        if status == "ok" and "raw_output" not in item:
            raise EvaluationInputError("successful frozen evidence is missing raw_output")
        if "raw_output_sha256" in item and item["raw_output_sha256"] != _sha256_text(raw_output):
            raise EvaluationInputError("frozen evidence raw-output hash mismatch")
        latency = item.get("latency_ms")
        if latency is not None and (not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0):
            raise EvaluationInputError("frozen evidence latency must be a non-negative number or null")
        indexed[key] = item
    missing = sorted(set(expected) - set(indexed))
    if missing:
        raise EvaluationInputError(
            f"frozen evidence is incomplete: {len(missing)} of 96 paired probes missing"
        )
    if len(indexed) != FORMAL_BASE_COUNT * len(VARIANTS):
        raise EvaluationInputError("frozen evidence must contain exactly 96 paired probes")
    return indexed


def _validated_model_run(
    metadata: Mapping[str, object],
    protocol: Mapping[str, object],
    dataset_sha256: str,
    evidence_sha256: str,
    evidence_failure_count: int = 0,
) -> dict[str, object]:
    registered = protocol["model"]
    result: dict[str, object] = {
        "registered_configuration": {
            "model_id": registered["id"],
            "revision": registered["revision"],
            "precision": registered["primary_precision"],
            "do_sample": registered["do_sample"],
            "temperature": registered["temperature"],
            "max_new_tokens": registered["max_new_tokens"],
            "seed": registered["seed"],
            "warmup_count": registered["warmup"]["count"],
            "warmup_source": registered["warmup"]["source"],
        },
        "metadata_verified": True,
        "observed_environment": None,
    }
    required_equal = {
        "model_id": registered["id"],
        "tested_revision": registered["revision"],
        "precision": registered["primary_precision"],
        "dataset_sha256": dataset_sha256,
        "sample_count": FORMAL_BASE_COUNT * len(VARIANTS),
        "base_count": FORMAL_BASE_COUNT,
        "warmup_count": registered["warmup"]["count"],
        "raw_evidence_sha256": evidence_sha256,
        "snapshot_manifest_sha256": REGISTERED_SNAPSHOT_MANIFEST_SHA256,
        "snapshot_revision_verified": True,
        "runner_sha256": _sha256_file(CASE_DIR / "run_model.py"),
        "grounding_policy_sha256": _sha256_file(CASE_DIR / "clarify_commit.py"),
        "evaluator_sha256": _sha256_file(Path(__file__).resolve()),
        "run_mode": "formal",
        "selective_reruns": 0,
        "sample_failures": evidence_failure_count,
    }
    for field, expected in required_equal.items():
        if metadata.get(field) != expected:
            raise EvaluationInputError(f"model-run metadata mismatch: {field}")
    generation = metadata.get("generation")
    if not isinstance(generation, dict):
        raise EvaluationInputError("model-run metadata is missing generation settings")
    for field in ("do_sample", "max_new_tokens", "seed"):
        if generation.get(field) != registered[field]:
            raise EvaluationInputError(f"model-run generation mismatch: {field}")
    # run_model.py records temperature=0 for deterministic decoding even though
    # transformers ignores it when sampling is disabled.
    if generation.get("temperature") != registered["temperature"]:
        raise EvaluationInputError("model-run generation mismatch: temperature")
    if metadata.get("offline_inference") is not True:
        raise EvaluationInputError("formal evidence must be recorded in offline inference mode")
    snapshot_files = metadata.get("snapshot_files")
    if not isinstance(snapshot_files, list) or len(snapshot_files) != 13:
        raise EvaluationInputError("model-run metadata has an invalid snapshot manifest")
    if any(
        not isinstance(item, dict)
        or set(item) != {"name", "size_bytes", "sha256", "hub_etag"}
        or not isinstance(item.get("name"), str)
        or "/" in str(item.get("name"))
        or "\\" in str(item.get("name"))
        for item in snapshot_files
    ):
        raise EvaluationInputError("model-run metadata has malformed snapshot entries")
    if [item["name"] for item in snapshot_files] != sorted(item["name"] for item in snapshot_files):
        raise EvaluationInputError("model-run snapshot manifest is not canonically ordered")
    if snapshot_files != REGISTERED_SNAPSHOT_FILES:
        raise EvaluationInputError("model-run snapshot entries differ from the registered revision")
    observed_manifest_sha256 = _sha256_text(canonical_json(snapshot_files))
    if observed_manifest_sha256 != REGISTERED_SNAPSHOT_MANIFEST_SHA256:
        raise EvaluationInputError("model-run snapshot files do not match the registered manifest")

    safe_fields = (
        "artifact_type",
        "runtime",
        "python_version",
        "platform",
        "torch_version",
        "transformers_version",
        "huggingface_hub_version",
        "accelerate_version",
        "bitsandbytes_version",
        "precision",
        "compute_dtype",
        "gpu",
        "gpu_total_memory_bytes",
        "visible_gpu_count",
        "sample_count",
        "base_count",
        "warmup_count",
        "warmup_source",
        "generation",
        "offline_inference",
        "run_mode",
        "selective_reruns",
        "sample_failures",
    )
    result["observed_environment"] = {
        field: metadata.get(field) for field in safe_fields
    }
    result["evidence_binding"] = {
        "metadata_raw_evidence_sha256": metadata["raw_evidence_sha256"],
        "observed_raw_evidence_sha256": evidence_sha256,
        "match": True,
    }
    result["code_binding"] = {
        "runner_sha256": metadata["runner_sha256"],
        "grounding_policy_sha256": metadata["grounding_policy_sha256"],
        "evaluator_sha256": metadata["evaluator_sha256"],
        "match": True,
    }
    return result


def analyze_probe(row: Mapping[str, object], variant: str, evidence: Mapping[str, object]) -> ProbeOutcome:
    """Classify a frozen model output without consulting the gold ambiguity label."""

    base_id = str(row["base_id"])
    raw_output = str(evidence.get("raw_output", ""))
    raw_hash = _sha256_text(raw_output)
    latency_value = evidence.get("latency_ms")
    latency = float(latency_value) if isinstance(latency_value, (int, float)) and not isinstance(latency_value, bool) else None
    if evidence.get("status") != "ok":
        return ProbeOutcome(
            base_id=base_id,
            variant=variant,
            evidence_status="error",
            disposition="unavailable",
            reason="inference_error",
            instructions=(),
            candidate_ids=(),
            grounded=None,
            raw_output=raw_output,
            raw_output_sha256=raw_hash,
            latency_ms=latency,
            error_type=str(evidence.get("error_type") or "InferenceError"),
        )

    try:
        registry = _registry(row)
        context = _context(row)
        grounded = ground_domux_request(
            str(row[f"{variant}_command"]),
            raw_output,
            registry,
            context,
        )
    except ParseError:
        return ProbeOutcome(
            base_id=base_id,
            variant=variant,
            evidence_status="ok",
            disposition="safe_abstain",
            reason="malformed_model_output",
            instructions=(),
            candidate_ids=(),
            grounded=None,
            raw_output=raw_output,
            raw_output_sha256=raw_hash,
            latency_ms=latency,
            error_type="ParseError",
        )
    candidate_ids = tuple(entity.entity_id for entity in grounded.candidates)
    disposition = "clarify" if grounded.clarification.required else "unique"
    reason = grounded.clarification.reason
    if disposition == "unique":
        try:
            build_plan(
                grounded.source_instructions[0],
                grounded.candidates[0],
                row["initial_states"][grounded.candidates[0].entity_id],
            )
        except (GroundingError, KeyError, TypeError, ValueError):
            disposition = "safe_abstain"
            reason = "non_executable_instruction"
    return ProbeOutcome(
        base_id=base_id,
        variant=variant,
        evidence_status="ok",
        disposition=disposition,
        reason=reason,
        instructions=grounded.source_instructions,
        candidate_ids=candidate_ids,
        grounded=grounded,
        raw_output=raw_output,
        raw_output_sha256=raw_hash,
        latency_ms=latency,
    )


def _candidate_coverage(row: Mapping[str, object], outcome: ProbeOutcome) -> bool:
    return (
        str(row["expected_target_entity"]) in outcome.candidate_ids
        and 1 <= len(outcome.candidate_ids) <= 3
    )


def _expected_projection(row: Mapping[str, object], registry: EntityRegistry, which: str) -> dict[str, object]:
    target = str(row["expected_target_entity"])
    entity = registry.get(target)
    return controlled_projection(row["expected_delta"][which], entity.domain)


def _wrong_target_transition(
    target: str,
    before: Mapping[str, Mapping[str, object]],
    after: Mapping[str, Mapping[str, object]],
    calls: Sequence[Mapping[str, object]],
) -> bool:
    if any(str(call.get("data", {}).get("entity_id")) != target for call in calls):
        return True
    return any(before[entity_id] != after[entity_id] for entity_id in before if entity_id != target)


def _exact_delta(
    row: Mapping[str, object],
    registry: EntityRegistry,
    before: Mapping[str, Mapping[str, object]],
    after: Mapping[str, Mapping[str, object]],
    calls: Sequence[Mapping[str, object]],
) -> bool:
    target = str(row["expected_target_entity"])
    return (
        before[target] == _expected_projection(row, registry, "before")
        and after[target] == _expected_projection(row, registry, "after")
        and len(calls) == 1
        and str(calls[0].get("data", {}).get("entity_id")) == target
        and not _wrong_target_transition(target, before, after, calls)
    )


def _base_trial(row: Mapping[str, object], arm: str, mutation: str, outcome: ProbeOutcome) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "execution_trial",
        "base_id": str(row["base_id"]),
        "category": str(row["category"]),
        "arm": arm,
        "mutation": mutation,
        "evidence_status": outcome.evidence_status,
        "input": str(row["ambiguous_command"]),
        "raw_output": outcome.raw_output,
        "raw_output_sha256": outcome.raw_output_sha256,
        "language_disposition": outcome.disposition,
        "reset_verified": False,
        "preconfirm_sut_calls": 0,
        "setup_calls": 0,
        "sut_calls": 0,
        "accepted": None,
        "dispatched": None,
        "commit_reason": None,
        "exact_delta_success": False,
        "wrong_target_transition": False,
        "oracle_pass": False,
        "setup_failure": None,
        "interpretation": "unclassified",
    }


def _trial_interpretation(trial: Mapping[str, object]) -> str:
    if trial.get("oracle_pass") is True:
        return "pass"
    arm = trial["arm"]
    mutation = trial["mutation"]
    if (
        arm == B1
        and mutation in B1_INTENTIONAL_GAPS
        and trial.get("setup_failure") is None
        and trial.get("accepted") is True
        and trial.get("dispatched") is True
    ):
        return "expected_b1_guard_limitation"
    if arm == B2:
        return "unexpected_b2_suite_failure"
    return "unexpected_baseline_failure"


def _prepare_trial(
    row: Mapping[str, object],
    outcome: ProbeOutcome,
    arm: str,
    mutation: str,
    ttl_seconds: float,
) -> tuple[
    InMemoryHAAdapter,
    EntityRegistry,
    PreparedActionStore,
    Any,
    LogicalClock,
    dict[str, dict[str, object]],
]:
    if outcome.evidence_status != "ok":
        raise GroundingError("inference_error")
    if not outcome.is_clarification:
        raise GroundingError("clarification_not_triggered")
    if not _candidate_coverage(row, outcome):
        raise GroundingError("candidate_coverage_failure")
    if outcome.grounded is None:
        raise GroundingError("grounded_request_missing")

    registry = _registry(row)
    adapter = InMemoryHAAdapter(row["initial_states"])
    expected_initial = _fixture_projection(row, registry)
    observed_initial = _projection_snapshot(adapter, registry)
    if observed_initial != expected_initial:
        raise GroundingError("reset_projection_mismatch")

    instruction = _instruction(row["confirmed_instruction"])
    target = str(row["expected_target_entity"])
    answer = row.get("clarification_answer")
    if not isinstance(answer, str) or not answer.strip():
        raise GroundingError("clarification_answer_missing")

    clock = LogicalClock()
    nonce = f"{arm}:{row['base_id']}:{mutation}"
    store_type = ClarifyPrepareStore if arm == B1 else PreparedActionStore
    store = store_type(
        ttl_seconds=ttl_seconds,
        clock=clock,
        nonce_factory=lambda: nonce,
    )
    state_dependencies = row.get("state_dependencies", ())
    if not isinstance(state_dependencies, list):
        raise GroundingError("invalid_state_dependencies")
    action = store.prepare(
        actor_id="actor-a",
        session_id="session-a",
        grounded=outcome.grounded,
        registry=registry,
        adapter=adapter,
        clarification_answer=answer,
        confirmed_instruction=instruction,
        state_dependencies=tuple(str(entity_id) for entity_id in state_dependencies),
    )
    snapshot = store.snapshot(action.nonce)
    plan = snapshot["plan"]
    if (
        action.entity_id != target
        or plan["entity_id"] != target
        or not projection_matches(
            _expected_projection(row, registry, "after"),
            plan["expected_projection"],
        )
    ):
        raise GroundingError("confirmed_plan_delta_mismatch")
    if adapter.sut_calls:
        raise GroundingError("preconfirm_dispatch")
    return adapter, registry, store, action, clock, observed_initial


def _run_prepared_trial(
    row: Mapping[str, object],
    outcome: ProbeOutcome,
    arm: str,
    mutation: str,
    ttl_seconds: float,
) -> dict[str, object]:
    trial = _base_trial(row, arm, mutation, outcome)
    try:
        adapter, registry, store, action, clock, initial = _prepare_trial(
            row, outcome, arm, mutation, ttl_seconds,
        )
    except (EvaluationInputError, GroundingError, KeyError, TypeError, ValueError) as exc:
        reason = str(exc)
        allowed = {
            "inference_error",
            "clarification_not_triggered",
            "candidate_coverage_failure",
            "grounded_request_missing",
            "reset_projection_mismatch",
            "clarification_answer_missing",
            "confirmed_plan_delta_mismatch",
            "preconfirm_dispatch",
            "invalid_state_dependencies",
        }
        trial["setup_failure"] = (
            reason
            if reason in allowed or isinstance(exc, (EvaluationInputError, GroundingError))
            else type(exc).__name__
        )
        trial["interpretation"] = _trial_interpretation(trial)
        return trial

    trial["reset_verified"] = True
    trial["initial_projection_sha256"] = digest_json(initial)
    trial["request_digest"] = action.request_digest
    trial["clarification_digest"] = action.clarification_digest
    target = str(row["expected_target_entity"])
    active_registry = registry
    confirmation = action.confirmation()

    if mutation == "expiry":
        clock.value = action.expires_at + 1.0
    elif mutation == "target_drift":
        adapter.mutate_state_for_setup(target)
    elif mutation == "session_swap":
        confirmation = altered_confirmation(confirmation, session_id="session-b")
    elif mutation == "plan_swap":
        different_digest = digest_json({"base_id": row["base_id"], "mutation": mutation})
        if different_digest == confirmation.plan_digest:
            raise AssertionError("deterministic plan-swap digest collision")
        confirmation = altered_confirmation(confirmation, plan_digest=different_digest)
    elif mutation == "candidate_change":
        candidate_ids = store.snapshot(action.nonce)["candidate_ids"]
        entity = registry.get(candidate_ids[0])
        active_registry = registry.with_replacement(replace(
            entity,
            aliases=tuple(entity.aliases) + ("evaluation-metadata-change",),
        ))
    elif mutation == "context_state_change":
        dependencies = tuple(str(entity_id) for entity_id in row.get("state_dependencies", ()))
        changed = next((entity_id for entity_id in dependencies if entity_id != target), None)
        if changed is None:
            trial["setup_failure"] = "missing_non_target_context_dependency"
            trial["setup_calls"] = len(adapter.setup_calls)
            trial["interpretation"] = _trial_interpretation(trial)
            return trial
        adapter.mutate_state_for_setup(changed)
    elif mutation == "unrelated_state_change":
        adapter.mutate_state_for_setup(str(row["unrelated_entity_id"]))
    elif mutation not in {"clean", "replay"}:
        raise AssertionError(f"unhandled mutation: {mutation}")

    trial["preconfirm_sut_calls"] = len(adapter.sut_calls)
    before = _projection_snapshot(adapter, active_registry)

    if mutation == "replay":
        first = store.commit(confirmation, registry=active_registry, adapter=adapter)
        after_first = _projection_snapshot(adapter, active_registry)
        first_calls = len(adapter.sut_calls)
        first_exact = (
            first.accepted
            and first.dispatched
            and first.reason == "committed"
            and _exact_delta(row, active_registry, before, after_first, adapter.sut_calls)
        )
        result = store.commit(confirmation, registry=active_registry, adapter=adapter)
        after = _projection_snapshot(adapter, active_registry)
        additional_calls = len(adapter.sut_calls) - first_calls
        oracle_pass = bool(
            first_exact
            and not result.accepted
            and not result.dispatched
            and result.reason == "replayed_nonce"
            and additional_calls == 0
            and after == after_first
        )
        trial["first_commit_exact_delta"] = bool(first_exact)
        trial["replay_additional_sut_calls"] = additional_calls
        trial["exact_delta_success"] = bool(first_exact)
    else:
        result = store.commit(confirmation, registry=active_registry, adapter=adapter)
        after = _projection_snapshot(adapter, active_registry)
        if mutation in BLOCKING_MUTATIONS:
            expected_reason = {
                "expiry": "expired",
                "target_drift": "state_changed",
                "session_swap": "session_mismatch",
                "plan_swap": "plan_mismatch",
                "candidate_change": "candidate_set_changed",
                "context_state_change": "state_changed",
            }[mutation]
            oracle_pass = bool(
                not result.accepted
                and not result.dispatched
                and result.reason == expected_reason
                and len(adapter.sut_calls) == 0
                and after == before
            )
        else:
            exact = _exact_delta(row, active_registry, before, after, adapter.sut_calls)
            oracle_pass = bool(
                result.accepted
                and result.dispatched
                and result.reason == "committed"
                and exact
            )
            trial["exact_delta_success"] = exact

    trial.update({
        "setup_calls": len(adapter.setup_calls),
        "sut_calls": len(adapter.sut_calls),
        "accepted": result.accepted,
        "dispatched": result.dispatched,
        "commit_reason": result.reason,
        "wrong_target_transition": _wrong_target_transition(
            target, before, after, adapter.sut_calls,
        ),
        "oracle_pass": oracle_pass,
    })
    trial["interpretation"] = _trial_interpretation(trial)
    return trial


def _run_b0_trial(row: Mapping[str, object], outcome: ProbeOutcome) -> dict[str, object]:
    """Run the strong no-clarification baseline: execute only a unique plan."""

    trial = _base_trial(row, B0, "clean", outcome)
    registry = _registry(row)
    adapter = InMemoryHAAdapter(row["initial_states"])
    before = _projection_snapshot(adapter, registry)
    trial["reset_verified"] = before == _fixture_projection(row, registry)
    trial["initial_projection_sha256"] = digest_json(before)
    if not outcome.is_unique:
        valid_abstention = outcome.evidence_status == "ok"
        trial["safe_abstention"] = valid_abstention
        trial["oracle_pass"] = valid_abstention
        trial["setup_failure"] = "inference_error" if outcome.evidence_status != "ok" else None
        trial["interpretation"] = (
            "expected_b0_safe_abstention" if outcome.evidence_status == "ok"
            else "unexpected_baseline_failure"
        )
        return trial

    try:
        if outcome.grounded is None:
            raise GroundingError("grounded request missing")
        resolved = resolve_unique_request(outcome.grounded, registry)
        entity = resolved.chosen
        plan = build_plan(
            resolved.confirmed_instruction,
            entity,
            adapter.get_state(entity.entity_id),
        )
        receipt = adapter.call_service(plan.domain, plan.service, plan.service_data)
        postcondition = projection_matches(
            controlled_projection(receipt.after, plan.domain),
            plan.expected_projection,
        )
    except (GroundingError, KeyError, TypeError, ValueError):
        trial["setup_failure"] = "direct_execution_failure"
        trial["interpretation"] = "unexpected_baseline_failure"
        return trial

    after = _projection_snapshot(adapter, registry)
    exact = _exact_delta(row, registry, before, after, adapter.sut_calls)
    target = str(row["expected_target_entity"])
    trial.update({
        "safe_abstention": False,
        "sut_calls": len(adapter.sut_calls),
        "accepted": True,
        "dispatched": True,
        "commit_reason": "direct_unique_dispatch" if postcondition else "postcondition_mismatch",
        "exact_delta_success": exact and postcondition,
        "wrong_target_transition": _wrong_target_transition(target, before, after, adapter.sut_calls),
        "oracle_pass": False,
        "interpretation": "b0_unclarified_dispatch_on_gold_ambiguous_probe",
    })
    return trial


def wilson_interval(successes: int, denominator: int, z: float = 1.959963984540054) -> dict[str, float]:
    if denominator <= 0 or not 0 <= successes <= denominator:
        raise ValueError("Wilson inputs require 0 <= successes <= a positive denominator")
    proportion = successes / denominator
    z2 = z * z
    scale = 1.0 + z2 / denominator
    center = (proportion + z2 / (2.0 * denominator)) / scale
    half = z / scale * math.sqrt(
        proportion * (1.0 - proportion) / denominator + z2 / (4.0 * denominator * denominator)
    )
    lower = 0.0 if successes == 0 else max(0.0, center - half)
    upper = 1.0 if successes == denominator else min(1.0, center + half)
    return {
        "lower": _stable_float(lower),
        "upper": _stable_float(upper),
    }


def binary_metric(values: Sequence[bool]) -> dict[str, object]:
    successes = sum(bool(value) for value in values)
    denominator = len(values)
    if denominator == 0:
        raise ValueError("binary metric cannot have an empty denominator")
    return {
        "successes": successes,
        "denominator": denominator,
        "rate": _stable_float(successes / denominator),
        "wilson_95": wilson_interval(successes, denominator),
    }


def exact_mcnemar(
    arm_a: str,
    arm_b: str,
    outcomes_a: Sequence[bool],
    outcomes_b: Sequence[bool],
) -> dict[str, object]:
    if len(outcomes_a) != len(outcomes_b) or not outcomes_a:
        raise ValueError("McNemar inputs must be non-empty paired vectors")
    both = sum(a and b for a, b in zip(outcomes_a, outcomes_b))
    a_only = sum(a and not b for a, b in zip(outcomes_a, outcomes_b))
    b_only = sum(not a and b for a, b in zip(outcomes_a, outcomes_b))
    neither = len(outcomes_a) - both - a_only - b_only
    discordant = a_only + b_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, index) for index in range(min(a_only, b_only) + 1))
        p_value = min(1.0, 2.0 * tail / (2 ** discordant))
    return {
        "arm_a": arm_a,
        "arm_b": arm_b,
        "paired_bases": len(outcomes_a),
        "both_success": both,
        "a_only_success": a_only,
        "b_only_success": b_only,
        "neither_success": neither,
        "discordant_pairs": discordant,
        "risk_difference_b_minus_a": _stable_float((b_only - a_only) / len(outcomes_a)),
        "exact_two_sided_p": _stable_float(p_value),
    }


def holm_adjust(comparisons: Sequence[dict[str, object]], alpha: float = 0.05) -> list[dict[str, object]]:
    if not comparisons:
        return []
    ordered = sorted(
        enumerate(comparisons),
        key=lambda item: (float(item[1]["exact_two_sided_p"]), str(item[1].get("comparison_id", ""))),
    )
    adjusted: list[float] = [1.0] * len(comparisons)
    running = 0.0
    total = len(comparisons)
    for rank, (original_index, comparison) in enumerate(ordered):
        candidate = min(1.0, (total - rank) * float(comparison["exact_two_sided_p"]))
        running = max(running, candidate)
        adjusted[original_index] = running
    result: list[dict[str, object]] = []
    for index, comparison in enumerate(comparisons):
        updated = dict(comparison)
        updated["holm_adjusted_p"] = _stable_float(adjusted[index])
        updated["reject_at_0_05"] = adjusted[index] <= alpha
        result.append(updated)
    return result


def _latency_summary(
    eval_rows: Sequence[Mapping[str, object]],
    outcomes: Mapping[tuple[str, str], ProbeOutcome],
) -> dict[str, object]:
    paired: list[float] = []
    missing: list[str] = []
    for row in eval_rows:
        base_id = str(row["base_id"])
        values = [outcomes[(base_id, variant)].latency_ms for variant in VARIANTS]
        if any(value is None for value in values):
            missing.append(base_id)
        else:
            paired.append(float(statistics.median(value for value in values if value is not None)))
    ordered = sorted(paired)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None
    return {
        "unit": "within-base median of clear and ambiguous latency",
        "formal_base_denominator": FORMAL_BASE_COUNT,
        "complete_pairs": len(paired),
        "missing_pairs": len(missing),
        "median_ms": None if not paired else _stable_float(float(statistics.median(paired))),
        "p95_ms_nearest_rank": None if p95 is None else _stable_float(p95),
    }


def _language_trial(
    row: Mapping[str, object],
    outcome: ProbeOutcome,
    evidence: Mapping[str, object],
) -> dict[str, object]:
    gold = "unique" if outcome.variant == "clear" else "clarify"
    value: dict[str, object] = {
        "schema_version": 1,
        "record_type": "language_probe",
        "base_id": outcome.base_id,
        "category": str(row["category"]),
        "variant": outcome.variant,
        "gold_disposition": gold,
        "observed_disposition": outcome.disposition,
        "reason": outcome.reason,
        "correct": outcome.disposition == gold,
        "evidence_status": outcome.evidence_status,
        "input": str(evidence["command"]),
        "raw_output": str(evidence.get("raw_output", "")),
        "raw_output_sha256": outcome.raw_output_sha256,
        "instruction_count": len(outcome.instructions),
        "candidate_ids": list(outcome.candidate_ids),
        "candidate_count": len(outcome.candidate_ids),
        "error_type": outcome.error_type,
        "latency_ms": outcome.latency_ms,
    }
    if outcome.variant == "ambiguous":
        value["candidate_coverage"] = _candidate_coverage(row, outcome)
        value["gold_candidate_set_match"] = set(outcome.candidate_ids) == set(row["candidate_entity_ids"])
    return value


def run_evaluation(
    eval_rows: Sequence[dict[str, Any]],
    protocol: Mapping[str, object],
    evidence: Mapping[tuple[str, str], Mapping[str, object]],
    *,
    integrity: Mapping[str, object] | None = None,
    model_run: Mapping[str, object] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Evaluate already validated inputs and return deterministic trials/report."""

    ttl_seconds = float(protocol["arms"][B2]["ttl_seconds"])
    outcomes: dict[tuple[str, str], ProbeOutcome] = {}
    language_trials: list[dict[str, object]] = []
    for row in eval_rows:
        base_id = str(row["base_id"])
        for variant in VARIANTS:
            outcome = analyze_probe(row, variant, evidence[(base_id, variant)])
            outcomes[(base_id, variant)] = outcome
            language_trials.append(_language_trial(
                row,
                outcome,
                evidence[(base_id, variant)],
            ))

    execution_trials: list[dict[str, object]] = []
    for row in eval_rows:
        ambiguous = outcomes[(str(row["base_id"]), "ambiguous")]
        execution_trials.append(_run_b0_trial(row, ambiguous))
        for arm in (B1, B2):
            for mutation in MUTATIONS:
                if mutation == "context_state_change" and row["category"] != "context_reference":
                    continue
                execution_trials.append(_run_prepared_trial(
                    row, ambiguous, arm, mutation, ttl_seconds,
                ))

    by_trial = {
        (str(trial["base_id"]), str(trial["arm"]), str(trial["mutation"])): trial
        for trial in execution_trials
    }
    base_ids = [str(row["base_id"]) for row in eval_rows]

    sensitivity = [outcomes[(base_id, "ambiguous")].is_clarification for base_id in base_ids]
    specificity = [outcomes[(base_id, "clear")].is_unique for base_id in base_ids]
    false_clarification = [outcomes[(base_id, "clear")].is_clarification for base_id in base_ids]
    discrimination = [ambiguous and clear for ambiguous, clear in zip(sensitivity, specificity)]
    coverage = [
        _candidate_coverage(row, outcomes[(str(row["base_id"]), "ambiguous")])
        for row in eval_rows
    ]
    completion = [
        bool(by_trial[(base_id, B2, "clean")]["exact_delta_success"])
        for base_id in base_ids
    ]

    language_metrics = {
        "sensitivity": binary_metric(sensitivity),
        "specificity": binary_metric(specificity),
        "false_clarification_rate": binary_metric(false_clarification),
        "paired_discrimination": binary_metric(discrimination),
        "candidate_coverage": binary_metric(coverage),
        "clarification_completion": binary_metric(completion),
        "inference_error_rate": binary_metric([
            any(outcomes[(base_id, variant)].evidence_status != "ok" for variant in VARIANTS)
            for base_id in base_ids
        ]),
        "latency": _latency_summary(eval_rows, outcomes),
        "independent_unit": "48 paired base IDs; the 96 probes are not independent samples",
        "production_ppv_warning": "The deliberately balanced 1:1 probes do not estimate production PPV.",
    }

    execution_metrics: dict[str, object] = {}
    universal_vectors: dict[str, list[bool]] = {}
    for arm in ARMS:
        clean = [by_trial[(base_id, arm, "clean")] for base_id in base_ids]
        arm_metrics: dict[str, object] = {
            "ambiguous_clean_exact_delta_success": binary_metric([
                bool(trial["exact_delta_success"]) for trial in clean
            ]),
            "dispatch_coverage": binary_metric([
                trial["sut_calls"] == 1 and trial["dispatched"] is True for trial in clean
            ]),
            "wrong_target_transition_rate": binary_metric([
                bool(trial["wrong_target_transition"]) for trial in clean
            ]),
            "zero_preconfirm_calls": binary_metric([
                trial["preconfirm_sut_calls"] == 0 for trial in clean
            ]),
        }
        if arm == B0:
            arm_metrics["safe_abstention_rate"] = binary_metric([
                bool(trial.get("safe_abstention")) for trial in clean
            ])
            arm_metrics["universal_guard_rate"] = {
                "status": "not_applicable",
                "reason": "B0 creates no prepared authorization to mutate",
            }
            arm_metrics["context_guard_rate"] = {
                "status": "not_applicable",
                "reason": "B0 creates no prepared authorization to mutate",
            }
        else:
            per_mutation: dict[str, object] = {}
            for mutation in MUTATIONS:
                eligible_ids = [
                    base_id for base_id in base_ids
                    if mutation != "context_state_change"
                    or by_trial[(base_id, arm, "clean")]["category"] == "context_reference"
                ]
                mutation_trials = [by_trial[(base_id, arm, mutation)] for base_id in eligible_ids]
                per_mutation[mutation] = binary_metric([
                    bool(trial["oracle_pass"]) for trial in mutation_trials
                ])
            universal = [
                all(bool(by_trial[(base_id, arm, mutation)]["oracle_pass"])
                    for mutation in UNIVERSAL_GUARD_MUTATIONS)
                for base_id in base_ids
            ]
            universal_vectors[arm] = universal
            context_ids = [
                str(row["base_id"]) for row in eval_rows if row["category"] == "context_reference"
            ]
            arm_metrics["mutation_oracle_rates"] = per_mutation
            arm_metrics["universal_guard_rate"] = binary_metric(universal)
            arm_metrics["context_guard_rate"] = binary_metric([
                bool(by_trial[(base_id, arm, "context_state_change")]["oracle_pass"])
                for base_id in context_ids
            ])
            arm_metrics["mutation_denominator_note"] = (
                "Each mutation is a paired per-base diagnostic, not an independent pooled sample."
            )
        execution_metrics[arm] = arm_metrics

    b0_clean = [bool(by_trial[(base_id, B0, "clean")]["exact_delta_success"]) for base_id in base_ids]
    b1_clean = [bool(by_trial[(base_id, B1, "clean")]["exact_delta_success"]) for base_id in base_ids]
    comparisons = [
        {
            "comparison_id": "B1_vs_B0_ambiguous_clean_exact_delta",
            **exact_mcnemar(B0, B1, b0_clean, b1_clean),
        },
        {
            "comparison_id": "B2_vs_B1_universal_guard",
            **exact_mcnemar(B1, B2, universal_vectors[B1], universal_vectors[B2]),
        },
    ]
    comparisons = holm_adjust(comparisons)

    b1_expected: dict[str, object] = {}
    for mutation in sorted(B1_INTENTIONAL_GAPS):
        eligible = [
            trial for trial in execution_trials
            if trial["arm"] == B1 and trial["mutation"] == mutation
        ]
        b1_expected[mutation] = {
            "observed_expected_limitations": sum(
                trial["interpretation"] == "expected_b1_guard_limitation" for trial in eligible
            ),
            "eligible_bases": len(eligible),
        }
    b2_failures: dict[str, int] = {}
    for mutation in MUTATIONS:
        count = sum(
            trial["arm"] == B2
            and trial["mutation"] == mutation
            and not bool(trial["oracle_pass"])
            for trial in execution_trials
        )
        b2_failures[mutation] = count

    claimed_b1 = ("clean", "session_swap", "plan_swap", "unrelated_state_change")
    unexpected_b1 = {
        mutation: sum(
            trial["arm"] == B1
            and trial["mutation"] == mutation
            and not bool(trial["oracle_pass"])
            for trial in execution_trials
        )
        for mutation in claimed_b1
    }
    b2_suite_pass = not any(b2_failures.values())
    interpretation = {
        "expected_baseline_outcomes": {
            "B0_safe_abstentions": sum(
                trial["arm"] == B0 and trial.get("safe_abstention") is True
                for trial in execution_trials
            ),
            "B1_deliberately_missing_guard_failures": b1_expected,
            "note": "Expected B0 abstention and B1 omitted-guard failures are comparison results, not B2 suite failures.",
        },
        "unexpected_failures": {
            "B1_claimed_behavior": unexpected_b1,
            "B2_full_suite": b2_failures,
        },
        "b2_suite_pass": b2_suite_pass,
    }

    trials = sorted(
        [*language_trials, *execution_trials],
        key=lambda trial: (
            0 if trial["record_type"] == "language_probe" else 1,
            str(trial["base_id"]),
            VARIANTS.index(str(trial["variant"])) if trial["record_type"] == "language_probe" else ARMS.index(str(trial["arm"])),
            -1 if trial["record_type"] == "language_probe" else MUTATIONS.index(str(trial["mutation"])),
        ),
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "protocol_id": protocol["protocol_id"],
        "status": "complete",
        "population": {
            "independent_unit": "base_id",
            "evaluation_bases": FORMAL_BASE_COUNT,
            "paired_probes": FORMAL_BASE_COUNT * len(VARIANTS),
            "context_guard_bases": CONTEXT_BASE_COUNT,
            "selective_reruns": 0,
            "infrastructure_errors_retained": True,
        },
        "input_integrity": dict(integrity or {}),
        "model_run": dict(model_run or {
            "registered_configuration": {},
            "metadata_verified": False,
            "observed_environment": None,
        }),
        "methods": {
            "binary_intervals": protocol["statistics"]["binary_intervals"],
            "paired_test": protocol["statistics"]["paired_test"],
            "multiplicity": protocol["statistics"]["multiplicity"],
            "latency_unit": protocol["statistics"]["latency_unit"],
            "trial_reset": list(protocol["trial_reset"]),
            "pseudo_replication_guards": list(
                protocol["statistics"]["pseudo_replication_guards"]
            ),
        },
        "metrics": {
            "language": language_metrics,
            "execution": execution_metrics,
        },
        "primary_inference": {
            "method": "two-sided exact McNemar on paired base outcomes",
            "multiplicity": "Holm correction across two pre-registered comparisons",
            "comparisons": comparisons,
        },
        "interpretation": interpretation,
        "quality_gate": {
            "result": "pass" if b2_suite_pass else "fail",
            "criterion": "Every eligible B2 clean/guard trial passes its pre-registered oracle.",
        },
        "trial_counts": {
            "language_probe_records": len(language_trials),
            "execution_trial_records": len(execution_trials),
            "total_records": len(trials),
        },
        "determinism": {
            "timestamps_in_outputs": False,
            "absolute_paths_in_outputs": False,
            "fresh_reset_and_prepare_per_mutation": True,
        },
    }
    return trials, report


def evaluate_to_directory(
    *,
    dataset_path: Path,
    protocol_path: Path,
    freeze_path: Path,
    evidence_path: Path,
    output_dir: Path,
    metadata_path: Path | None = None,
) -> dict[str, object]:
    dataset_rows = _read_jsonl(dataset_path, "dataset")
    protocol = _read_json(protocol_path, "protocol")
    freeze = _read_json(freeze_path, "freeze manifest")
    _validate_protocol(protocol)
    eval_rows = _validate_rows(dataset_rows)
    _validate_freeze(freeze, dataset_path, protocol_path, eval_rows)
    evidence_rows = _read_jsonl(evidence_path, "frozen evidence")
    evidence = _load_evidence(evidence_rows, eval_rows)
    if metadata_path is None:
        raise EvaluationInputError("formal evaluation requires model-run metadata")
    metadata = _read_json(metadata_path, "model-run metadata")
    evidence_sha256 = _sha256_file(evidence_path)
    model_run = _validated_model_run(
        metadata,
        protocol,
        _sha256_file(dataset_path),
        evidence_sha256,
        sum(item.get("status") != "ok" for item in evidence_rows),
    )
    integrity = {
        "dataset_sha256": _sha256_file(dataset_path),
        "evaluation_sha256": _sha256_bytes(_canonical_lines(eval_rows)),
        "protocol_sha256": _sha256_file(protocol_path),
        "evidence_sha256": evidence_sha256,
        "freeze_verified": True,
        "evidence_pairs_verified": FORMAL_BASE_COUNT * len(VARIANTS),
        "code_binding": model_run["code_binding"],
    }
    integrity["model_run_metadata_sha256"] = _sha256_file(metadata_path)
    trials, report = run_evaluation(
        eval_rows,
        protocol,
        evidence,
        integrity=integrity,
        model_run=model_run,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "trials.jsonl").write_text(
        "".join(canonical_json(trial) + "\n" for trial in trials),
        encoding="utf-8",
    )
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-frozen-evidence",
        nargs="?",
        const=DEFAULT_EVIDENCE,
        type=Path,
        required=True,
        metavar="JSONL",
        help="raw JSONL from run_model.py; omit JSONL to use evidence/domux_raw.jsonl",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument(
        "--evidence-metadata",
        type=Path,
        default=DEFAULT_EVIDENCE_METADATA,
        help="required JSON metadata emitted alongside run_model.py evidence",
    )
    args = parser.parse_args(argv)
    try:
        report = evaluate_to_directory(
            dataset_path=args.dataset,
            protocol_path=args.protocol,
            freeze_path=args.freeze,
            evidence_path=args.from_frozen_evidence,
            output_dir=args.output_dir,
            metadata_path=args.evidence_metadata,
        )
    except EvaluationInputError as exc:
        parser.error(str(exc))
    print(canonical_json({
        "status": report["status"],
        "quality_gate": report["quality_gate"]["result"],
        "evaluation_bases": report["population"]["evaluation_bases"],
        "trial_records": report["trial_counts"]["total_records"],
    }))
    return 0 if report["quality_gate"]["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
