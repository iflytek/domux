#!/usr/bin/env python3
"""Output-aware fail-closed execution safety gate for Domux commands."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from time import perf_counter_ns

from domux_parser import DomuxCommand, normalize_term, parse_domux_output_v2
from safety_gate_parser_fixed import input_policy_decision


SEVERITY = {"allow": 0, "confirm": 1, "block": 2}
DISABLE_ACTIONS = {
    "bypass", "deactivate", "disable", "kill", "mute", "override", "pause",
    "silence", "stop", "turnoff", "unplug",
}
ENABLE_ACTIONS = {"activate", "open", "release", "start", "startflow", "turnon", "unlock"}
ACCESS_ACTIONS = {"activate", "close", "lock", "open", "turnoff", "turnon", "unlock"}

DEVICE_PATTERNS = {
    "life_safety": (
        "smokealarm", "smokedetector", "firealarm", "codetector",
        "carbonmonoxidedetector", "gasleakdetector", "gasleaksensor",
        "watersensor", "waterleaksensor", "safetysensor",
    ),
    "security": ("securityalarm", "homesecurityalarm", "interlock"),
    "gas": ("gasvalve", "gasline", "gasflow", "gasactuator"),
    "access": ("doorlock", "frontdoor", "backdoor", "garagedoor", "securitydoor", "gate", "door"),
    "thermal": ("oven", "stove", "heater", "boiler"),
    "utility": ("watervalve", "mainvalve", "breaker", "mainpower", "electricalpanel"),
    "climate": ("ac", "airconditioner", "airconditioning"),
    "lighting": ("light", "lamp"),
    "shade": ("curtain", "blind"),
    "scene": ("mode", "scene"),
}

INPUT_DEVICE_PATTERNS = {
    "life_safety": re.compile(r"smoke|fire alarm|carbon monoxide|gas leak|烟雾|火警|一氧化碳|燃气泄漏", re.I),
    "security": re.compile(r"security alarm|security system|安防|防盗报警", re.I),
    "gas": re.compile(r"gas valve|gas line|燃气阀|燃气管", re.I),
    "access": re.compile(r"door lock|front door|back door|garage door|security door|\bgate\b|门锁|前门|后门|车库门", re.I),
    "thermal": re.compile(r"oven|stove|heater|boiler|烤箱|炉灶|加热器|锅炉", re.I),
    "utility": re.compile(r"water valve|main valve|breaker|main power|水阀|总阀|断路器|总电源", re.I),
    "climate": re.compile(r"\bac\b|air condition|空调", re.I),
    "lighting": re.compile(r"light|lamp|灯", re.I),
    "shade": re.compile(r"curtain|blind|窗帘|百叶", re.I),
    "scene": re.compile(r"scene|mode|场景|模式", re.I),
}

ROOM_TERMS = (
    "living room", "kitchen", "bedroom", "guest bedroom", "master bedroom", "nursery",
    "bathroom", "garage", "office", "home office", "garden", "balcony", "hallway",
    "dining room", "basement", "laundry room", "entrance hall",
    "客厅", "厨房", "卧室", "客房", "主卧", "婴儿房", "浴室", "车库", "办公室", "花园", "阳台",
)
FLOOR_TERMS = (
    "ground floor", "first floor", "second floor", "upstairs", "downstairs",
    "一楼", "二楼", "楼上", "楼下",
)


@dataclass(frozen=True)
class LineDecision:
    line_number: int
    decision: str
    reasons: tuple[str, ...]
    semantic_supported: bool
    mismatch_detected: bool
    parsed: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GateDecision:
    decision: str
    reasons: tuple[str, ...]
    structural_valid: bool
    action_recognized: bool
    semantic_supported: bool
    legacy_action_accepted: bool
    parsed_lines: int
    line_decisions: tuple[LineDecision, ...]
    input_decision: str
    mismatch_detected: bool
    interception_mode: str
    latency_us: float

    @property
    def format_valid(self) -> bool:
        """Compatibility alias; callers should use structural_valid."""
        return self.structural_valid

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _max_decision(*decisions: str) -> str:
    return max(decisions, key=SEVERITY.__getitem__)


def _device_category(device: str) -> str:
    normalized = normalize_term(device)
    for category, patterns in DEVICE_PATTERNS.items():
        if any(pattern in normalized for pattern in patterns):
            return category
    return "unknown"


def _input_categories(command: str) -> set[str]:
    return {category for category, pattern in INPUT_DEVICE_PATTERNS.items() if pattern.search(command)}


def _explicit_terms(command: str, terms: tuple[str, ...]) -> set[str]:
    lowered = command.casefold()
    return {normalize_term(term) for term in terms if term.casefold() in lowered}


def _expected_action_family(command: str) -> str | None:
    lowered = command.casefold()
    if re.search(r"\b(?:dim|decrease|lower)\b|调暗|降低", lowered):
        return "decrease"
    if re.search(r"\b(?:brighten|increase|raise|brighter)\b|调亮|提高", lowered):
        return "increase"
    if re.search(r"\b(?:unlock|open)\b|解锁|打开", lowered):
        return "enable"
    if re.search(r"\b(?:turn on|switch on|activate|enable)\b|开启", lowered):
        return "enable"
    if re.search(r"\b(?:turn off|switch off|disable|deactivate|stop)\b|关闭|停用", lowered):
        return "disable"
    if re.search(r"\b(?:lock|close)\b|上锁|锁定|关上", lowered):
        return "disable"
    if re.search(r"\b(?:set|make|change)\b|设置|调到", lowered):
        return "set"
    return None


def _action_family(action: str) -> str:
    normalized = normalize_term(action)
    if normalized in ENABLE_ACTIONS:
        return "enable"
    if normalized in DISABLE_ACTIONS or normalized in {"close", "lock"}:
        return "disable"
    if normalized == "adjustdown":
        return "decrease"
    if normalized == "adjustup":
        return "increase"
    if normalized == "set":
        return "set"
    return "unknown"


def _numeric_value(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _location_matches(explicit_locations: set[str], output_location: str) -> bool:
    if output_location in {"", "*"} or output_location in explicit_locations:
        return True
    # Numbered locations such as "Bedroom 2" retain the explicit base room.
    return any(re.fullmatch(re.escape(location) + r"\d+", output_location) for location in explicit_locations)


def _output_risk(line: DomuxCommand) -> tuple[str, list[str], bool]:
    action = line.normalized_action
    category = _device_category(line.device)
    reasons: list[str] = []
    decision = "allow"
    supported = line.action_recognized

    if not supported:
        decision = "block" if category in {"life_safety", "security", "gas", "access", "thermal", "utility"} else "confirm"
        reasons.append(f"output: unknown action {line.action!r} for {category} device")

    if category in {"life_safety", "security"}:
        if action in DISABLE_ACTIONS:
            decision = "block"
            reasons.append("output: protection or life-safety system disable/bypass")
        elif decision == "allow":
            decision = "confirm"
            reasons.append("output: protection or life-safety actuator")
    elif category == "gas":
        if action in ENABLE_ACTIONS:
            decision = "block"
            reasons.append("output: gas flow activation/opening")
        elif decision == "allow":
            decision = "confirm"
            reasons.append("output: gas utility control")
    elif category == "access":
        if action in ACCESS_ACTIONS:
            decision = _max_decision(decision, "confirm")
            reasons.append("output: perimeter or access-control actuation")
    elif category == "thermal":
        value = _numeric_value(line.value)
        if action == "keep" or normalize_term(line.attribute) in {"indefinite", "continuous", "on"}:
            decision = "block"
            reasons.append("output: unbounded thermal persistence")
        elif normalize_term(line.attribute) == "temperature" and value is not None:
            limit = 300.0 if normalize_term(line.device) in {"oven", "stove"} else 40.0
            if value > limit:
                decision = "block"
                reasons.append(f"output: extreme thermal setting {value:g} {line.unit}")
            else:
                decision = _max_decision(decision, "confirm")
                reasons.append("output: thermal temperature actuation")
        else:
            decision = _max_decision(decision, "confirm")
            reasons.append("output: thermal appliance actuation")
    elif category == "utility":
        decision = _max_decision(decision, "confirm")
        reasons.append("output: high-consequence utility control")
    elif category == "climate" and normalize_term(line.attribute) == "temperature":
        value = _numeric_value(line.value)
        if value is not None and normalize_term(line.unit) in {"c", "celsius", "degreecelsius"} and not 16 <= value <= 30:
            decision = _max_decision(decision, "confirm")
            reasons.append(f"output: AC temperature {value:g}C outside documented range")

    return decision, reasons or ["output: no semantic risk trigger"], supported


def _mismatch_risk(command: str, line: DomuxCommand) -> tuple[str, list[str]]:
    reasons: list[str] = []
    decision = "allow"
    input_categories = _input_categories(command)
    output_category = _device_category(line.device)
    high_consequence = {"life_safety", "security", "gas", "access", "thermal", "utility"}

    if input_categories and output_category not in input_categories:
        decision = "block" if output_category in high_consequence else "confirm"
        reasons.append(
            f"mismatch: input devices {sorted(input_categories)} do not support output device category {output_category}"
        )

    expected_action = _expected_action_family(command)
    actual_action = _action_family(line.action)
    action_compatible = expected_action == actual_action or actual_action == "unknown"
    if (
        output_category == "shade"
        and expected_action in {"enable", "disable"}
        and actual_action == "set"
        and normalize_term(line.attribute) == "position"
    ):
        action_compatible = True
    if expected_action and not action_compatible:
        decision = _max_decision(decision, "confirm")
        reasons.append(f"mismatch: input action {expected_action} vs output action {actual_action}")

    input_rooms = _explicit_terms(command, ROOM_TERMS)
    output_room = normalize_term(line.room)
    if input_rooms and not _location_matches(input_rooms, output_room):
        decision = _max_decision(decision, "confirm")
        reasons.append(f"mismatch: input room {sorted(input_rooms)} vs output room {line.room!r}")

    input_floors = _explicit_terms(command, FLOOR_TERMS)
    output_floor = normalize_term(line.floor)
    if input_floors and not _location_matches(input_floors, output_floor):
        decision = _max_decision(decision, "confirm")
        reasons.append(f"mismatch: input floor {sorted(input_floors)} vs output floor {line.floor!r}")

    attribute = normalize_term(line.attribute)
    lowered = command.casefold()
    expected_attributes: set[str] = set()
    if re.search(r"temperature|degrees?|温度|度", lowered):
        expected_attributes.add("temperature")
    if re.search(r"brightness|dim|brighten|亮度|调暗|调亮", lowered):
        expected_attributes.add("brightness")
    if re.search(r"color|颜色", lowered):
        expected_attributes.add("color")
    if re.search(r"\bmode\b|模式", lowered):
        expected_attributes.add("mode")
    if re.search(r"position|百分比|位置", lowered):
        expected_attributes.add("position")
    combined_attributes = {"colortemperature"} if {"color", "temperature"} <= expected_attributes else set()
    if expected_attributes and attribute not in expected_attributes | combined_attributes | {"", "*"}:
        decision = _max_decision(decision, "confirm")
        reasons.append(f"mismatch: input attribute {sorted(expected_attributes)} vs output attribute {line.attribute!r}")

    input_numbers = {
        float(match.group(1))
        for match in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(?:degrees?|°|celsius|kelvin|percent|%|度)", lowered)
    }
    output_value = _numeric_value(line.value)
    if input_numbers and output_value is not None and output_value not in input_numbers:
        decision = _max_decision(decision, "confirm")
        reasons.append(f"mismatch: input value {sorted(input_numbers)} vs output value {output_value:g}")

    expected_unit: str | None = None
    if re.search(r"\bkelvin\b", lowered):
        expected_unit = "kelvin"
    elif re.search(r"(?:percent|%)", lowered):
        expected_unit = "percent"
    elif re.search(r"(?:degrees?|°|celsius|度)", lowered):
        expected_unit = "celsius"
    actual_unit = normalize_term(line.unit)
    unit_aliases = {
        "celsius": {"c", "celsius", "degreecelsius"},
        "kelvin": {"k", "kelvin"},
        "percent": {"percent", "percentage"},
    }
    if expected_unit and output_value is not None and actual_unit not in unit_aliases[expected_unit]:
        decision = _max_decision(decision, "confirm")
        reasons.append(
            f"mismatch: expected {expected_unit} unit vs output unit {line.unit!r}"
        )

    return decision, reasons


def decide(command: str, raw_output: str) -> GateDecision:
    started = perf_counter_ns()
    parsed = parse_domux_output_v2(raw_output)
    input_decision, input_reasons = input_policy_decision(command)

    if not parsed.structural_valid:
        reasons = tuple(f"parser: {error}" for error in parsed.structural_errors)
        return GateDecision(
            decision="block",
            reasons=reasons,
            structural_valid=False,
            action_recognized=False,
            semantic_supported=False,
            legacy_action_accepted=False,
            parsed_lines=len(parsed.commands),
            line_decisions=(),
            input_decision=input_decision,
            mismatch_detected=False,
            interception_mode="fail_closed",
            latency_us=(perf_counter_ns() - started) / 1_000,
        )

    lines: list[LineDecision] = []
    final_decision = input_decision
    all_reasons = list(input_reasons if input_decision != "allow" else ())
    for line in parsed.commands:
        output_decision, output_reasons, supported = _output_risk(line)
        mismatch_decision, mismatch_reasons = _mismatch_risk(command, line)
        line_decision = _max_decision(output_decision, mismatch_decision)
        reasons = tuple(output_reasons + mismatch_reasons)
        lines.append(LineDecision(
            line_number=line.line_number,
            decision=line_decision,
            reasons=reasons,
            semantic_supported=supported,
            mismatch_detected=bool(mismatch_reasons),
            parsed=line.to_dict(),
        ))
        final_decision = _max_decision(final_decision, line_decision)
        if line_decision != "allow":
            all_reasons.extend(
                f"line {line.line_number}: {reason}"
                for reason in reasons
                if "no semantic" not in reason
            )

    mismatch_detected = any(line.mismatch_detected for line in lines)
    semantic_supported = all(line.semantic_supported for line in lines)
    if final_decision == "allow":
        all_reasons = ["input/output: no safety trigger or mismatch"]
    return GateDecision(
        decision=final_decision,
        reasons=tuple(dict.fromkeys(all_reasons)),
        structural_valid=True,
        action_recognized=parsed.action_recognized,
        semantic_supported=semantic_supported,
        legacy_action_accepted=parsed.legacy_action_accepted,
        parsed_lines=len(parsed.commands),
        line_decisions=tuple(lines),
        input_decision=input_decision,
        mismatch_detected=mismatch_detected,
        interception_mode="semantic" if final_decision != "allow" else "none",
        latency_us=(perf_counter_ns() - started) / 1_000,
    )
