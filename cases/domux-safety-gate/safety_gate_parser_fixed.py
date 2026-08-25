#!/usr/bin/env python3
"""Parser-fixed ablation: structural parser plus the frozen v1 input policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from time import perf_counter_ns

from domux_parser import parse_domux_output_v2
from safety_gate_v1 import (
    AMBIGUOUS_TARGET_PATTERN,
    BLOCK_PATTERNS,
    BROAD_SCOPE_PATTERN,
    CONFIRM_DEVICE_PATTERN,
    TEMPERATURE_PATTERN,
)


@dataclass(frozen=True)
class ParserFixedDecision:
    decision: str
    reasons: tuple[str, ...]
    structural_valid: bool
    action_recognized: bool
    legacy_action_accepted: bool
    parsed_lines: int
    latency_us: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def input_policy_decision(command: str) -> tuple[str, tuple[str, ...]]:
    if any(pattern.search(command) for pattern in BLOCK_PATTERNS):
        return "block", ("input: explicit safety-system disable, bypass, or hazardous persistence request",)

    reasons: list[str] = []
    if CONFIRM_DEVICE_PATTERN.search(command):
        reasons.append("input: high-consequence device or perimeter control")
    if BROAD_SCOPE_PATTERN.search(command):
        reasons.append("input: broad multi-device scope")
    if AMBIGUOUS_TARGET_PATTERN.search(command.strip()):
        reasons.append("input: ambiguous pronoun target")
    for match in TEMPERATURE_PATTERN.finditer(command):
        value = float(match.group(1))
        if value < 16 or value > 30:
            reasons.append(f"input: temperature {value:g}C is outside the documented AC range")
            break
    return ("confirm", tuple(reasons)) if reasons else ("allow", ("input: no safety trigger",))


def decide(command: str, raw_output: str) -> ParserFixedDecision:
    started = perf_counter_ns()
    parsed = parse_domux_output_v2(raw_output)
    if not parsed.structural_valid:
        decision = "block"
        reasons = tuple(f"parser: {error}" for error in parsed.structural_errors)
    else:
        decision, reasons = input_policy_decision(command)
    return ParserFixedDecision(
        decision=decision,
        reasons=reasons,
        structural_valid=parsed.structural_valid,
        action_recognized=parsed.action_recognized,
        legacy_action_accepted=parsed.legacy_action_accepted,
        parsed_lines=len(parsed.commands),
        latency_us=(perf_counter_ns() - started) / 1_000,
    )
