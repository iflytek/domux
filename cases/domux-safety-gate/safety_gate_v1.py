#!/usr/bin/env python3
"""Frozen v1 input-aware safety gate for before/after reproduction."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from time import perf_counter_ns


ALLOWED_ACTIONS = {
    "turnOn", "turnOff", "set", "adjustUp", "adjustDown",
    "activate", "deactivate", "pause",
}
BLOCK_PATTERNS = (
    re.compile(
        r"\b(?:disable|deactivate|turn\s+off|stop)\b.*\b"
        r"(?:smoke|fire|carbon\s+monoxide|gas\s+leak|water\s+leak)\b.*\b"
        r"(?:alarm|detector|sensor)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:bypass|circumvent)\b.*\b(?:lock|alarm|safety|interlock)\b", re.IGNORECASE),
    re.compile(r"\b(?:ignore|override)\b.*\b(?:safety|limit|interlock|confirmation)\b", re.IGNORECASE),
    re.compile(r"\b(?:open|turn\s+on)\b.*\bgas\s+valve\b", re.IGNORECASE),
    re.compile(r"\bleave\b.*\b(?:oven|stove|heater)\b.*\bon\b.*\bindefinitely\b", re.IGNORECASE),
)
CONFIRM_DEVICE_PATTERN = re.compile(
    r"\b(?:door\s+lock|front\s+door|back\s+door|garage\s+door|oven|stove|heater|"
    r"security\s+(?:alarm|camera)|water\s+valve)\b",
    re.IGNORECASE,
)
BROAD_SCOPE_PATTERN = re.compile(
    r"\b(?:all|every|everything|entire\s+(?:home|house)|whole\s+(?:home|house))\b",
    re.IGNORECASE,
)
AMBIGUOUS_TARGET_PATTERN = re.compile(
    r"^(?:please\s+)?(?:turn|switch|open|close|stop|pause|set|make)\s+"
    r"(?:it|that|them|those)\b",
    re.IGNORECASE,
)
TEMPERATURE_PATTERN = re.compile(r"\b(?:to|at)\s+(-?\d+(?:\.\d+)?)\s*(?:degrees?|°|c\b)", re.IGNORECASE)


@dataclass(frozen=True)
class GateDecision:
    decision: str
    reasons: tuple[str, ...]
    format_valid: bool
    parsed_lines: int
    latency_us: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_domux_output(raw_output: str) -> tuple[list[list[str]], list[str]]:
    """Return parsed seven-field lines and validation errors."""
    lines = [line.strip() for line in raw_output.strip().splitlines() if line.strip()]
    parsed: list[list[str]] = []
    errors: list[str] = []
    if not lines:
        return [], ["empty model output"]

    for index, line in enumerate(lines, start=1):
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != 7:
            errors.append(f"line {index} has {len(fields)} fields, expected 7")
            continue
        if fields[0] not in ALLOWED_ACTIONS:
            errors.append(f"line {index} has unsupported action {fields[0]!r}")
            continue
        parsed.append(fields)
    return parsed, errors


def decide(command: str, raw_output: str) -> GateDecision:
    """Classify an instruction as allow, confirm, or block before execution."""
    started = perf_counter_ns()
    parsed, format_errors = parse_domux_output(raw_output)
    reasons: list[str] = []

    if format_errors:
        reasons.extend(format_errors)
        decision = "block"
    elif any(pattern.search(command) for pattern in BLOCK_PATTERNS):
        reasons.append("explicit safety-system disable, bypass, or hazardous persistence request")
        decision = "block"
    else:
        if CONFIRM_DEVICE_PATTERN.search(command):
            reasons.append("high-consequence device or perimeter control")
        if BROAD_SCOPE_PATTERN.search(command):
            reasons.append("broad multi-device scope")
        if AMBIGUOUS_TARGET_PATTERN.search(command.strip()):
            reasons.append("ambiguous pronoun target")
        for match in TEMPERATURE_PATTERN.finditer(command):
            value = float(match.group(1))
            if value < 16 or value > 30:
                reasons.append(f"temperature {value:g}C is outside the documented AC range")
                break
        decision = "confirm" if reasons else "allow"

    elapsed_us = (perf_counter_ns() - started) / 1_000
    return GateDecision(
        decision=decision,
        reasons=tuple(reasons or ("no safety trigger",)),
        format_valid=not format_errors,
        parsed_lines=len(parsed),
        latency_us=elapsed_us,
    )
