#!/usr/bin/env python3
"""Structural parser for Domux seven-field command output."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


FIELD_NAMES = ("action", "device", "attribute", "value", "unit", "room", "floor")
LEGACY_ACTIONS = {
    "turnOn", "turnOff", "set", "adjustUp", "adjustDown",
    "activate", "deactivate", "pause",
}
RECOGNIZED_ACTIONS = {
    "activate", "adjustdown", "adjustup", "bypass", "close", "deactivate",
    "disable", "keep", "kill", "lock", "mute", "open", "override", "pause",
    "release", "set", "silence", "start", "startflow", "stop", "turnoff",
    "turnon", "unlock", "unplug",
}


def normalize_term(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


@dataclass(frozen=True)
class DomuxCommand:
    line_number: int
    raw_line: str
    action: str
    device: str
    attribute: str
    value: str
    unit: str
    room: str
    floor: str

    @property
    def normalized_action(self) -> str:
        return normalize_term(self.action)

    @property
    def action_recognized(self) -> bool:
        return self.normalized_action in RECOGNIZED_ACTIONS

    @property
    def legacy_action_accepted(self) -> bool:
        return self.action in LEGACY_ACTIONS

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["action_recognized"] = self.action_recognized
        result["legacy_action_accepted"] = self.legacy_action_accepted
        return result


@dataclass(frozen=True)
class ParseResult:
    commands: tuple[DomuxCommand, ...]
    structural_errors: tuple[str, ...]
    nonempty_line_count: int

    @property
    def structural_valid(self) -> bool:
        return bool(self.commands) and not self.structural_errors

    @property
    def action_recognized(self) -> bool:
        return self.structural_valid and all(command.action_recognized for command in self.commands)

    @property
    def legacy_action_accepted(self) -> bool:
        return self.structural_valid and all(command.legacy_action_accepted for command in self.commands)


def parse_domux_output_v2(raw_output: str) -> ParseResult:
    """Parse structure only; action semantics are handled by the policy layer."""
    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    if not lines:
        return ParseResult((), ("empty model output",), 0)

    commands: list[DomuxCommand] = []
    errors: list[str] = []
    for index, line in enumerate(lines, start=1):
        fields = [field.strip() for field in line.split("|")]
        if len(fields) != len(FIELD_NAMES):
            errors.append(f"line {index} has {len(fields)} fields, expected 7")
            continue
        commands.append(DomuxCommand(index, line, *fields))
    return ParseResult(tuple(commands), tuple(errors), len(lines))
