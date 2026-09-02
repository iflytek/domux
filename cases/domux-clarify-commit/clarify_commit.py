#!/usr/bin/env python3
"""Deterministic grounding and one-time commit for Domux seven-slot outputs.

The model remains a parser.  This module deliberately keeps inventory lookup,
clarification, authorization lifetime, state binding, dispatch and postcondition
checks outside the model so every boundary can be audited and replayed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import ExitStack
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Callable, Iterable, Mapping, Sequence


SLOTS = ("action", "device", "attribute", "value", "unit", "room", "floor")
SUPPORTED_DOMAINS = frozenset({"light", "cover", "climate"})
MAX_UTTERANCE_CHARS = 2048
ENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")
GENERIC_DEVICE_ALIASES = {
    "light": "light",
    "lights": "light",
    "lamp": "light",
    "lamps": "light",
    "lighting": "light",
    "curtain": "cover",
    "curtains": "cover",
    "blind": "cover",
    "blinds": "cover",
    "shade": "cover",
    "shades": "cover",
    "cover": "cover",
    "covers": "cover",
    "ac": "climate",
    "acs": "climate",
    "a c": "climate",
    "a/c": "climate",
    "air conditioner": "climate",
    "air conditioners": "climate",
    "air conditioning": "climate",
}
GENERIC_DEVICE_NOUN_RE = (
    "(?:"
    + "|".join(
        re.escape(alias)
        for alias in sorted(GENERIC_DEVICE_ALIASES, key=len, reverse=True)
    )
    + ")"
)
GENERIC_REFERENCE_NOUN_RE = (
    rf"(?:one|ones|device|devices|{GENERIC_DEVICE_NOUN_RE}|"
    r"room|rooms|floor|floors|level|levels)"
)
CORRECTION_SELECTOR_ACTION_RE = r"(?:use|select|choose|mean)"
SELECTOR_ACTION_RE = rf"(?:{CORRECTION_SELECTOR_ACTION_RE}|touch|act\s+on)"
STATE_CHANGE_ACTION_RE = (
    r"(?:turn(?:\s+(?:on|off))?|switch(?:\s+(?:on|off))?|open|close|set|"
    r"change|make|move|adjust|raise|lower|increase|decrease|execute|proceed|"
    r"dispatch)"
)
EXECUTION_CONTROL_ACTION_RE = (
    rf"(?:{STATE_CHANGE_ACTION_RE}|confirm|authorize|approve|go\s+ahead|do\s+it)"
)
STATE_COMMAND_RE = (
    rf"(?:(?:(?:please|just)\s+)*{STATE_CHANGE_ACTION_RE}|"
    rf"(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:just\s+)?"
    rf"{STATE_CHANGE_ACTION_RE}|"
    rf"(?:i|we)\s+(?:just\s+)?(?:want|need)\s+(?:you\s+)?to\s+"
    rf"(?:please\s+)?(?:just\s+)?"
    rf"{STATE_CHANGE_ACTION_RE})"
)
RESTART_COMMAND_RE = (
    rf"(?:{STATE_COMMAND_RE}|(?:(?:please|just)\s+)*"
    rf"(?:(?:i|we)\s+(?:just\s+)?{SELECTOR_ACTION_RE}|{SELECTOR_ACTION_RE}))"
)
OTHER_REFERENCE_RE = (
    rf"\b(?:the\s+other(?!\s+than\b)(?:\s+(?:[a-z0-9]+\s+){{0,3}}"
    rf"{GENERIC_REFERENCE_NOUN_RE})?|other(?!\s+than\b)"
    rf"(?:\s+[a-z0-9]+){{0,3}}\s+{GENERIC_REFERENCE_NOUN_RE})\b"
)
POSTPOSED_NO_MARKER_RE = r"\bno\b\s*(?:[,，:：—–.!?;。！？；-])"
COLOR_RGB = {
    "blue": [0, 0, 255],
    "cyan": [0, 255, 255],
    "cool white": [201, 226, 255],
    "green": [0, 128, 0],
    "lavender": [230, 230, 250],
    "magenta": [255, 0, 255],
    "orange": [255, 165, 0],
    "pink": [255, 192, 203],
    "purple": [128, 0, 128],
    "red": [255, 0, 0],
    "sky blue": [135, 206, 235],
    "warm white": [255, 244, 229],
    "white": [255, 255, 255],
    "yellow": [255, 255, 0],
}


class ParseError(ValueError):
    """The raw Domux text is not a bounded sequence of seven-slot records."""


class GroundingError(ValueError):
    """A structurally valid model output cannot be mapped to an executable plan."""


class AdapterError(RuntimeError):
    """The Home Assistant adapter could not read or change controlled state."""


class ServiceCallError(AdapterError):
    """A dispatch failure with action-local, non-heuristic outcome metadata."""

    def __init__(
        self,
        message: str,
        *,
        attempted: bool,
        acknowledged: bool,
        outcome_unknown: bool,
    ) -> None:
        super().__init__(message)
        self.attempted = attempted
        self.acknowledged = acknowledged
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class ServiceCallResult:
    after: Mapping[str, object]
    attempted: bool
    acknowledged: bool
    outcome_unknown: bool = False


def normalize_text(value: object) -> str:
    """Normalize only for comparison; never use this to overwrite raw evidence."""

    return " ".join(
        str(value)
        .replace("_", " ")
        .replace("-", " ")
        .replace("’", "'")
        .replace("‘", "'")
        .split()
    ).casefold()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DomuxInstruction:
    action: str
    device: str
    attribute: str
    value: str
    unit: str
    room: str
    floor: str

    @classmethod
    def from_fields(cls, fields: Sequence[str]) -> "DomuxInstruction":
        if len(fields) != len(SLOTS):
            raise ParseError(f"expected seven fields, got {len(fields)}")
        cleaned = tuple(field.strip() for field in fields)
        if any(not field for field in cleaned):
            raise ParseError("empty fields must be represented by '*'")
        if not cleaned[0] or cleaned[0] == "*":
            raise ParseError("action cannot be omitted")
        return cls(*cleaned)

    def to_pipe(self) -> str:
        return "|".join(getattr(self, slot) for slot in SLOTS)

    def canonical_slots(self) -> dict[str, str]:
        return {slot: normalize_text(getattr(self, slot)) for slot in SLOTS}


def parse_domux_output(raw_output: str, *, max_instructions: int = 8) -> tuple[DomuxInstruction, ...]:
    """Parse raw model text without silently dropping malformed segments."""

    if not isinstance(raw_output, str) or not raw_output.strip():
        raise ParseError("model output is empty")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in raw_output):
        raise ParseError("model output contains control characters")
    segments = [segment.strip() for segment in raw_output.replace("&", "\n").splitlines()]
    if any(not segment for segment in segments):
        segments = [segment for segment in segments if segment]
    if not segments:
        raise ParseError("model output has no non-empty instruction")
    if len(segments) > max_instructions:
        raise ParseError(f"model output has more than {max_instructions} instructions")
    parsed: list[DomuxInstruction] = []
    for index, segment in enumerate(segments, start=1):
        fields = segment.split("|")
        if len(fields) != len(SLOTS):
            raise ParseError(f"instruction {index} has {len(fields)} fields, expected seven")
        parsed.append(DomuxInstruction.from_fields(fields))
    return tuple(parsed)


@dataclass(frozen=True)
class EntitySpec:
    entity_id: str
    domain: str
    device: str
    room: str
    floor: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.domain not in SUPPORTED_DOMAINS:
            raise ValueError(f"unsupported entity domain: {self.domain}")
        if not self.entity_id.startswith(f"{self.domain}."):
            raise ValueError(f"entity_id/domain mismatch: {self.entity_id}")
        if not ENTITY_ID_RE.fullmatch(self.entity_id):
            raise ValueError(f"invalid Home Assistant entity_id: {self.entity_id}")

    def stable_metadata(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id,
            "domain": self.domain,
            "device": self.device,
            "room": self.room,
            "floor": self.floor,
            "aliases": sorted(self.aliases, key=lambda value: (normalize_text(value), value)),
        }


@dataclass(frozen=True)
class SessionContext:
    recent_entity_ids: tuple[str, ...] = ()


class EntityRegistry:
    """A deterministic, immutable view of the allowed Home Assistant entities."""

    def __init__(self, entities: Iterable[EntitySpec]):
        by_id: dict[str, EntitySpec] = {}
        for entity in entities:
            if entity.entity_id in by_id:
                raise ValueError(f"duplicate entity_id: {entity.entity_id}")
            by_id[entity.entity_id] = entity
        if not by_id:
            raise ValueError("registry cannot be empty")
        self._by_id = by_id

    def get(self, entity_id: str) -> EntitySpec:
        try:
            return self._by_id[entity_id]
        except KeyError as exc:
            raise GroundingError(f"entity is not in the allowed registry: {entity_id}") from exc

    @property
    def entities(self) -> tuple[EntitySpec, ...]:
        return tuple(sorted(self._by_id.values(), key=self._sort_key))

    @staticmethod
    def _sort_key(entity: EntitySpec) -> tuple[str, str, str, str]:
        return tuple(normalize_text(part) for part in (
            entity.floor, entity.room, entity.device, entity.entity_id,
        ))

    @staticmethod
    def _domain_hint(instruction: DomuxInstruction) -> str | None:
        device = normalize_text(instruction.device)
        if device in GENERIC_DEVICE_ALIASES:
            return GENERIC_DEVICE_ALIASES[device]
        attribute = normalize_text(instruction.attribute)
        if attribute in {"brightness", "color", "color temperature", "colortemperature"}:
            return "light"
        if attribute in {"position", "openness"}:
            return "cover"
        if attribute in {"temperature", "mode", "wind speed", "windspeed", "fan speed"}:
            return "climate"
        return None

    @staticmethod
    def _device_matches(entity: EntitySpec, requested: str) -> bool:
        requested_norm = normalize_text(requested)
        if requested_norm in {"*", "it", "that", "that one", "this", "this one"}:
            return True
        names = {normalize_text(entity.device), *(normalize_text(alias) for alias in entity.aliases)}
        if requested_norm in names:
            return True
        domain = GENERIC_DEVICE_ALIASES.get(requested_norm)
        return domain == entity.domain

    def candidates(
        self,
        instruction: DomuxInstruction,
        context: SessionContext | None = None,
    ) -> tuple[EntitySpec, ...]:
        domain_hint = self._domain_hint(instruction)
        room = normalize_text(instruction.room)
        floor = normalize_text(instruction.floor)
        candidates = [
            entity
            for entity in self._by_id.values()
            if (domain_hint is None or entity.domain == domain_hint)
            and self._device_matches(entity, instruction.device)
            and (room == "*" or normalize_text(entity.room) == room)
            and (floor == "*" or normalize_text(entity.floor) == floor)
        ]

        requested = normalize_text(instruction.device)
        if context and requested in {"*", "it", "that", "that one", "this", "this one"}:
            recent = set(context.recent_entity_ids)
            contextual = [entity for entity in candidates if entity.entity_id in recent]
            if contextual:
                candidates = contextual
        return tuple(sorted(candidates, key=self._sort_key))

    def metadata_digest(self, entity_ids: Iterable[str]) -> str:
        ordered = [self.get(entity_id).stable_metadata() for entity_id in sorted(set(entity_ids))]
        return digest_json(ordered)

    def with_replacement(self, entity: EntitySpec) -> "EntityRegistry":
        if entity.entity_id not in self._by_id:
            raise GroundingError(f"cannot replace unknown entity: {entity.entity_id}")
        updated = dict(self._by_id)
        updated[entity.entity_id] = entity
        return EntityRegistry(updated.values())


@dataclass(frozen=True)
class Clarification:
    required: bool
    reason: str
    candidates: tuple[EntitySpec, ...]
    prompt: str | None
    reasons: tuple[str, ...] = ()
    unresolved_slots: tuple[str, ...] = ()


def _candidate_option(index: int, candidate: EntitySpec) -> str:
    """Render a visibly unique, stable authorization choice."""

    aliases = sorted(
        {alias for alias in candidate.aliases if normalize_text(alias)},
        key=lambda value: (normalize_text(value), value),
    )
    alias_text = f" / alias: {', '.join(aliases)}" if aliases else ""
    return (
        f"{index}. {candidate.floor} / {candidate.room} / {candidate.device}"
        f"{alias_text} / id: {candidate.entity_id}"
    )


def clarification_for(candidates: Sequence[EntitySpec], *, max_display: int = 3) -> Clarification:
    if not candidates:
        return Clarification(True, "no_registry_match", (), None, ("no_registry_match",))
    if len(candidates) == 1:
        return Clarification(False, "unique_registry_match", tuple(candidates), None)
    displayed = tuple(candidates[:max_display])
    if len(candidates) > max_display:
        prompt = "Which room or floor should I use? More than three devices match."
        return Clarification(True, "too_many_candidates", displayed, prompt)
    options = "; ".join(
        _candidate_option(index, candidate)
        for index, candidate in enumerate(displayed, start=1)
    )
    return Clarification(
        True,
        "multiple_registry_matches",
        displayed,
        f"Which device: {options}?",
        ("multiple_registry_matches",),
    )


def _phrase_in(normalized_text: str, phrase: object) -> bool:
    normalized_phrase = normalize_text(phrase)
    if not normalized_phrase or normalized_phrase == "*":
        return False
    return re.search(
        rf"(?<![a-z0-9]){re.escape(normalized_phrase)}(?![a-z0-9])",
        normalized_text,
    ) is not None


def _numbers_in(text: str) -> tuple[float, ...]:
    return tuple(float(value) for value in re.findall(r"(?<![a-z0-9])-?\d+(?:\.\d+)?", text))


def _canonical_number_token(value: float) -> str:
    return str(int(value)) if value.is_integer() else format(value, ".15g")


def _operation_value_token(value: str) -> str | None:
    if value == "*":
        return None
    try:
        return f"number:{_canonical_number_token(float(value))}"
    except ValueError:
        return f"value:{normalize_text(value)}"


def _value_is_explicitly_excluded(text: str, value: str) -> bool:
    """Bind a negative choice to the exact operation value it excludes."""

    token = normalize_text(value)
    if not token or token == "*":
        return False
    prefix = (
        r"(?:\b(?:anything|something|everything|any(?:\s+[a-z]+)?|a\s+value)\s+"
        r"(?:but|other\s+than|besides|apart\s+from)\s+(?:the\s+)?|"
        r"\b(?:other\s+than|except|besides|apart\s+from|avoid|without)\s+"
        r"(?:using\s+)?(?:the\s+)?|"
        r"\b(?:do\s+not|don't|dont|never)\s+(?:use|choose|select)\s+(?:the\s+)?|"
        r"\bnot\s+(?:the\s+)?|"
        r"\bno\s+(?:the\s+)?)"
    )
    return re.search(
        rf"{prefix}{re.escape(token)}(?![a-z0-9])",
        normalize_text(text),
    ) is not None


def _has_operation_value_exclusion(text: str) -> bool:
    """Detect requests that permit a set of values but do not choose one."""

    normalized = normalize_text(text)
    if re.search(
        r"\b(?:anything|something|everything|any(?:\s+[a-z]+)?|a\s+value)\s+"
        r"(?:but|other\s+than|besides|apart\s+from)\b|"
        r"\b(?:do\s+not|don't|dont|never)\s+(?:use|choose|select)\b|"
        r"\b(?:set|change|make|use)\b.{0,40}\b"
        r"(?:avoid|without|other\s+than|besides|apart\s+from)\b",
        normalized,
    ):
        return True
    known_values = (
        *COLOR_RGB,
        "fan only", "heat cool", "cool", "heat", "dry", "fan", "auto",
        "low", "medium", "high",
    )
    return any(_value_is_explicitly_excluded(normalized, value) for value in known_values)


def _excluded_operation_value_tokens(
    text: str,
    source_instructions: Sequence[DomuxInstruction] = (),
) -> tuple[str, ...]:
    """Return audit metadata for values explicitly ruled out by the user."""

    known_values = (
        *COLOR_RGB,
        "fan only", "heat cool", "cool", "heat", "dry", "fan", "auto",
        "low", "medium", "high",
    )
    tokens = {
        token
        for value in known_values
        if _value_is_explicitly_excluded(text, value)
        if (token := _operation_value_token(value)) is not None
    }
    for number in _numbers_in(normalize_text(text)):
        rendered = _canonical_number_token(number)
        if _value_is_explicitly_excluded(text, rendered):
            tokens.add(f"number:{rendered}")
    for instruction in source_instructions:
        if _value_is_explicitly_excluded(text, instruction.value):
            token = _operation_value_token(instruction.value)
            if token is not None:
                tokens.add(token)
    return tuple(sorted(tokens))


def _value_supported(text: str, value: str) -> bool:
    if value == "*":
        return True
    normalized = normalize_text(text)
    try:
        wanted = float(value)
    except ValueError:
        return _phrase_in(normalized, value)
    if any(math.isclose(wanted, number, rel_tol=0, abs_tol=0.001) for number in _numbers_in(normalized)):
        return True
    return math.isclose(wanted, 50.0, rel_tol=0, abs_tol=0.001) and _phrase_in(normalized, "halfway")


def _attribute_supported(text: str, instruction: DomuxInstruction) -> bool:
    attribute = normalize_text(instruction.attribute)
    if attribute == "*":
        return True
    normalized = normalize_text(text)
    if attribute == "brightness":
        return any(_phrase_in(normalized, term) for term in ("brightness", "bright", "brighter", "dimmer")) or (
            _phrase_in(normalized, "percent")
            and any(_phrase_in(normalized, term) for term in ("light", "lamp"))
        )
    if attribute in {"position", "openness"}:
        return _phrase_in(normalized, "position") or _phrase_in(normalized, "openness") or (
            any(_phrase_in(normalized, term) for term in ("percent", "halfway", "open", "close", "move", "adjust"))
            and any(_phrase_in(normalized, term) for term in ("curtain", "blind", "shade"))
        ) or any(_phrase_in(normalized, term) for term in ("open", "close"))
    if attribute == "temperature":
        return any(_phrase_in(normalized, term) for term in (
            "temperature", "degree", "degrees", "celsius", "warmer", "cooler"
        )) or (
            bool(_numbers_in(normalized))
            and any(_phrase_in(normalized, term) for term in ("ac", "air conditioner", "air conditioning"))
        )
    if attribute == "colortemperature":
        return "color temperature" in normalized or _phrase_in(normalized, "kelvin") or bool(
            re.search(r"\d+(?:\.\d+)?\s*k\b", normalized)
        )
    if attribute == "color":
        return _phrase_in(normalized, "color") or any(
            _phrase_in(normalized, color) for color in COLOR_RGB
        )
    if attribute == "mode":
        return _phrase_in(normalized, "mode") or (
            any(_phrase_in(normalized, mode) for mode in ("cool", "heat", "dry", "fan", "auto"))
            and any(_phrase_in(normalized, term) for term in ("ac", "air conditioner", "air conditioning"))
        )
    if attribute in {"windspeed", "wind speed", "fan speed"}:
        return any(_phrase_in(normalized, term) for term in ("wind", "fan", "low", "medium", "high"))
    return False


def _attribute_supported_for_entity(
    text: str,
    instruction: DomuxInstruction,
    entity: EntitySpec,
) -> bool:
    """Allow registry-domain inference only when the operation value is explicit."""

    if _attribute_supported(text, instruction):
        return True
    attribute = normalize_text(instruction.attribute)
    if attribute == "*":
        return True
    value_supported = _value_supported(text, instruction.value)
    unit_supported = _unit_supported(text, instruction)
    normalized = normalize_text(text)
    if entity.domain == "cover" and attribute in {"position", "openness"}:
        return value_supported and unit_supported and normalize_text(instruction.unit) == "percent"
    if entity.domain == "light" and attribute == "brightness":
        return value_supported and unit_supported and normalize_text(instruction.unit) == "percent"
    if entity.domain == "light" and attribute == "colortemperature":
        return value_supported and unit_supported and normalize_text(instruction.unit) == "kelvin"
    if entity.domain == "light" and attribute == "color":
        return value_supported and any(_phrase_in(normalized, color) for color in COLOR_RGB)
    if entity.domain == "climate" and attribute == "temperature":
        return value_supported and unit_supported and normalize_text(instruction.unit) == "celsius"
    if entity.domain == "climate" and attribute == "mode":
        return value_supported and any(
            _phrase_in(normalized, mode)
            for mode in ("cool", "heat", "dry", "fan only", "fan", "auto")
        )
    if entity.domain == "climate" and attribute in {"windspeed", "wind speed", "fan speed"}:
        return value_supported and unit_supported and normalize_text(instruction.unit) == "level"
    return False


def _relative_adjust_directions(text: str) -> frozenset[str]:
    """Return only relative directions explicitly authored by the user.

    A bare ``adjust`` carries no direction.  Treating it as evidence for either
    ``adjustUp`` or ``adjustDown`` would let a model choose a state transition
    that the user never authorized.
    """

    normalized = normalize_text(text)
    if _has_negated_direction(normalized):
        return frozenset()
    directions: set[str] = set()
    if re.search(
        r"\b(?:raise|increase)\b|"
        r"\b(?:brighter|warmer|higher)\b(?!\s+(?:floor|level|mode)\b)",
        normalized,
    ):
        directions.add("adjustup")
    if re.search(
        r"\blower\b(?!\s+(?:floor|level|mode)\b)|\bdecrease\b|"
        r"\b(?:dimmer|cooler)\b(?!\s+(?:floor|level|mode)\b)",
        normalized,
    ):
        directions.add("adjustdown")
    return frozenset(directions)


def _action_supported(text: str, instruction: DomuxInstruction) -> bool:
    normalized = normalize_text(text)
    if _has_negative_action_authorization(normalized) or _has_negated_direction(normalized):
        return False
    action = normalize_text(instruction.action)
    directional = _directional_actions(normalized)
    # A proposed ``set`` must not bypass opposing open/close or on/off clauses.
    # Such clauses describe more than one state transition and need an explicit
    # clarification even when the model happens to emit a value-bearing action.
    if len(directional) > 1:
        return False
    if action == "turnon":
        return directional == {"turnon"}
    if action == "turnoff":
        return directional == {"turnoff"}
    if action == "set":
        return any(_phrase_in(normalized, term) for term in (
            "set", "change", "make", "move", "adjust", "open", "raise", "lower", "use", "confirm"
        ))
    if action in {"adjustup", "adjustdown"}:
        return _relative_adjust_directions(normalized) == {action}
    return False


def _directional_actions(normalized_text: str) -> set[str]:
    """Extract action words without treating prepositions such as ``on Floor`` as actions."""

    result: set[str] = set()
    if re.search(r"\bon\s+(?:or|and\s*/?\s*or)\s+off\b|\boff\s+(?:or|and\s*/?\s*or)\s+on\b", normalized_text):
        result.update(("turnon", "turnoff"))
    if re.search(r"\bopen\s+(?:or|and\s*/?\s*or)\s+close\b|\bclose\s+(?:or|and\s*/?\s*or)\s+open\b", normalized_text):
        result.update(("turnon", "turnoff"))
    immediate = re.findall(r"\b(?:turn|switch)\s+(on|off)\b", normalized_text)
    result.update("turnon" if value == "on" else "turnoff" for value in immediate)
    # Support "switch that device off", but only when the direction ends the
    # clause.  This deliberately does not match "on the Ground Floor".  It is
    # collected even when another immediate action exists, so opposing clauses
    # cannot be silently collapsed into the first action.
    trailing = re.findall(
        r"\b(?:turn|switch)\s+(?:the\s+)?(?:that\s+)?(?:[a-z0-9]+\s+){0,8}"
        r"(on|off)(?=\s*(?:(?:right\s+)?now|please)?\s*(?:[,.!?;]|$))",
        normalized_text,
    )
    result.update("turnon" if value == "on" else "turnoff" for value in trailing)
    if _phrase_in(normalized_text, "open") or _phrase_in(normalized_text, "start"):
        result.add("turnon")
    if _phrase_in(normalized_text, "close") or _phrase_in(normalized_text, "shut"):
        result.add("turnoff")
    return result


def _unit_supported(text: str, instruction: DomuxInstruction) -> bool:
    unit = normalize_text(instruction.unit)
    if unit == "*":
        return True
    normalized = normalize_text(text)
    fahrenheit = _phrase_in(normalized, "fahrenheit") or bool(
        re.search(r"-?\d+(?:\.\d+)?\s*(?:°\s*)?f\b", normalized)
    )
    kelvin = _phrase_in(normalized, "kelvin") or bool(
        re.search(r"-?\d+(?:\.\d+)?\s*k\b", normalized)
    )
    celsius = _phrase_in(normalized, "celsius") or bool(
        re.search(r"-?\d+(?:\.\d+)?\s*(?:°\s*)?c\b", normalized)
    )
    if unit == "percent":
        return _phrase_in(normalized, "percent") or "%" in text or _phrase_in(normalized, "halfway")
    if unit == "celsius":
        if fahrenheit or kelvin:
            return False
        # In this English-only case, an otherwise unqualified "degree(s)" is
        # interpreted using the registered HA entity's Celsius capability.
        return celsius or any(_phrase_in(normalized, term) for term in ("degree", "degrees"))
    if unit == "kelvin":
        return kelvin and not (fahrenheit or celsius)
    if unit == "level":
        return any(_phrase_in(normalized, term) for term in ("level", "low", "medium", "high"))
    return False


def _distinct_named_matches(
    text: str,
    values: Iterable[str],
) -> tuple[tuple[int, int, str], ...]:
    """Return longest label matches while preserving occurrence provenance."""

    normalized = normalize_text(text)
    matches: list[tuple[int, int, str]] = []
    for value in {normalize_text(item) for item in values if normalize_text(item)}:
        for match in re.finditer(
            rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])",
            normalized,
        ):
            matches.append((match.start(), match.end(), value))
    kept = [
        item for item in matches
        if not any(
            other[:2] != item[:2]
            and other[0] <= item[0]
            and other[1] >= item[1]
            for other in matches
        )
    ]
    return tuple(sorted(kept))


def _distinct_named_values(text: str, values: Iterable[str]) -> frozenset[str]:
    """Return values with shorter matches removed only at overlapping spans."""

    return frozenset(
        value for _start, _end, value in _distinct_named_matches(text, values)
    )


def _negative_clause_is_selector_only(text: str) -> bool:
    """Return whether a negative clause only excludes a target selector."""

    normalized = normalize_text(text)
    if re.search(
        rf"\b(?:{EXECUTION_CONTROL_ACTION_RE}|want|need|have)\b",
        normalized,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:do\s+not|don't|dont|never|cannot|can't|cant|won't|wont|"
            r"wouldn't|wouldnt|shouldn't|shouldnt|couldn't|couldnt|mustn't|"
            r"mustnt|not)\s+(?:please\s+)?"
            rf"{CORRECTION_SELECTOR_ACTION_RE}\b",
            normalized,
        )
        or re.match(
            r"\s*(?:not|no|except|besides|other\s+than|anything\s+but|"
            r"everything\s+but|all\s+but|apart\s+from|instead\s+of|avoid|without)\b",
            normalized,
        )
    )


def _negative_clause_has_target_predicate(text: str) -> bool:
    """Require a negated target to be attached to a bounded predicate."""

    normalized = normalize_text(text)
    if re.search(
        rf"\b(?:{SELECTOR_ACTION_RE}|{EXECUTION_CONTROL_ACTION_RE}|want|need|have)\b",
        normalized,
    ):
        return True
    return bool(re.match(
        r"\s*(?:not|no|except|besides|other\s+than|anything\s+but|"
        r"everything\s+but|all\s+but|apart\s+from|instead\s+of|avoid|without)"
        r"\s+(?:(?:a|an|the|this|that|any|all|every|each|either)\s+)?"
        r"[a-z0-9]",
        normalized,
    ))


def _negative_clause_is_control_withdrawal(text: str) -> bool:
    """Return whether a complete targetless execution-control was withdrawn."""

    normalized = normalize_text(text).rstrip(" ,;:—.!?")
    negative_lead = (
        r"(?:do\s+not|don't|dont|never(?:\s+ever)?|cannot|can't|cant|won't|"
        r"wont|wouldn't|wouldnt|shouldn't|shouldnt|couldn't|couldnt|mustn't|"
        r"mustnt|(?:must|may|shall|should|can|could|will|would)\s+not)"
    )
    control = r"(?:proceed|execute|confirm|authorize|approve|dispatch|go\s+ahead|do\s+it)"
    return bool(re.fullmatch(
        rf"\s*{negative_lead}\s+(?:please\s+)?{control}\s*",
        normalized,
    ))


def _selector_span_is_negative_base(
    normalized_text: str,
    start: int,
    end: int | None = None,
    selector_spans: Sequence[tuple[int, int]] = (),
) -> bool:
    """Return whether a selector occurrence is inside the current negative clause.

    Inventory labels may be quoted or parenthesized, and a clarifying noun
    phrase may be more than a fixed number of words after ``not``.  Character
    distance is therefore not a safe scope boundary.  Keep negation active
    within the current clause and reset it only at an explicit hard boundary,
    positive contrast, or new imperative.
    """

    # Keep the complete prefix.  Punctuation is interpreted below only after
    # checking what appeared between the negator and the apparent restart.
    # That prevents emphasis such as ``not!!! Study`` and an entity ID such as
    # ``climate.study`` from silently terminating negative scope.
    prefix = normalized_text[:start]
    prefix_offset = 0
    quantified_exclusion = False
    command_quantified_exclusion = False
    anaphoric_exclusion = False
    contrastive_boundaries = tuple(re.finditer(r"\b(?:but|rather)\b", prefix))
    if contrastive_boundaries:
        boundary = contrastive_boundaries[-1]
        before = prefix[:boundary.start()]
        after = prefix[boundary.end():]
        command_quantified_exclusion = bool(
            re.search(r"\b(?:anything|everything)\b[\s,([{'\"]*$", before)
        )
        quantified_exclusion = bool(
            command_quantified_exclusion
            or re.search(
                r"\b(?:all|any|each|either|every)\b[^.!?;—]*$",
                before,
            )
        )
        # ``but by that I mean X`` can resolve an anaphor inside the
        # preceding negative clause; it is not a positive correction.  The
        # shorter ``but I mean X`` is also carried when the negative clause
        # ended in a generic/deictic target.  Stable-target corrections such
        # as ``do not use Living, but I mean Study`` remain positive.
        negative_before = re.search(
            r"\b(?:do\s+not|don't|dont|never|cannot|can't|cant|won't|wont|"
            r"wouldn't|wouldnt|shouldn't|shouldnt|couldn't|couldnt|mustn't|"
            r"mustnt|not|no|except|besides|avoid|without)\b",
            before,
        )
        anaphoric_exclusion = bool(
            negative_before
            and (
                re.match(
                    r"\s*by\s+(?:that|it|this)[\s,;:—-]*(?:i|we)\s+mean\b",
                    after,
                )
                or (
                    re.match(r"\s*(?:i|we)\s+mean\b", after)
                    and re.search(
                        rf"\b(?:it|that|this|{GENERIC_REFERENCE_NOUN_RE})\b"
                        r"[\s,()'\"—-]*$",
                        before,
                    )
                )
            )
        )
        prefix = prefix[boundary.end():]
        prefix_offset += boundary.end()
    negative_action = rf"(?:{SELECTOR_ACTION_RE}|{STATE_CHANGE_ACTION_RE})"
    strong_negative = (
        rf"(?:do\s+not|don't|dont|not)(?:\s+to)?\s+{negative_action}|"
        r"(?:do\s+not|don't|dont|never|cannot|can't|cant|won't|wont|"
        r"wouldn't|wouldnt|shouldn't|shouldnt|couldn't|couldnt|mustn't|mustnt)|"
        r"(?:except|besides|other\s+than|anything\s+but|apart\s+from|"
        r"everything\s+but|all\s+but|instead\s+of|avoid|without)|no"
    )
    carried_exclusion = quantified_exclusion or anaphoric_exclusion
    last_negative_start: int | None = None
    if carried_exclusion:
        tail = prefix
        tail_offset = prefix_offset
    else:
        negative_matches = tuple(re.finditer(
            rf"\b(?:{strong_negative})\b|\bnot\b",
            prefix,
        ))
        if not negative_matches:
            return False
        last_negative = negative_matches[-1]
        last_negative_start = prefix_offset + last_negative.start()
        tail = prefix[last_negative.end():]
        tail_offset = prefix_offset + last_negative.end()
        if last_negative.group(0) == "no" and re.match(
            r"\s*,\s*(?:actually|perhaps|rather|instead|sorry|"
            r"(?:i\s+)?(?:mean|want|choose|select|use)|just\s+use)\b",
            tail,
        ):
            return False
    # A shared negator can govern a coordinated verb list: ``do not use,
    # choose, or mean Study Lamp``.  In that construction the commas and
    # conjunctions introduce no positive imperative because no target occurs
    # between the verbs.  Keep the whole list negative; a later imperative
    # remains expressible with ``then``/``and then``, hard punctuation, or a
    # comma after an already named target.
    coordinated_delimiter = r"(?:,\s*(?:(?:and|or)\s+)?|(?:and|or)\s+)"
    coordinated_negative_actions = (
        None
        if carried_exclusion
        else re.match(
            rf"\s*(?:{coordinated_delimiter}(?:please\s+)?"
            rf"{negative_action}\b\s*)+",
            tail,
        )
    )
    restart_tail = (
        tail[coordinated_negative_actions.end():]
        if coordinated_negative_actions
        else tail
    )
    restart_tail_offset = tail_offset + (
        coordinated_negative_actions.end()
        if coordinated_negative_actions
        else 0
    )
    restart_command = RESTART_COMMAND_RE
    if quantified_exclusion and not command_quantified_exclusion and re.match(
        rf"\s*{restart_command}\b",
        tail,
    ):
        return False
    def has_prior_stable_target(relative_end: int) -> bool:
        absolute_end = restart_tail_offset + relative_end
        return any(
            tail_offset <= selector_start
            and selector_end <= absolute_end
            for selector_start, selector_end in selector_spans
        )

    def has_prior_target(relative_end: int) -> bool:
        absolute_end = restart_tail_offset + relative_end
        generic_target = re.search(
            rf"\b(?:anything|everything|it|that|this|{GENERIC_REFERENCE_NOUN_RE})\b",
            normalized_text[tail_offset:absolute_end],
        )
        return has_prior_stable_target(relative_end) or generic_target is not None

    state_restart = rf"{STATE_COMMAND_RE}\b"

    def positive_restart(
        relative_end: int,
        command_text: str,
        boundary_text: str,
    ) -> bool:
        complete_state_command = re.match(state_restart, command_text) is not None
        absolute_end = restart_tail_offset + relative_end
        negative_clause_start = (
            tail_offset if last_negative_start is None else last_negative_start
        )
        negative_clause = (
            ""
            if not carried_exclusion and last_negative_start is None
            else normalized_text[negative_clause_start:absolute_end]
        )
        stable_target = has_prior_stable_target(relative_end)
        # A colon can introduce the content being rejected ("I don't want
        # this: turn off Study").  Without a stable target on the negative
        # side it is not an authorization boundary.
        if boundary_text == ":" and not stable_target:
            return False
        explicit_sequence = bool(
            boundary_text == ";" or re.fullmatch(r"(?:and\s+then|then)", boundary_text)
        )
        if (
            complete_state_command
            and explicit_sequence
            and _negative_clause_is_control_withdrawal(negative_clause)
        ):
            return True
        if not _negative_clause_has_target_predicate(negative_clause):
            if carried_exclusion and stable_target and complete_state_command:
                return explicit_sequence
            return False
        if stable_target:
            if complete_state_command:
                return True
            return _negative_clause_is_selector_only(negative_clause)
        if not has_prior_target(relative_end):
            return False
        return complete_state_command

    def selector_only_restart(relative_end: int) -> bool:
        if (
            last_negative_start is None
            or not has_prior_stable_target(relative_end)
        ):
            return False
        absolute_end = restart_tail_offset + relative_end
        negative_clause = normalized_text[last_negative_start:absolute_end]
        return (
            _negative_clause_has_target_predicate(negative_clause)
            and _negative_clause_is_selector_only(negative_clause)
        )

    punctuation_restarts = re.finditer(
        rf"(?P<delimiter>[!?;,:—]|\.(?![a-z0-9]))\s*"
        rf"(?P<command>{restart_command})\b",
        restart_tail,
    )
    for restart in punctuation_restarts:
        if positive_restart(
            restart.start(),
            restart.group("command"),
            restart.group("delimiter"),
        ):
            return False
    for restart in re.finditer(
        rf"\b(?P<delimiter>and\s+then|then)\s+"
        rf"(?P<command>{restart_command})\b",
        restart_tail,
    ):
        if positive_restart(
            restart.start(),
            restart.group("command"),
            restart.group("delimiter"),
        ):
            return False
    for boundary in re.finditer(r"[!?;:—]|\.(?![a-z0-9])", restart_tail):
        suffix = restart_tail[boundary.end():]
        if selector_only_restart(boundary.start()) and re.fullmatch(
            r"[\s([{\"']*(?:(?:the|this|that|my|your|our)\s+)?",
            suffix,
        ):
            return False
    effective_end = end if end is not None else start
    effective_end = max(
        (
            selector_end
            for selector_start, selector_end in selector_spans
            if selector_start <= start
            and effective_end <= selector_end
        ),
        default=effective_end,
    )
    if (
        not carried_exclusion
        and selector_only_restart(len(restart_tail))
        and re.search(r"[,;:—]\s*(?:(?:the|this|that|my|your|our)\s+)?$", restart_tail)
        and re.match(r"\s+instead\b", normalized_text[effective_end:])
    ):
        return False
    return True


def _overlapping_selector_component(
    seed: tuple[int, int],
    selector_spans: Sequence[tuple[int, int]],
) -> frozenset[tuple[int, int]]:
    """Return the connected overlap component containing one selector span."""

    component = {seed}
    remaining = set(selector_spans) - component
    changed = True
    while changed:
        changed = False
        component_start = min(start for start, _end in component)
        component_end = max(end for _start, end in component)
        overlapping = {
            span
            for span in remaining
            if span[0] < component_end and component_start < span[1]
        }
        if overlapping:
            component.update(overlapping)
            remaining.difference_update(overlapping)
            changed = True
    return frozenset(component)


@lru_cache(maxsize=512)
def _postposed_no_correction_analysis(
    normalized_text: str,
    selector_spans: tuple[tuple[int, int], ...],
) -> tuple[tuple[tuple[tuple[int, int], str], ...], bool]:
    """Analyze every ``old target, no, replacement`` component once.

    A postposed ``no`` withdraws the preceding positive selector.  The next
    selector becomes positive only when the text between ``no,`` and that
    selector is a bounded correction introducer.  Unknown or negative
    replacement language therefore withdraws the old target without silently
    authorizing a new one.
    """

    spans = tuple(sorted(set(selector_spans)))
    # ASCII hyphens have already become spaces in ``normalize_text``.  Match
    # from the correction word itself so transcripts using ``-``, en/em dash,
    # ASCII/CJK punctuation, or ``no:`` all retain the same safe semantics.
    marker_pattern = POSTPOSED_NO_MARKER_RE
    correction_gap = (
        r"\s*(?:(?:actually|perhaps|rather|instead|sorry|please|just|the|this|that|"
        r"my|your|our)\s+|(?:(?:i|we)\s+)?(?:mean|want|choose|select|use)\s+)*"
    )
    state_command_gap = rf"\s*(?:{STATE_COMMAND_RE})\s+(?:(?:the|this|that|my|your|our)\s+)?"
    polite_selector_gap = (
        rf"\s*(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:just\s+)?"
        rf"{CORRECTION_SELECTOR_ACTION_RE}\s+"
        r"(?:(?:the|this|that|my|your|our)\s+)?"
    )
    withdrawn: set[tuple[int, int]] = set()
    replacements: set[tuple[int, int]] = set()
    unsafe_replacement = False
    for marker in re.finditer(marker_pattern, normalized_text):
        before = tuple(span for span in spans if span[1] <= marker.start())
        if not before:
            continue
        old_seed = max(before, key=lambda span: (span[1], span[0]))
        old_component = _overlapping_selector_component(old_seed, spans)
        old_was_positive_replacement = bool(old_component & replacements)
        old_was_withdrawn = bool(old_component & withdrawn)
        old_was_base_negative = (
            False
            if old_was_positive_replacement
            else _selector_span_is_negative_base(
                normalized_text,
                old_seed[0],
                old_seed[1],
                spans,
            )
        )
        if old_was_withdrawn or (
            old_was_base_negative and not old_was_positive_replacement
        ):
            # ``do not use A—no, B`` is not a positive A-to-B correction.
            continue
        after = tuple(span for span in spans if span[0] >= marker.end())
        if not after:
            # ``no`` may be correcting a value or mode rather than the target
            # (for example, ``Study Lamp to 30—no, perhaps 60``).  Without a
            # later selector there is no evidence of a target correction.
            continue
        replacement_seed = min(after, key=lambda span: (span[0], -span[1]))
        gap = normalized_text[marker.end():replacement_seed[0]]
        positive_replacement = (
            re.fullmatch(correction_gap, gap) is not None
            or re.fullmatch(state_command_gap, gap) is not None
            or re.fullmatch(polite_selector_gap, gap) is not None
        )
        withdrawn.update(old_component)
        replacements.difference_update(old_component)
        if not positive_replacement:
            # Once both sides name stable selectors, an unknown or negative
            # bridge cannot keep the old authorization alive or authorize the
            # replacement.  The request-level gate below also prevents a
            # clarification from expanding to some third, unmentioned target.
            unsafe_replacement = True
            continue
        replacement_component = _overlapping_selector_component(
            replacement_seed,
            spans,
        )
        replacements.update(replacement_component)
    roles = tuple(sorted(
        (*((span, "withdrawn") for span in withdrawn),
         *((span, "replacement") for span in replacements)),
    ))
    return roles, unsafe_replacement


def _postposed_no_correction_role(
    normalized_text: str,
    start: int,
    end: int,
    selector_spans: Sequence[tuple[int, int]],
) -> str | None:
    """Return the cached postposed-correction role of one selector span."""

    roles, _unsafe = _postposed_no_correction_analysis(
        normalized_text,
        tuple(sorted(set(selector_spans))),
    )
    current = (start, end)
    for span, role in roles:
        if span == current:
            return role
    return None


def _selector_span_is_negative(
    normalized_text: str,
    start: int,
    end: int | None = None,
    selector_spans: Sequence[tuple[int, int]] = (),
) -> bool:
    """Apply postposed corrections before the general negative-scope rules."""

    effective_end = start if end is None else end
    role = _postposed_no_correction_role(
        normalized_text,
        start,
        effective_end,
        selector_spans,
    )
    if role == "withdrawn":
        return True
    if role == "replacement":
        return False
    return _selector_span_is_negative_base(
        normalized_text,
        start,
        end,
        selector_spans,
    )


def _positive_named_matches(
    text: str,
    values: Iterable[str],
    *,
    selector_spans: Sequence[tuple[int, int]] = (),
) -> tuple[tuple[int, int, str], ...]:
    normalized = normalize_text(text)
    matches = _distinct_named_matches(normalized, values)
    known_spans = tuple(selector_spans) or tuple(
        (start, end) for start, end, _value in matches
    )
    return tuple(
        match
        for match in matches
        if not _selector_span_is_negative(
            normalized,
            match[0],
            match[1],
            known_spans,
        )
        and not (
            re.search(r"\bleave\s+(?:the\s+)?$", normalized[:match[0]])
            and re.match(
                r"(?:\s+[a-z0-9]+){0,4}\s+(?:unchanged|as\s+is)\b",
                normalized[match[1]:],
            )
        )
    )


def _label_is_grammar_collision(value: str) -> bool:
    """Return whether a metadata label is also ordinary command language."""

    words = re.findall(r"[a-z0-9]+", normalize_text(value))
    if not words or all(word.isdigit() for word in words):
        return True
    command_words = {
        "a", "about", "adjust", "an", "and", "any", "around", "at", "auto",
        "before", "between", "brightness", "bright", "brighter", "by", "change",
        "choose", "close", "color", "confirm", "cool", "cooler", "decrease",
        "degree", "degrees", "device", "dimmer", "do", "dry", "fan", "fahrenheit",
        "first", "for", "from", "halfway", "heat", "high", "in", "increase",
        "it", "kelvin", "last", "left", "level", "light", "lighting", "low",
        "lower", "make", "medium", "middle", "mode", "move", "next", "no", "not",
        "now", "of", "off", "on", "one", "open", "openness", "or", "other",
        "percent", "please", "position", "proceed", "raise", "right", "second",
        "select", "set", "side", "speed", "switch", "temperature", "that", "the",
        "third", "this", "to", "turn", "use", "value", "wait", "warmer", "white",
        "wind", "with", "without", "yes", "you",
        *{
            word
            for color in COLOR_RGB
            for word in re.findall(r"[a-z0-9]+", normalize_text(color))
        },
    }
    return all(word in command_words for word in words)


def _selector_match_is_anchored(
    normalized_text: str,
    start: int,
    end: int,
    value: str,
    category: str,
    registry: EntityRegistry,
    *,
    allow_topic: bool = True,
) -> bool:
    """Require inventory words to occur in a target-selector position."""

    if category in {"domain", "entity"}:
        return True
    prefix = normalized_text[:start]
    suffix = normalized_text[end:]
    device_labels = {
        *GENERIC_DEVICE_ALIASES,
        *(normalize_text(entity.device) for entity in registry.entities),
    }
    device_pattern = "|".join(
        re.escape(label) for label in sorted(device_labels, key=len, reverse=True)
    )
    locative_before = bool(re.search(
        r"\b(?:in|inside|at|on|from)\s+(?:the\s+)?$",
        prefix,
    ))
    device_after = bool(re.match(
        rf"\s+(?:(?:or|and)\s+(?:the\s+)?(?:[a-z0-9]+\s+){{0,3}})?"
        rf"(?:{device_pattern})(?![a-z0-9])",
        suffix,
    ))
    explicit_slot = bool(re.search(
        r"\b(?:room|floor|level|device)\s+(?:named\s+|called\s+)?$|"
        r"\b(?:in|on)\s+the\s+(?:room|floor|level)\s+$",
        prefix,
    )) or bool(re.match(r"\s+(?:room|floor|level|device)\b", suffix))
    topic_before_command = not re.search(
        r"\bfor\s+(?:the\s+)?$",
        prefix,
    ) and bool(re.match(
        r"\s*[,;:]\s*(?:please\s+)?(?:turn|switch|open|close|set|change|"
        r"make|adjust|raise|lower|increase|decrease|move|use|select|choose)\b",
        suffix,
    ))
    if category != "domain" and _label_is_grammar_collision(value):
        return locative_before or explicit_slot
    value_words = set(re.findall(r"[a-z0-9]+", value))
    if category == "room":
        if value_words.intersection({"room"}):
            return True
        if value in {
            "any", "first", "second", "third", "last", "middle", "next", "one",
            "right", "now", "please", "light", "that", "this", "other", "it",
        }:
            return locative_before or explicit_slot
        return (
            locative_before or device_after or explicit_slot
            or (allow_topic and topic_before_command)
        )
    if category == "floor":
        if value_words.intersection({"floor", "level", "storey", "story"}):
            return True
        return (
            locative_before or device_after or explicit_slot
            or (allow_topic and topic_before_command)
        )

    # Specific device names containing a device noun are self-anchoring.
    if any(_phrase_in(value, label) for label in GENERIC_DEVICE_ALIASES):
        return True
    command_before = bool(re.search(
        r"\b(?:turn|switch|open|close|set|change|make|move|adjust|raise|lower|"
        r"increase|decrease|use|select|choose|mean)\b(?:\s+(?:on|off))?"
        r"(?:\s+(?:the|that|this|my))?\s+$|"
        r"\b(?:the|that|this|my)\s+$",
        prefix,
    ))
    operation_after = bool(re.match(
        r"\s+(?:on|off|open|closed|to|by)(?:\b|$)",
        suffix,
    ))
    return command_before or operation_after or locative_before or explicit_slot


def _registry_selector_spans(
    text: str,
    registry: EntityRegistry,
) -> tuple[tuple[int, int], ...]:
    """Return actual stable inventory-selector spans for scope transitions."""

    normalized = normalize_text(text)
    labels = (
        ("room", tuple(entity.room for entity in registry.entities)),
        ("floor", tuple(entity.floor for entity in registry.entities)),
        ("device", tuple(
            entity.device
            for entity in registry.entities
            if normalize_text(entity.device) not in GENERIC_DEVICE_ALIASES
        )),
        ("alias", tuple(alias for entity in registry.entities for alias in entity.aliases)),
        ("entity", tuple(entity.entity_id for entity in registry.entities)),
    )
    spans: set[tuple[int, int]] = set()
    for category, values in labels:
        for start, end, value in _distinct_named_matches(normalized, values):
            if not _label_is_grammar_collision(value) or _selector_match_is_anchored(
                normalized,
                start,
                end,
                value,
                category,
                registry,
            ):
                spans.add((start, end))
    return tuple(sorted(spans))


def _final_authorization_clause(text: str, registry: EntityRegistry) -> str:
    """Return the clause after a complete, explicit positive restart.

    A negative command may name an old target before a new immediate command,
    for example ``do not turn off Living; turn off Study``.  The latter clause
    is independent authorization only when the negative clause already named
    a stable or generic target and the restart begins with a supported command.
    This target-before-boundary rule keeps emphasis such as ``do not! turn off
    Study`` inside negative scope.
    """

    normalized = normalize_text(text)
    negative_anchor = (
        r"\b(?:do\s+not|don't|dont|never(?:\s+ever)?|cannot|can't|cant|"
        r"won't|wont|wouldn't|wouldnt|shouldn't|shouldnt|couldn't|couldnt|"
        r"mustn't|mustnt|not|no|avoid|without|except|besides|other\s+than|"
        r"anything\s+but|everything\s+but|all\s+but|apart\s+from|instead\s+of)\b"
    )
    negatives = tuple(re.finditer(negative_anchor, normalized))
    if not negatives:
        return normalized
    last_negative = negatives[-1]
    selector_spans = _registry_selector_spans(normalized, registry)
    generic_target = re.compile(
        rf"\b(?:anything|everything|it|that|this|{GENERIC_REFERENCE_NOUN_RE})\b"
    )

    def has_prior_target(boundary_start: int) -> bool:
        if any(
            last_negative.end() <= selector_start
            and selector_end <= boundary_start
            for selector_start, selector_end in selector_spans
        ):
            return True
        return generic_target.search(
            normalized,
            last_negative.end(),
            boundary_start,
        ) is not None

    command = RESTART_COMMAND_RE
    state_command = rf"{STATE_COMMAND_RE}\b"

    def valid_restart(restart: re.Match[str]) -> bool:
        boundary_start = restart.start("boundary")
        if boundary_start < last_negative.end():
            return False
        complete_state_command = (
            re.match(state_command, restart.group("command")) is not None
        )
        negative_clause = normalized[last_negative.start():boundary_start]
        if not _negative_clause_has_target_predicate(negative_clause):
            return False
        stable_target = any(
            last_negative.end() <= selector_start
            and selector_end <= boundary_start
            for selector_start, selector_end in selector_spans
        )
        boundary_text = restart.group("boundary")
        if boundary_text == ":" and not stable_target:
            return False
        explicit_sequence = bool(
            boundary_text == ";"
            or re.fullmatch(r"(?:and\s+then|then)", boundary_text)
        )
        if (
            complete_state_command
            and explicit_sequence
            and _negative_clause_is_control_withdrawal(negative_clause)
        ):
            return True
        if stable_target:
            if complete_state_command:
                return True
            return _negative_clause_is_selector_only(negative_clause)
        if not has_prior_target(boundary_start):
            return False
        # A generic withdrawal can only be superseded by a complete new
        # state-changing command.  ``I mean Study`` merely identifies which
        # generic target was unwanted and cannot revive an earlier action.
        return complete_state_command

    restart_points: list[int] = []
    for restart in re.finditer(
        rf"(?P<boundary>[!?;,:—]|\.(?![a-z0-9]))\s*"
        rf"(?:(?:but|rather)\s+)?(?P<command>{command})\b",
        normalized,
    ):
        if valid_restart(restart):
            restart_points.append(restart.start("command"))
    for restart in re.finditer(
        rf"(?P<boundary>\b(?:and\s+then|then|but|rather)\b)\s+"
        rf"(?P<command>{command})\b",
        normalized,
    ):
        if valid_restart(restart):
            restart_points.append(restart.start("command"))
    if not restart_points:
        return normalized
    return normalized[min(restart_points):]


def _selector_label_evidence(
    text: str,
    label: str,
    category: str,
    registry: EntityRegistry,
) -> bool:
    """Recognize a label in a clarification without protocol-word collisions."""

    normalized = normalize_text(text)
    matches = _positive_named_matches(
        normalized,
        (label,),
        selector_spans=_registry_selector_spans(normalized, registry),
    )
    if not matches:
        return False
    if not _label_is_grammar_collision(label):
        return True
    return any(
        _selector_match_is_anchored(
            normalized, start, end, value, category, registry,
        )
        for start, end, value in matches
    )


def _targeted_named_values(text: str, values: Iterable[str]) -> frozenset[str]:
    """Extract enum values that immediately follow an explicit target cue."""

    normalized = normalize_text(text)
    targets: set[str] = set()
    for value in _distinct_named_values(normalized, values):
        if re.search(
            rf"\b(?:to|into)\b(?:\s+(?:the|a|an|target|new)){{0,3}}\s+{re.escape(value)}\b",
            normalized,
        ):
            targets.add(value)
    return frozenset(targets)


def _instruction_numeric_value(instruction: DomuxInstruction) -> float | None:
    try:
        return float(instruction.value)
    except ValueError:
        return None


def _operational_conflicts(text: str, instruction: DomuxInstruction) -> frozenset[str]:
    """Find explicit semantics that one proposed seven-slot tuple cannot bind.

    Presence checks alone are unsafe for corrections such as ``from 50 to 20``:
    both numbers occur, but only 20 is the requested target.  This routine is a
    deliberately small fail-closed binder for directional actions, target
    numbers, colors, HVAC modes, and incompatible unit families.
    """

    normalized = normalize_text(text)
    conflicts: set[str] = set()
    if _has_operation_value_exclusion(text):
        # The seven-slot contract cannot encode "anything except X".  A
        # concrete, non-excluded replacement must be supplied in clarification.
        conflicts.add("value")
    directional_actions = _directional_actions(normalized)
    proposed_action = normalize_text(instruction.action)
    if len(directional_actions) > 1:
        conflicts.add("action")
    if "turnoff" in directional_actions and proposed_action in {"set", "adjustup", "adjustdown"}:
        # HA set/adjust services for these domains can turn a light/cover/AC on;
        # they cannot stand in for an explicit off/close operation.
        conflicts.add("action")

    numbers = set(_numbers_in(normalized))
    if _phrase_in(normalized, "halfway"):
        numbers.add(50.0)
    target_numbers = {
        float(value)
        for value in re.findall(
            r"\b(?:to|at|around|about)\s+(?:around\s+|about\s+)?(-?\d+(?:\.\d+)?)\b",
            normalized,
        )
    }
    by_numbers = {
        float(value)
        for value in re.findall(r"\bby\s+(-?\d+(?:\.\d+)?)\b", normalized)
    }
    relative_numbers = {
        float(value)
        for value in re.findall(
            r"(?<![a-z0-9])-?(\d+(?:\.\d+)?)\s*(?:%|percent|degrees?|celsius)?\s+"
            r"(?:brighter|dimmer|warmer|cooler|higher|lower)\b",
            normalized,
        )
    }
    if proposed_action in {"adjustup", "adjustdown"}:
        target_numbers.update(by_numbers | relative_numbers)
    if re.search(r"\b(?:to|at)\s+halfway\b", normalized):
        target_numbers.add(50.0)
    range_expression = bool(re.search(
        r"\bbetween\s+-?\d+(?:\.\d+)?\s+and\s+-?\d+(?:\.\d+)?\b|"
        r"\bfrom\s+-?\d+(?:\.\d+)?\s+(?:through|until)\s+-?\d+(?:\.\d+)?\b",
        normalized,
    ))
    inequality_expression = bool(re.search(
        r"(?:\b(?:below|under|above|over)\b|\b(?:less|greater)\s+than\b|[<>])"
        r"\s*-?\d+(?:\.\d+)?\b",
        normalized,
    ))
    numeric_from_to = bool(re.search(
        r"\bfrom\s+-?\d+(?:\.\d+)?\s+to\s+-?\d+(?:\.\d+)?\b",
        normalized,
    ))
    proposed_number = _instruction_numeric_value(instruction)
    if "turnon" in directional_actions and proposed_action in {"set", "adjustup", "adjustdown"}:
        attribute = normalize_text(instruction.attribute)
        compatible_turn_on = (
            attribute in {"brightness", "color", "colortemperature"}
            or (proposed_action == "set" and attribute == "mode")
            or (
                attribute in {"position", "openness"}
                and (
                    (proposed_action == "set" and proposed_number is not None and proposed_number > 0)
                    or proposed_action == "adjustup"
                )
            )
        )
        if not compatible_turn_on:
            conflicts.add("action")
    absolute_numeric_cue = bool(target_numbers - by_numbers - relative_numbers) or bool(
        re.search(r"\b(?:to|at)\s+halfway\b", normalized)
    )
    if proposed_action in {"adjustup", "adjustdown"} and absolute_numeric_cue:
        conflicts.update(("action", "value"))
    if proposed_action == "set" and by_numbers:
        conflicts.update(("action", "value"))
    if (
        proposed_action in {"adjustup", "adjustdown"}
        and numbers
        and not (by_numbers or relative_numbers)
    ):
        conflicts.add("value")
    if (
        range_expression
        or inequality_expression
        or len(target_numbers) > 1
        or (len(numbers) > 1 and not numeric_from_to)
    ):
        conflicts.add("value")
    elif len(target_numbers) == 1:
        target = next(iter(target_numbers))
        if proposed_number is None or not math.isclose(proposed_number, target, rel_tol=0, abs_tol=0.001):
            conflicts.add("value")
    elif len(numbers) == 1:
        only = next(iter(numbers))
        if proposed_number is None or not math.isclose(proposed_number, only, rel_tol=0, abs_tol=0.001):
            conflicts.add("value")

    colors = _distinct_named_values(normalized, COLOR_RGB)
    color_targets = _targeted_named_values(normalized, COLOR_RGB)
    proposed_value = normalize_text(instruction.value)
    color_from_to = bool(re.search(
        r"\bfrom\b(?:\s+[a-z0-9]+){0,3}\s+(?:" + "|".join(
            re.escape(color) for color in sorted(COLOR_RGB, key=len, reverse=True)
        ) + r")\s+to\s+(?:" + "|".join(
            re.escape(color) for color in sorted(COLOR_RGB, key=len, reverse=True)
        ) + r")\b",
        normalized,
    ))
    if len(color_targets) > 1 or (len(colors) > 1 and not color_from_to):
        conflicts.add("value")
    elif len(color_targets) == 1 and proposed_value != next(iter(color_targets)):
        conflicts.add("value")

    hvac_values = ("fan only", "heat cool", "cool", "heat", "dry", "fan", "auto")
    mode_text = re.sub(r"\b(?:fan|wind)\s+speed\b", " ", normalized)
    modes = _distinct_named_values(mode_text, hvac_values)
    mode_targets = _targeted_named_values(mode_text, hvac_values)

    def canonical_mode(value: str) -> str:
        return "fan only" if normalize_text(value) == "fan" else normalize_text(value)

    proposed_mode = canonical_mode(instruction.value)
    canonical_targets = {canonical_mode(value) for value in mode_targets}
    mode_pattern = r"(?:fan\s+only|heat\s+cool|cool|heat|dry|fan|auto)"
    mode_from_to = bool(re.search(
        rf"\bfrom\s+{mode_pattern}\s+to\s+{mode_pattern}\b",
        mode_text,
    ))
    if len(canonical_targets) > 1 or (len(modes) > 1 and not mode_from_to):
        conflicts.add("value")
    elif len(canonical_targets) == 1 and proposed_mode != next(iter(canonical_targets)):
        conflicts.add("value")

    # A mode/color plus a numeric target is more than one operation for this
    # seven-slot contract; do not let either proposed tuple silently drop half.
    if numbers and modes and any(_phrase_in(normalized, cue) for cue in ("ac", "air conditioner", "mode")):
        conflicts.update(("attribute", "value"))
    if numbers and colors and any(_phrase_in(normalized, cue) for cue in ("light", "lamp", "color")):
        conflicts.update(("attribute", "value"))

    families: set[str] = set()
    color_temperature_text = re.sub(r"\bcolor\s+temperature\b", " ", normalized)
    light_cue = any(_phrase_in(normalized, cue) for cue in ("light", "lamp"))
    cover_cue = any(_phrase_in(normalized, cue) for cue in ("curtain", "blind", "shade"))
    climate_cue = any(_phrase_in(normalized, cue) for cue in ("ac", "air conditioner", "air conditioning"))
    if any(_phrase_in(normalized, cue) for cue in ("brightness", "brighter", "dimmer")) or (
        light_cue and ("%" in text or _phrase_in(normalized, "percent"))
    ):
        families.add("brightness")
    if any(_phrase_in(normalized, cue) for cue in ("position", "openness")) or (
        cover_cue and (numbers or _phrase_in(normalized, "halfway"))
    ):
        families.add("position")
    if _phrase_in(normalized, "color temperature") or _phrase_in(normalized, "kelvin") or bool(
        re.search(r"-?\d+(?:\.\d+)?\s*k\b", normalized)
    ):
        families.add("color_temperature")
    if colors or _phrase_in(color_temperature_text, "color"):
        families.add("color")
    temperature_text = re.sub(r"\bcolor\s+temperature\b", " ", normalized)
    if any(_phrase_in(temperature_text, cue) for cue in (
        "temperature", "celsius", "fahrenheit", "degree", "degrees",
    )) or (
        climate_cue and (numbers or any(_phrase_in(normalized, cue) for cue in ("warmer", "cooler")))
    ):
        families.add("temperature")
    if _phrase_in(normalized, "mode") or (climate_cue and modes):
        families.add("mode")
    if any(_phrase_in(normalized, cue) for cue in ("wind speed", "fan speed")):
        families.add("fan_speed")
    if len(families) > 1:
        conflicts.add("attribute")
    setter_cue = any(_phrase_in(normalized, cue) for cue in (
        "set", "change", "make", "adjust", "raise", "lower", "use",
    ))
    if proposed_action in {"turnon", "turnoff"} and setter_cue and families:
        conflicts.update(("action", "attribute"))

    fahrenheit = _phrase_in(normalized, "fahrenheit") or bool(
        re.search(r"-?\d+(?:\.\d+)?\s*(?:°\s*)?f\b", normalized)
    )
    kelvin = _phrase_in(normalized, "kelvin") or bool(
        re.search(r"-?\d+(?:\.\d+)?\s*k\b", normalized)
    )
    celsius = _phrase_in(normalized, "celsius") or bool(
        re.search(r"-?\d+(?:\.\d+)?\s*(?:°\s*)?c\b", normalized)
    )
    proposed_unit = normalize_text(instruction.unit)
    if (
        (proposed_unit == "celsius" and (fahrenheit or kelvin))
        or (proposed_unit == "kelvin" and (fahrenheit or celsius))
        or (proposed_unit not in {"*", "celsius", "kelvin"} and fahrenheit)
    ):
        conflicts.add("unit")
    return frozenset(conflicts)


def _source_selector(
    utterance: str,
    instruction: DomuxInstruction,
    registry: EntityRegistry,
) -> DomuxInstruction:
    """Erase model-proposed grounding fields that the user's words do not support."""

    normalized = normalize_text(utterance)
    fields = {slot: getattr(instruction, slot) for slot in SLOTS}

    device_is_registered = any(
        EntityRegistry._device_matches(entity, instruction.device)
        for entity in registry.entities
    )
    room_is_registered = instruction.room == "*" or any(
        normalize_text(entity.room) == normalize_text(instruction.room)
        for entity in registry.entities
    )
    floor_is_registered = instruction.floor == "*" or any(
        normalize_text(entity.floor) == normalize_text(instruction.floor)
        for entity in registry.entities
    )
    if not _phrase_in(normalized, instruction.device) or not device_is_registered:
        fields["device"] = "*"
    if not _phrase_in(normalized, instruction.room) or not room_is_registered:
        fields["room"] = "*"
    if not _phrase_in(normalized, instruction.floor) or not floor_is_registered:
        fields["floor"] = "*"
    if not _attribute_supported(utterance, instruction):
        fields["attribute"] = "*"
    return DomuxInstruction(**fields)


def _partial_registry_selector_slots(
    utterance: str,
    registry: EntityRegistry,
) -> tuple[str, ...]:
    """Find incomplete multi-token registry labels in the user's own text.

    Token overlap is useful for recognizing that a selector is present, but it
    is not enough to authorize a canonical room, floor, or device.  For
    example, ``dining light`` must not silently become ``Dining Room``.  The
    slot remains repairable through clarification instead of being guessed.
    """

    normalized = normalize_text(utterance)
    ignored = {
        "ac", "air", "conditioner", "conditioning", "device", "room", "floor",
        "level", "light", "lamp", "curtain", "blind", "shade",
        "auto", "cool", "dry", "fan", "heat", "high", "low", "medium",
        *COLOR_RGB,
    }
    labels: dict[str, set[str]] = {
        "device": {entity.device for entity in registry.entities},
        "room": {entity.room for entity in registry.entities},
        "floor": {entity.floor for entity in registry.entities},
    }
    full_spans: list[tuple[int, int]] = []
    for value in {value for values in labels.values() for value in values}:
        value_norm = normalize_text(value)
        full_spans.extend(
            (match.start(), match.end())
            for match in re.finditer(
                rf"(?<![a-z0-9]){re.escape(value_norm)}(?![a-z0-9])",
                normalized,
            )
        )
    unresolved: list[str] = []
    for slot, values in labels.items():
        for value in values:
            value_norm = normalize_text(value)
            words = [
                word for word in re.findall(r"[a-z0-9]+", value_norm)
                if word not in ignored and not word.isdigit()
            ]
            if len(re.findall(r"[a-z0-9]+", value_norm)) < 2 or not words:
                continue
            for word in words:
                word_matches = re.finditer(
                    rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])",
                    normalized,
                )
                if any(
                    not any(start <= match.start() and match.end() <= end for start, end in full_spans)
                    and _selector_match_is_anchored(
                        normalized,
                        match.start(),
                        match.end(),
                        word,
                        slot,
                        registry,
                        allow_topic=False,
                    )
                    for match in word_matches
                ):
                    unresolved.append(slot)
                    break
            if slot in unresolved:
                break
    return tuple(dict.fromkeys(unresolved))


def _unanchored_registry_selector_slots(
    utterance: str,
    registry: EntityRegistry,
) -> tuple[str, ...]:
    """Keep recognized-but-unparsed inventory words from being discarded.

    A registered label in ``in Study`` or ``Study light`` is a positive
    selector; one in a bounded negative scope is an exclusion.  Any remaining
    occurrence (for example ``for Reading`` or a room named ``Right`` in
    ``right now``) is semantically ambiguous and must force clarification
    instead of falling back to a globally unique device.
    """

    labels: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("room", "room", tuple(entity.room for entity in registry.entities)),
        ("floor", "floor", tuple(entity.floor for entity in registry.entities)),
        ("device", "device", tuple(
            entity.device
            for entity in registry.entities
            if normalize_text(entity.device) not in GENERIC_DEVICE_ALIASES
        )),
        ("alias", "entity", tuple(
            alias for entity in registry.entities for alias in entity.aliases
        )),
        ("entity", "entity", tuple(entity.entity_id for entity in registry.entities)),
    )
    unresolved: list[str] = []
    normalized = normalize_text(utterance)
    selector_spans = _registry_selector_spans(normalized, registry)
    occurrences = tuple(
        (start, end, value, category, slot)
        for category, slot, values in labels
        for start, end, value in _positive_named_matches(
            normalized,
            values,
            selector_spans=selector_spans,
        )
    )
    anchored_spans = tuple(
        (start, end)
        for start, end, value, category, _slot in occurrences
        if _selector_match_is_anchored(
            normalized, start, end, value, category, registry,
        )
    )
    for start, end, value, category, slot in occurrences:
        if _selector_match_is_anchored(
            normalized, start, end, value, category, registry,
        ):
            continue
        if any(
            outer_start <= start and end <= outer_end
            and (outer_start, outer_end) != (start, end)
            for outer_start, outer_end in anchored_spans
        ):
            continue
        unresolved.append(slot)
    return tuple(dict.fromkeys(unresolved))


def _positional_unknown_selector_words(
    utterance: str,
    registry: EntityRegistry,
) -> frozenset[str]:
    """Allow one user-authored noncanonical selector token in a target position.

    The token is still marked unresolved by the caller, so it can lead only to
    clarification.  This preserves inputs such as ``upstairs AC`` and
    ``downstairs temperature`` without letting arbitrary model slot text
    expand the executable grammar.
    """

    normalized = normalize_text(utterance)
    known_words: set[str] = set()
    for entity in registry.entities:
        for label in (
            entity.entity_id, entity.room, entity.floor, entity.device, *entity.aliases,
        ):
            known_words.update(re.findall(r"[a-z0-9]+", normalize_text(label)))
    for alias in GENERIC_DEVICE_ALIASES:
        known_words.update(re.findall(r"[a-z0-9]+", normalize_text(alias)))
    reserved = {
        "a", "an", "and", "at", "change", "close", "confirm", "decrease", "do",
        "increase", "it", "lower", "make", "move", "not", "open", "other",
        "please", "raise", "set", "switch", "that", "the", "this", "to", "turn",
        # Operation values, modifiers, and units can naturally precede an
        # attribute noun (``Cool mode``, ``40 percent brightness``).  They are
        # not positional selectors.
        "auto", "bright", "brighter", "celsius", "color", "cool", "cooler", "degree",
        "degrees", "dimmer", "dry", "fan", "fahrenheit", "halfway", "heat",
        "high", "kelvin", "low", "medium", "percent", "warmer", "white",
        *{
            word
            for color in COLOR_RGB
            for word in re.findall(r"[a-z0-9]+", normalize_text(color))
        },
    }
    target_nouns = sorted(
        {
            *(normalize_text(alias) for alias in GENERIC_DEVICE_ALIASES),
            "brightness",
            "color",
            "mode",
            "openness",
            "position",
            "temperature",
        },
        key=len,
        reverse=True,
    )
    unknown: set[str] = set()
    for noun in target_nouns:
        for match in re.finditer(
            rf"(?<![a-z0-9]){re.escape(noun)}(?![a-z0-9])",
            normalized,
        ):
            prefix = normalized[:match.start()]
            preceding = re.search(r"([a-z0-9]+)\s*$", prefix)
            if preceding is None:
                continue
            word = preceding.group(1)
            if noun == "mode" and re.search(
                rf"\bto\s+{re.escape(word)}\s*$",
                prefix,
            ):
                continue
            if word not in known_words and word not in reserved and not word.isdigit():
                unknown.add(word)
    return frozenset(unknown)


def _positional_unknown_attribute_words(
    utterance: str,
    registry: EntityRegistry,
) -> frozenset[str]:
    """Return unknown positional words immediately modifying an operation slot."""

    normalized = normalize_text(utterance)
    unknown = _positional_unknown_selector_words(utterance, registry)
    attribute_noun = r"(?:brightness|color|mode|openness|position|temperature)"
    return frozenset(
        word for word in unknown
        if re.search(
            rf"(?<![a-z0-9]){re.escape(word)}\s+{attribute_noun}(?![a-z0-9])",
            normalized,
        )
    )


def _unresolved_selector_phrase_slots(utterance: str) -> tuple[str, ...]:
    """Detect relational location phrases that do not name registry metadata."""

    normalized = normalize_text(utterance)
    qualifiers = (
        r"(?:my|your|our|their|his|her|this|that|one|other|right|left|current|"
        r"same|nearby|any|some|another|a|an)"
    )
    unresolved: list[str] = []
    if re.search(
        rf"\b(?:in|inside|at|from|for)\s+(?:the\s+)?{qualifiers}\s+room\b",
        normalized,
    ):
        unresolved.append("room")
    if re.search(
        rf"\b(?:in|inside|at|on|from|for)\s+(?:the\s+)?{qualifiers}\s+"
        rf"(?:floor|level|storey|story)\b",
        normalized,
    ):
        unresolved.append("floor")
    if re.search(r"\bin\s+the\s+middle\b", normalized):
        unresolved.extend(("room", "context"))
    return tuple(unresolved)


def _missing_required_slots(instruction: DomuxInstruction) -> tuple[str, ...]:
    action = normalize_text(instruction.action)
    missing: list[str] = []
    if action in {"set", "adjustup", "adjustdown"} and instruction.attribute == "*":
        missing.append("attribute")
    if action == "set" and instruction.value == "*":
        missing.append("value")
    if action == "set" and instruction.attribute not in {"*", "color", "mode"} and instruction.unit == "*":
        missing.append("unit")
    return tuple(missing)


def _has_uncertainty_or_conflict(utterance: str) -> bool:
    normalized = normalize_text(utterance)
    return "—" in utterance or any(_phrase_in(normalized, cue) for cue in (
        "perhaps", "maybe", "confirm", "which", "not sure", "not decided",
        "have not decided", "ask me", "do not choose", "do not guess", "or did",
    )) or _phrase_in(normalized, "or") or _phrase_in(normalized, "either") or bool(
        re.search(r"\b(?:not|except|instead\s+of)\s+(?:the\s+)?[a-z0-9]", normalized)
    )


def _has_negative_action_authorization(text: str) -> bool:
    """Detect negation scoped over execution, even with intervening pronouns."""

    normalized = normalize_text(text)
    action = (
        rf"(?:{STATE_CHANGE_ACTION_RE}|act|go\s+ahead|do\s+it|touch|confirm|"
        r"authorize|approve)"
    )
    bridge = r"(?:(?:you|me|us|i|we|to|need|want|let|allow|please|just)\s+){0,8}"
    return bool(re.search(
        rf"\b(?:do\s+not|don't|dont|never(?:\s+ever)?|rather\s+not)\s+{bridge}{action}\b|"
        rf"\b(?:must|may|shall|should|can|could|will|would)\s+not\s+{bridge}{action}\b|"
        rf"\bnot\s+(?:to\s+)?{action}\b|"
        rf"\b(?:refuse|forbid)\b.{{0,64}}\b{action}\b|"
        rf"\b(?:withdraw|revoke|deny)\b.{{0,40}}\b(?:permission|authorization|consent)\b"
        rf"(?:.{{0,64}}\b{action}\b)?|"
        rf"\bunder\s+no\s+circumstances\b.{{0,64}}\b{action}\b",
        normalized,
    ))


def _has_negated_direction(text: str) -> bool:
    """Reject a relative direction appearing under an explicit negative scope."""

    normalized = normalize_text(text)
    direction = (
        r"(?:raise|increase|raising|increasing|brighter|warmer|higher|"
        r"lower|decrease|lowering|decreasing|dimmer|cooler)"
    )
    bridge = r"(?:(?:make|move|adjust|set|turn)\s+)?(?:(?:it|that|this|the\s+\w+)\s+)?"
    qualifier = r"(?:(?:any|even|more|less|at\s+all)\s+){0,3}"
    return bool(re.search(
        rf"\b(?:do\s+not|don't|dont|never(?:\s+ever)?|no(?:\s+more)?|not|"
        rf"anything\s+but|other\s+than|besides|apart\s+from)\s+"
        rf"{bridge}{qualifier}{direction}\b|"
        rf"\b(?:avoid|without)\s+(?:making\s+(?:it|that|this)\s+)?{direction}\b|"
        rf"\bno\s+need\s+(?:to|for)\b.{{0,48}}\b{direction}\b",
        normalized,
    ))


def _generic_withdrawal_pattern(generic_target: str) -> str:
    """Build the shared generic-target withdrawal grammar."""

    selection_action = r"(?:use|select|choose|touch|act\s+on)"
    intent_verb = (
        rf"(?:{selection_action}|(?:want|need|have)"
        rf"(?:\s+(?:you|me|us|them|him|her))?"
        rf"(?:\s+to\s+{selection_action})?)"
    )
    negative_lead = (
        r"(?:do\s+not|don't|dont|never(?:\s+ever)?|cannot|can't|cant|won't|"
        r"wont|wouldn't|wouldnt|shouldn't|shouldnt|couldn't|couldnt|mustn't|"
        r"mustnt|(?:must|may|shall|should|can|could|will|would)\s+not)"
    )
    speaker = r"(?:(?:i|we|you)\s+)?"
    no_relation = rf"(?:for|of|to\s+{selection_action})"
    return (
        rf"\b(?:{negative_lead}\s+{intent_verb}|"
        rf"{speaker}(?:have|want|need)\s+no"
        rf"(?:\s+(?:need|use)\s+{no_relation})?|"
        rf"no\s+(?:need|use)\s+{no_relation}|"
        rf"{speaker}(?:need|want)\s+not\s+(?:to\s+)?{selection_action}|"
        rf"{speaker}(?:would|should|could)\s+rather\s+not\s+{selection_action})"
        rf"\s+{generic_target}\b"
    )


def _has_generic_withdrawal(text: str) -> bool:
    """Detect withdrawal/negative possession of a generic device target."""

    normalized = normalize_text(text)
    generic_target = (
        rf"(?:anything|everything|it|that|this|(?:(?:any|a|an|the|this|that|one|all|every|"
        rf"each|either)\s+)?{GENERIC_REFERENCE_NOUN_RE})"
    )
    return bool(re.search(_generic_withdrawal_pattern(generic_target), normalized))


def _has_generic_domain_withdrawal(
    text: str,
    noun_pattern: str,
    *,
    selector_spans: Sequence[tuple[int, int]] = (),
) -> bool:
    """Return whether a withdrawal directly governs one generic domain noun."""

    normalized = normalize_text(text)
    generic_target = (
        rf"(?:(?:any|a|an|the|this|that|one|all|every|each|either)\s+)?"
        rf"(?:{noun_pattern})"
    )
    pattern = _generic_withdrawal_pattern(generic_target)
    for match in re.finditer(pattern, normalized):
        suffix = normalized[match.end():]
        qualified = re.match(
            r"\s+(?:but|that|which|in|on|at|from|called|named|i\s+mean|"
            r"we\s+mean|namely)\b|"
            r"\s*,\s*(?:namely|called|named|i\s+mean|we\s+mean)\b|"
            r"\s*[—(]\s*[a-z0-9]",
            suffix,
        )
        if qualified and any(
            selector_start >= match.end()
            and not re.search(
                r"[.!?;]",
                normalized[match.end():selector_start],
            )
            for selector_start, _selector_end in selector_spans
        ):
            continue
        return True
    return False


def _has_negative_selector_correction(
    utterance: str,
    registry: EntityRegistry,
) -> bool:
    """Detect a named-selector correction whose bridge is not safely positive."""

    normalized = normalize_text(utterance)
    spans = tuple(_registry_selector_spans(normalized, registry))
    _roles, unsafe = _postposed_no_correction_analysis(normalized, spans)
    return unsafe


def _has_negative_or_cancelled_intent(
    utterance: str,
    registry: EntityRegistry | None = None,
) -> bool:
    normalized = normalize_text(utterance)
    ambiguous_negative_correction = (
        registry is not None
        and _has_negative_selector_correction(normalized, registry)
    )
    negative_imperative = (
        _has_negative_action_authorization(normalized)
        or _has_negated_direction(normalized)
        or bool(re.search(
        r"\b(?:do\s+not|don't|dont|never(?:\s+ever)?)\s+"
        rf"{STATE_CHANGE_ACTION_RE}\b",
        normalized,
        ))
    )
    withdrawn_request = bool(re.search(
        r"\b(?:i\s+)?(?:do\s+not|don't|dont)\s+(?:want|need)\b.{0,48}\b"
        r"(?:turn|switch|open|close|set|change|make|move|adjust|raise|lower|increase|"
        r"decrease|brighter|dimmer|warmer|cooler|higher)\b|"
        r"\bno\s+need\s+to\s+(?:turn|switch|open|close|set|change|make|move|adjust|raise|lower|"
        r"increase|decrease)\b|"
        r"\b(?:refrain\s+from|avoid)\s+(?:turning|switching|opening|closing|setting|"
        r"changing|making|moving|adjusting|raising|lowering)\b|"
        r"\bwithout\s+(?:turning|switching|opening|closing|setting|changing|making|"
        r"moving|adjusting|raising|lowering)\b|"
        r"\bjust\s+kidding\b|"
        r"\b(?:scratch|nix|disregard|ignore)\s+(?:it|that|this)\b|"
        r"\b(?:i\s+)?changed?\s+my\s+mind\b|\bhold\s+on\b|"
        r"\b(?:actually\s+)?(?:never\s+mind|cancel(?:\s+(?:it|that|this|the\s+request))?)\b",
        normalized,
    ))
    negative_preference = bool(re.search(
        r"\b(?:(?:i|we)\s+)?(?:do\s+not|don't|dont|cannot|can't|cant|"
        r"won't|wont|wouldn't|wouldnt|shouldn't|shouldnt|couldn't|couldnt|"
        r"mustn't|mustnt|(?:can|could|would|should|will|must|may)\s+not)\s+"
        r"(?:want|need|have)\b",
        normalized,
    ))
    terminal_cancel = bool(re.fullmatch(
        r"(?:cancel|never mind|do nothing|stop|wait|forget it|"
        r"(?:actually\s+)?(?:do not|don't|dont))[.!]?",
        normalized,
    ))
    trailing_cancel = bool(re.search(
        r"(?:^|[,;:—-]\s*)(?:(?:(?:please|i\s+mean)\s+)?no(?:\s+please)?|"
        r"i\s+want\s+no|not now|forget it|wait|stop|don't bother|dont bother|"
        r"(?:i\s+)?(?:do not|don't|dont)\s+(?:want|need)\s+(?:it|that|this)|"
        r"(?:actually\s+|please\s+)?(?:do not|don't|dont))[.!]?\s*$",
        normalized,
    ))
    suspensive_wait = bool(
        re.search(r"\bwait\b", normalized)
        and not re.search(
            r"\bwait\b.{0,64}\b(?:actually|perhaps|rather|instead|confirm)\b",
            normalized,
        )
    )
    return (
        negative_imperative
        or _has_generic_withdrawal(normalized)
        or withdrawn_request
        or negative_preference
        or terminal_cancel
        or trailing_cancel
        or suspensive_wait
        or ambiguous_negative_correction
    )


def _has_unsupported_condition_or_time(utterance: str) -> bool:
    """Reject modifiers this immediate, single-action executor cannot honor."""

    normalized = normalize_text(utterance)
    # "Confirm ... before acting" describes this safety protocol itself, not
    # a delayed execution condition.  Keep it available to clarification.
    normalized = re.sub(
        r"\bbefore\s+(?:acting|executing|proceeding|you\s+act|you\s+execute)\b",
        " ",
        normalized,
    )
    number_word = (
        r"(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"few|couple(?:\s+of)?|\d+(?:\.\d+)?)"
    )
    return bool(re.search(
        r"^\s*wait\b|"
        r"\b(?:if|unless|when|whenever|once|while|after|before|until|provided|providing|assuming)\b|"
        r"\b(?:as\s+long\s+as|so\s+long\s+as|in\s+case|depending\s+on|subject\s+to)\b|"
        r"\bas\s+soon\s+as\b|"
        r"\b(?:during|upon\s+arrival|at\s+dusk|at\s+dawn|as\s+needed|momentarily)\b|"
        r"\b(?:tomorrow|tonight|later|today|noon|midnight|sunrise|sunset|"
        r"morning|afternoon|evening)\b|"
        r"\b(?:for\s+now|from\s+now\s+on|now\s+and\s+then)\b|"
        r"\b(?:this|next)\s+(?:morning|afternoon|evening|night|week|month|year)\b|"
        r"\bon\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"weekday|weekend)\b|"
        r"\b(?:every|each)\s+(?:morning|afternoon|evening|night|day|week|month|"
        r"weekday|weekend)\b|"
        r"\b(?:daily|nightly|weekly|monthly|briefly|temporarily|momentarily|schedule|scheduled)\b|"
        rf"\b(?:in|for)\s+{number_word}\s+(?:seconds?|minutes?|hours?|days?|weeks?)\b|"
        r"\b(?:in|for)\s+(?:half|a\s+half|quarter|a\s+quarter)\s+"
        r"(?:of\s+)?(?:an?\s+)?(?:hour|day)\b|"
        r"\bat\s+(?:noon|midnight|sunrise|sunset)\b|"
        r"\bat\s+\d{1,2}:\d{2}(?:\s*(?:am|pm))?\b|"
        r"\bat\s+\d{1,2}\s*(?:am|pm)\b|"
        r"\bat\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"(?:\s+o'?clock)?\b",
        normalized,
    ))


def _has_informational_request(utterance: str) -> bool:
    """Distinguish questions about an action from an authorization to execute it."""

    normalized = re.sub(r"^[^a-z0-9]+", "", normalize_text(utterance))
    return bool(re.match(
        r"^(?:(?:should|would|could|can|may)\s+i\b|"
        r"(?:i|we|you)\s+(?:do|did)\s+(?:please\s+)?"
        r"(?:turn|switch|open|close|set|change|make|move|adjust|raise|lower|increase|decrease)\b|"
        r"(?:do|did|have|has)\s+(?:you|we)\s+(?:want|need|have|mean|intend|plan|"
        r"expect|already|ever|turn|turned|switch|switched|open|opened|close|closed|"
        r"set|change|changed|make|made|adjust|adjusted)\b|"
        r"(?:should|would|could|can|may|will)\s+we\b|"
        r"(?:should|would|could|can|may|will)\s+you\s+"
        r"(?:want|need|have|mean|intend|plan|expect)\b|"
        r"do\s+i\s+(?:turn|switch|open|close|set|change|make|move|adjust)\b|"
        r"(?:would|could)\s+it\s+be\s+(?:safe|okay|ok|wise|advisable)\b|"
        r"is\s+it\s+(?:safe|okay|ok|wise|advisable)\b|"
        r"do\s+you\s+(?:recommend|suggest|think)\b|"
        r"do\s+i\s+(?:need|have)\s+to\b|"
        r"are\s+you\s+going\s+to\b|"
        r"why\s+(?:should|would|could|can|do)\s+i\b|"
        r"(?:please\s+)?explain\s+why\b|"
        r"what\s+if\b|"
        r"what\s+(?:happens|would\s+happen)\b|"
        r"(?:tell|show|explain)\s+me\s+(?:how|whether|what)\b|"
        r"how\s+(?:do|can|should|would)\s+i\b)",
        normalized,
    ))


def _has_supported_request_grammar(
    utterance: str,
    registry: EntityRegistry,
    source_instructions: Sequence[DomuxInstruction] = (),
) -> bool:
    """Accept only the documented immediate, single-operation language.

    Negative and temporal detectors provide useful reason codes, but they can
    never enumerate every English paraphrase.  This bounded vocabulary is the
    fail-closed backstop: registry labels, numeric values, and the small Domux
    operation language are accepted; an unconsumed word requires a new request
    rather than being silently discarded from an executable plan.
    """

    selector_words: set[str] = set()
    for entity in registry.entities:
        for label in (
            entity.entity_id, entity.room, entity.floor, entity.device, *entity.aliases,
        ):
            selector_words.update(re.findall(r"[a-z0-9]+", normalize_text(label)))
    for alias in GENERIC_DEVICE_ALIASES:
        selector_words.update(re.findall(r"[a-z0-9]+", normalize_text(alias)))
    # Permit an unknown single-token mode only when the user's own syntax binds
    # it as ``to <value> mode`` and the model puts that exact token in value.
    # Attribute/unit text never extends the grammar: otherwise an untrusted raw
    # output could launder an ignored condition into a replaceable source slot.
    normalized_utterance = normalize_text(utterance)
    source_mode_words = {
        value
        for instruction in source_instructions
        if normalize_text(instruction.attribute) == "mode"
        if re.fullmatch(r"[a-z0-9]+", (value := normalize_text(instruction.value)))
        if re.search(
            rf"\bto\s+{re.escape(value)}\s+mode\b",
            normalized_utterance,
        )
    }
    positional_selector_words = _positional_unknown_selector_words(utterance, registry)

    request_words = {
        # Immediate command and clarification-protocol framing.
        "adjust", "ask", "acting", "avoid", "before", "change", "check", "choose",
        "close", "confirm", "decided", "decrease", "did", "do", "execute",
        "guess", "increase", "lower", "make", "mean", "move", "need", "open",
        "proceed", "raise", "set", "switch", "then", "turn", "value", "wait", "want",
        # Operation attributes, values, and units.
        "auto", "blue", "bright", "brighter", "brightness", "celsius", "color",
        "cool", "cooler", "degree", "degrees", "dimmer", "dry", "fan", "green",
        "fahrenheit", "halfway", "heat", "high", "kelvin", "level", "low", "medium", "mode",
        "off", "on", "openness", "percent", "position", "red", "speed",
        "temperature", "warm", "warmer", "white", "wind", "yellow",
        # Selector and polite-command glue.  These words carry no operation by
        # themselves; the source-to-slot checks still require positive evidence.
        "about", "ac", "air", "am", "and", "around", "at",
        "any", "anything", "apart", "besides", "but", "by", "can", "cannot",
        "conditioner", "could", "curtain", "device",
        "floor", "for", "from", "have", "i", "in", "instead", "it", "its", "just",
        "light", "me", "my", "no", "not", "now", "of",
        "may", "middle", "must", "never", "one", "or", "other", "perhaps",
        "please", "right", "room", "shall", "should", "side",
        "something", "than",
        "sure", "talked", "that", "the", "this", "to",
        "use", "we", "which", "will", "would", "you",
        *{word for color in COLOR_RGB for word in normalize_text(color).split()},
    }
    allowed = selector_words | request_words | source_mode_words | positional_selector_words
    normalized = normalized_utterance.replace("don't", "do not").replace("dont", "do not")
    words = re.findall(r"[a-z0-9]+", normalized)
    return all(
        word in allowed or word.isdigit() or re.fullmatch(r"\d+(?:\d+)?k", word)
        for word in words
    )


def _has_direct_generic_exclusion(
    text: str,
    noun_pattern: str,
    *,
    selector_spans: Sequence[tuple[int, int]] = (),
) -> bool:
    """Return whether an exclusion directly governs a generic noun phrase."""

    normalized = normalize_text(text)
    direct_marker = (
        r"(?:not(?:\s+in)?|no|except(?:\s+for)?|besides|other\s+than|"
        r"anything\s+but|everything\s+but|all\s+but|apart\s+from|"
        r"instead\s+of|avoid|without)"
        r"(?:\s+(?:using|selecting|choosing|touching))?"
    )
    negative_command = (
        r"(?:(?:do\s+not|don't|dont|never|cannot|can't|cant|won't|wont|"
        r"wouldn't|wouldnt|shouldn't|shouldnt|couldn't|couldnt|mustn't|mustnt|"
        r"(?:must|may|shall|should|can|could|will|would)\s+not)"
        r"(?:\s+(?:want|need)(?:\s+(?:you|me|us|them|him|her))?\s+to)?\s+"
        r"(?:use|select|choose|touch|act\s+on))"
    )
    pattern = (
        rf"\b(?:{direct_marker}|{negative_command})\s+"
        rf"(?:(?:a|an|the|this|that|any|all|every|each|either)\s+)?"
        rf"(?:{noun_pattern})\b"
    )
    for match in re.finditer(pattern, normalized):
        suffix = normalized[match.end():]
        qualified = re.match(
            r"\s+(?:but|that|which|in|on|at|from|called|named|i\s+mean|"
            r"we\s+mean|namely)\b|"
            r"\s*,\s*(?:namely|called|named|i\s+mean|we\s+mean)\b|"
            r"\s*[—(]\s*[a-z0-9]",
            suffix,
        )
        if qualified and any(
            selector_start >= match.end()
            and not re.search(
                r"[.!?;]",
                normalized[match.end():selector_start],
            )
            for selector_start, _selector_end in selector_spans
        ):
            continue
        return True
    return False


def _has_unresolved_generic_exclusion(
    text: str,
    registry: EntityRegistry | None = None,
) -> bool:
    """Reject generic exclusions that do not identify a stable entity ID."""

    normalized = normalize_text(text)
    selector_spans = (
        _registry_selector_spans(normalized, registry)
        if registry is not None
        else ()
    )
    return _has_direct_generic_exclusion(
        normalized,
        rf"(?:anything|everything|{GENERIC_REFERENCE_NOUN_RE})",
        selector_spans=selector_spans,
    ) or bool(re.search(
        r"\bnot\s+(?:this|that)\b|"
        rf"\b(?:leave|keep)\s+(?:(?:this|that|the)\s+)?{GENERIC_REFERENCE_NOUN_RE}\s+"
        r"(?:unchanged|as\s+is)\b|"
        rf"\b(?:use|select|choose|mean)\s+{OTHER_REFERENCE_RE}",
        normalized,
    ))


def _excluded_generic_domains(
    text: str,
    registry: EntityRegistry,
) -> frozenset[str]:
    labels = {
        domain: tuple(
            alias for alias, alias_domain in GENERIC_DEVICE_ALIASES.items()
            if alias_domain == domain
        )
        for domain in SUPPORTED_DOMAINS
    }
    selector_spans = _registry_selector_spans(text, registry)
    return frozenset(
        domain for domain, names in labels.items()
        if any(
            _has_direct_generic_exclusion(
                text,
                re.escape(normalize_text(name)),
                selector_spans=selector_spans,
            )
            or _has_generic_domain_withdrawal(
                text,
                re.escape(normalize_text(name)),
                selector_spans=selector_spans,
            )
            for name in names
        )
    )


def _has_universal_generic_exclusion(text: str, registry: EntityRegistry) -> bool:
    """Return whether a withdrawal excludes every supported device domain."""

    normalized = normalize_text(text)
    selector_spans = _registry_selector_spans(normalized, registry)
    generic_target = (
        r"(?:anything|everything|(?:(?:any|a|an|the|this|that|one|all|every|"
        r"each|either)\s+)?devices?)"
    )
    return bool(re.search(
        _generic_withdrawal_pattern(generic_target),
        normalized,
    )) or _has_direct_generic_exclusion(
        normalized,
        r"(?:anything|everything|devices?)",
        selector_spans=selector_spans,
    )


def _explicit_operational_requirements(utterance: str) -> frozenset[str]:
    """Return operational slots explicitly present in the user's own words.

    This is a fail-closed preservation check, not a replacement semantic
    parser.  Its purpose is to stop a model proposal from silently dropping a
    number, unit, or attribute that would materially change the operation.
    """

    normalized = normalize_text(utterance)
    requirements: set[str] = set()
    has_operational_number = bool(re.search(
        r"\b(?:to|by|at|around|about)\s+-?\d+(?:\.\d+)?\b|"
        r"\b(?:brightness|position|openness|temperature|degrees?|celsius|kelvin)\b"
        r"(?:\s+[a-z]+){0,3}\s+-?\d+(?:\.\d+)?\b",
        normalized,
    )) or _phrase_in(normalized, "halfway")
    percent = "%" in utterance or _phrase_in(normalized, "percent") or _phrase_in(normalized, "halfway")
    celsius = any(_phrase_in(normalized, term) for term in ("celsius", "degree", "degrees"))
    kelvin = _phrase_in(normalized, "kelvin") or bool(re.search(r"\d+(?:\.\d+)?\s*k\b", normalized))
    if has_operational_number:
        requirements.add("value")
    if percent or celsius or kelvin:
        requirements.update(("attribute", "unit", "value"))
    if any(_phrase_in(normalized, term) for term in (
        "brightness", "bright", "dimmer", "position", "openness", "temperature",
        "color temperature", "wind speed", "fan speed",
    )):
        requirements.add("attribute")
    named_color = any(_phrase_in(normalized, color) for color in COLOR_RGB)
    light_cue = any(_phrase_in(normalized, term) for term in ("light", "lamp"))
    color_operation = any(_phrase_in(normalized, term) for term in ("make", "color")) or bool(
        re.search(r"\bto\b(?:\s+[a-z0-9]+){0,4}\s+(?:" + "|".join(
            re.escape(normalize_text(color)) for color in COLOR_RGB
        ) + r")\b", normalized)
    )
    if named_color and light_cue and color_operation:
        requirements.update(("attribute", "value"))
    mode_cue = _phrase_in(normalized, "mode")
    named_mode = any(_phrase_in(normalized, mode) for mode in ("cool", "heat", "dry", "fan only", "auto"))
    climate_cue = any(_phrase_in(normalized, term) for term in ("ac", "air conditioner", "air conditioning"))
    mode_operation = mode_cue or bool(re.search(
        r"\b(?:to|use)\b(?:\s+[a-z0-9]+){0,4}\s+(?:cool|heat|dry|fan\s+only|auto)\b",
        normalized,
    ))
    if mode_cue or (named_mode and climate_cue and mode_operation):
        requirements.add("attribute")
    if named_mode and climate_cue and mode_operation:
        requirements.add("value")
    return frozenset(requirements)


def _label_is_excluded(
    text: str,
    label: str,
    *,
    selector_spans: Sequence[tuple[int, int]] = (),
) -> bool:
    normalized_label = normalize_text(label)
    if not normalized_label:
        return False
    normalized = normalize_text(text)
    matches = _distinct_named_matches(normalized, (normalized_label,))
    known_spans = tuple(selector_spans) or tuple(
        (start, end) for start, end, _value in matches
    )
    correction_occurrences = tuple(
        (start, end, role)
        for start, end, _value in matches
        if (role := _postposed_no_correction_role(
            normalized,
            start,
            end,
            known_spans,
        )) is not None
    )
    if correction_occurrences:
        latest_start, _latest_end, latest_role = max(
            correction_occurrences,
            key=lambda item: (item[1], item[0]),
        )
        explicitly_scoped = latest_role == "withdrawn" or any(
            start > latest_start
            and _selector_span_is_negative_base(
                normalized,
                start,
                end,
                known_spans,
            )
            for start, end, _value in matches
        )
    else:
        explicitly_scoped = any(
            _selector_span_is_negative(
                normalized,
                start,
                end,
                known_spans,
            )
            for start, end, _value in matches
        )
    return explicitly_scoped or bool(re.search(
        rf"\b(?:not(?:\s+in)?|except|besides|other\s+than|anything\s+but|"
        rf"apart\s+from|instead\s+of)\s+(?:the\s+)?{re.escape(normalized_label)}\b|"
        rf"\bleave\s+(?:the\s+)?{re.escape(normalized_label)}\b.*\bunchanged\b",
        normalized,
    ))


def _positive_target_domains(text: str, registry: EntityRegistry) -> frozenset[str]:
    """Return domains named by positive domain/entity/alias/device selectors.

    Room and floor labels deliberately do not count: they can be shared across
    domains and therefore cannot by themselves reverse a domain-wide exclusion.
    """

    normalized = normalize_text(text)
    selector_spans = _registry_selector_spans(normalized, registry)
    state_command = re.match(rf"{STATE_COMMAND_RE}\b", normalized)
    spatial_spans = tuple(
        (start, end)
        for values in (
            tuple(entity.room for entity in registry.entities),
            tuple(entity.floor for entity in registry.entities),
        )
        for start, end, _value in _distinct_named_matches(normalized, values)
    )

    target_modifiers = tuple({
        normalize_text(value)
        for entity in registry.entities
        for value in (entity.room, entity.floor)
        if normalize_text(value)
    })
    target_fillers = {
        "a", "all", "an", "any", "each", "either", "every", "my", "one",
        "our", "that", "the", "this", "your",
    }
    target_nouns = {
        "ac", "acs", "air", "blind", "blinds", "conditioner", "conditioners",
        "conditioning", "cover", "covers", "curtain",
        "curtains", "device", "devices", "lamp", "lamps", "light", "lighting",
        "lights", "shade", "shades",
    }
    operation_bridge_words = {
        "about", "around", "at", "auto", "blue", "bright", "brighter",
        "brightness", "by", "celsius", "close", "closed", "color", "cool",
        "cooler", "decrease", "degree", "degrees", "dimmer", "dry", "fan",
        "fahrenheit", "for", "from", "green", "halfway", "heat", "high", "in",
        "increase", "kelvin", "level", "low", "lower", "medium", "mode", "of",
        "on", "open", "openness", "percent", "position", "raise", "red", "set",
        "speed", "temperature", "to", "value", "warm", "warmer", "white", "wind",
        "yellow",
        *{word for color in COLOR_RGB for word in normalize_text(color).split()},
    }
    operation_cues = {
        "brightness", "celsius", "color", "degree", "degrees", "fahrenheit",
        "halfway", "kelvin", "level", "mode", "openness", "percent", "position",
        "speed", "temperature", "value", "wind",
        *{word for color in COLOR_RGB for word in normalize_text(color).split()},
    }

    def selector_is_target_anchored(start: int) -> bool:
        if state_command is None or start < state_command.end():
            return False
        actions = tuple(re.finditer(STATE_CHANGE_ACTION_RE, normalized[:start]))
        bridge_start = actions[-1].end() if actions else state_command.end()
        bridge = normalized[bridge_start:start]
        for modifier in sorted(target_modifiers, key=len, reverse=True):
            bridge = re.sub(
                rf"(?<![a-z0-9]){re.escape(modifier)}(?![a-z0-9])",
                " ",
                bridge,
            )
        words = re.findall(r"[a-z0-9]+", bridge)
        if all(word in target_fillers for word in words):
            return True
        if any(word in target_nouns for word in words):
            return False
        has_operation_cue = any(
            word in operation_cues or word.isdigit()
            for word in words
        )
        return has_operation_cue and all(
            word in target_fillers
            or word in operation_bridge_words
            or word.isdigit()
            for word in words
        )

    labels: dict[tuple[str, str], set[str]] = {}
    for alias, domain in GENERIC_DEVICE_ALIASES.items():
        labels.setdefault(("domain", normalize_text(alias)), set()).add(domain)
    for entity in registry.entities:
        labels.setdefault(("entity", normalize_text(entity.entity_id)), set()).add(
            entity.domain
        )
        for alias in entity.aliases:
            labels.setdefault(("alias", normalize_text(alias)), set()).add(entity.domain)
        if normalize_text(entity.device) not in GENERIC_DEVICE_ALIASES:
            labels.setdefault(("device", normalize_text(entity.device)), set()).add(
                entity.domain
            )

    domains: set[str] = set()
    for (category, label), owner_domains in labels.items():
        if not label or len(owner_domains) != 1:
            continue
        for start, end, _value in _distinct_named_matches(normalized, (label,)):
            if _selector_span_is_negative(normalized, start, end, selector_spans):
                continue
            if category == "domain" and any(
                spatial_start <= start and end <= spatial_end
                for spatial_start, spatial_end in spatial_spans
            ):
                continue
            if not selector_is_target_anchored(start):
                continue
            if _label_is_grammar_collision(label) and not _selector_match_is_anchored(
                normalized,
                start,
                end,
                label,
                category,
                registry,
            ):
                continue
            domains.update(owner_domains)
            break
    return frozenset(domains)


def _selector_phrase_candidate_ids(
    text: str,
    start: int,
    end: int,
    registry: EntityRegistry,
    selector_spans: Sequence[tuple[int, int]],
) -> frozenset[str]:
    """Resolve the bounded noun phrase containing one shared selector label."""

    boundary_pattern = (
        r"[!?;,:—]|\.(?![a-z0-9])|"
        r"\b(?:and\s+then|then|but|rather|and|or)\b"
    )
    boundaries = tuple(
        match
        for match in re.finditer(boundary_pattern, text)
        if not any(
            selector_start < match.start() < selector_end
            or selector_start < match.end() < selector_end
            for selector_start, selector_end in selector_spans
        )
    )
    phrase_start = max(
        (match.end() for match in boundaries if match.end() <= start),
        default=0,
    )
    phrase_end = min(
        (match.start() for match in boundaries if match.start() >= end),
        default=len(text),
    )
    phrase = text[phrase_start:phrase_end]
    relative_start = start - phrase_start
    predicate = (
        rf"\b(?:{SELECTOR_ACTION_RE}|{EXECUTION_CONTROL_ACTION_RE}|want|need|have|"
        r"avoid|without|except|besides|not|no|using|selecting|choosing|touching)\b"
    )
    predicates = tuple(re.finditer(predicate, phrase[:relative_start]))
    if predicates:
        phrase = phrase[predicates[-1].end():]
    selector = _explicit_selector_match(
        registry,
        phrase,
        allow_bare_meaningful=True,
    )
    if not selector.present or selector.conflicting_slots or not selector.candidates:
        return frozenset()
    return frozenset(entity.entity_id for entity in selector.candidates)


def _negated_entity_ids(registry: EntityRegistry, utterance: str) -> tuple[str, ...]:
    normalized = normalize_text(utterance)
    selector_spans = _registry_selector_spans(normalized, registry)
    # A later state command reverses a domain-wide exclusion only when that
    # command itself positively names the domain or a domain-specific stable
    # selector.  A bare action, ``a device``, room, or floor cannot silently
    # make an excluded domain eligible for clarification again.
    authorization_clause = _final_authorization_clause(normalized, registry)
    excluded_domains = set(_excluded_generic_domains(normalized, registry))
    if _has_universal_generic_exclusion(normalized, registry):
        excluded_domains.update(SUPPORTED_DOMAINS)
    if (
        authorization_clause != normalized
        and re.match(rf"{STATE_COMMAND_RE}\b", authorization_clause)
    ):
        excluded_domains.difference_update(
            _positive_target_domains(authorization_clause, registry)
        )

    label_owners: dict[str, set[str]] = {}
    for entity in registry.entities:
        labels = (entity.entity_id, entity.room, entity.floor, entity.device, *entity.aliases)
        for label in labels:
            normalized_label = normalize_text(label)
            if normalized_label:
                label_owners.setdefault(normalized_label, set()).add(entity.entity_id)

    def label_excludes(entity_id: str, label: str) -> bool:
        normalized_label = normalize_text(label)
        if not _label_is_excluded(
            normalized,
            normalized_label,
            selector_spans=selector_spans,
        ):
            return False
        if len(label_owners.get(normalized_label, ())) <= 1:
            return True
        scoped_matches = tuple(
            (start, end)
            for start, end, _value in _distinct_named_matches(
                normalized,
                (normalized_label,),
            )
            if _selector_span_is_negative(
                normalized,
                start,
                end,
                selector_spans,
            )
        )
        if not scoped_matches:
            # Preserve the previous fail-closed behavior for exclusion forms
            # recognized by the fallback grammar (for example ``leave X
            # unchanged``) but not by the general negative-scope parser.
            return True
        for start, end in scoped_matches:
            phrase_ids = _selector_phrase_candidate_ids(
                normalized,
                start,
                end,
                registry,
                selector_spans,
            )
            if not phrase_ids or entity_id in phrase_ids:
                return True
        return False

    excluded: list[str] = []
    for entity in registry.entities:
        labels = (
            entity.entity_id,
            entity.room,
            entity.floor,
            entity.device,
            *entity.aliases,
        )
        if entity.domain in excluded_domains or any(
            label_excludes(entity.entity_id, label)
            for label in labels
            if normalize_text(label) not in GENERIC_DEVICE_ALIASES
        ):
            excluded.append(entity.entity_id)
    return tuple(sorted(set(excluded)))


def _operational_text(text: str, entities: Sequence[EntitySpec]) -> str:
    """Remove registry selector spans before interpreting values or attributes."""

    result = normalize_text(text)
    labels: dict[str, bool] = {}
    for entity in entities:
        for value in (entity.room, entity.floor):
            normalized_value = normalize_text(value)
            if normalized_value:
                labels.setdefault(normalized_value, True)
        values = [*entity.aliases]
        if normalize_text(entity.device) not in GENERIC_DEVICE_ALIASES:
            values.append(entity.device)
        for value in values:
            normalized_value = normalize_text(value)
            if normalized_value:
                labels[normalized_value] = False
    for label in sorted(labels, key=len, reverse=True):
        spatial_prefix = (
            r"(?:(?<!turn\s)(?<!switch\s)\b(?:in|on|at|from)\s+)?"
            if labels[label]
            else ""
        )
        result = re.sub(
            rf"(?<![a-z0-9]){spatial_prefix}{re.escape(label)}(?![a-z0-9])",
            " ",
            result,
        )
    return " ".join(result.split())


def _authorized_operational_text(
    text: str,
    registry: EntityRegistry,
    entities: Sequence[EntitySpec],
) -> str:
    """Return only operation evidence that remains authorized after a restart.

    A pure selector correction (``do not use Living, I mean Study``) keeps the
    operation stated before that correction.  A new state-command clause after
    a withdrawal replaces the earlier operation instead: any action, attribute,
    value, or unit must then be present in that clause or in the clarification
    answer.  This prevents an incomplete restart such as ``adjust Study`` from
    borrowing a withdrawn ``turn off`` action from an earlier clause.
    """

    normalized = normalize_text(text)
    authorization_clause = _final_authorization_clause(normalized, registry)
    if (
        authorization_clause != normalized
        and re.match(rf"{STATE_COMMAND_RE}\b", authorization_clause)
    ):
        return _operational_text(authorization_clause, entities)
    return _operational_text(normalized, entities)


def _clarification_operational_text(answer: str, chosen: EntitySpec) -> str:
    """Remove candidate-selection evidence before interpreting operation slots."""

    result = normalize_text(answer)
    generic_device_labels = tuple(
        alias for alias, domain in GENERIC_DEVICE_ALIASES.items()
        if domain == chosen.domain
    )
    labels: dict[str, bool] = {}
    for value in (chosen.room, chosen.floor):
        normalized_value = normalize_text(value)
        if normalized_value:
            labels.setdefault(normalized_value, True)
    for value in (
        chosen.entity_id,
        chosen.device,
        *chosen.aliases,
        *generic_device_labels,
    ):
        normalized_value = normalize_text(value)
        if normalized_value:
            labels[normalized_value] = False
    for label in sorted(labels, key=len, reverse=True):
        spatial_prefix = (
            r"(?:(?<!turn\s)(?<!switch\s)\b(?:in|on|at|from)\s+)?"
            if labels[label]
            else ""
        )
        result = re.sub(
            rf"(?<![a-z0-9]){spatial_prefix}{re.escape(label)}(?![a-z0-9])",
            " ",
            result,
        )
    return " ".join(result.split())


def _answer_operational_requirements(answer: str, chosen: EntitySpec) -> frozenset[str]:
    operation_only = _clarification_operational_text(answer, chosen)
    requirements = set(_explicit_operational_requirements(operation_only))
    if chosen.domain == "light" and any(_phrase_in(operation_only, color) for color in COLOR_RGB):
        requirements.update(("attribute", "value"))
    if chosen.domain == "climate" and any(
        _phrase_in(operation_only, mode) for mode in ("cool", "heat", "dry", "fan only", "auto")
    ):
        requirements.update(("attribute", "value"))
    return frozenset(requirements)


def _clarification_has_positive_authorization(
    answer: str,
    chosen: EntitySpec,
    candidates: Sequence[EntitySpec] = (),
) -> bool:
    """Recognize a bounded fail-closed clarification-answer grammar.

    Candidate metadata and a small positive operation vocabulary are allowed;
    any unparsed residual word rejects the answer.  Merely finding an action
    token somewhere in arbitrary prose is never sufficient authorization.
    """

    answer_normalized = normalize_text(answer)
    if _has_negative_action_authorization(answer_normalized):
        return False
    if answer_normalized.isdigit():
        return True
    operation_only = _clarification_operational_text(answer, chosen)
    ordered_words = re.findall(r"[a-z0-9]+", operation_only)
    words = set(ordered_words)
    selector_fillers = {
        "the", "one", "option", "number", "device", "please", "room", "floor",
        "in", "at", "on", "located", "by", "other", "i", "mean", "and", "for",
        "to", "use", "not", "leave", "unchanged",
    }
    if not words or words.issubset(selector_fillers):
        return True
    selector_words: set[str] = set()
    for entity in (*candidates, chosen):
        for label in (
            entity.entity_id, entity.room, entity.floor, entity.device, *entity.aliases,
        ):
            selector_words.update(re.findall(r"[a-z0-9]+", normalize_text(label)))
    for alias in GENERIC_DEVICE_ALIASES:
        selector_words.update(re.findall(r"[a-z0-9]+", normalize_text(alias)))
    operation_words = {
        "yes", "confirm", "confirmed", "proceed", "go", "ahead", "do", "execute",
        "it", "that", "this", "turn", "switch", "open", "close", "shut", "start",
        "set", "change", "make", "move", "adjust", "raise", "lower", "increase",
        "decrease", "brighter", "dimmer", "warmer", "cooler", "temperature",
        "brightness", "position", "openness", "color", "mode", "fan", "wind",
        "speed", "celsius", "kelvin", "percent", "degree", "degrees", "halfway",
        "instead", "its", "right", "now", "off", "low", "medium", "high", "level",
        "cool", "heat", "dry", "auto", "only", "from", "into", "target", "new",
        *{word for color in COLOR_RGB for word in normalize_text(color).split()},
    }
    allowed = selector_fillers | selector_words | operation_words
    if any(word not in allowed and not word.isdigit() for word in ordered_words):
        return False
    affirmative = bool(re.fullmatch(
        r"(?:please\s+)?(?:yes(?:\s+please)?|confirm(?:ed)?|proceed|go\s+ahead|"
        r"do\s+it(?:\s+(?:right\s+)?now)?|execute\s+it)[.!]?",
        operation_only.strip(),
    ))
    identifies_candidate = any(
        _phrase_in(answer_normalized, label)
        for label in (chosen.entity_id, chosen.room, chosen.floor, chosen.device, *chosen.aliases)
    )
    requirements = _answer_operational_requirements(answer, chosen)
    directions = _directional_actions(operation_only)
    return affirmative or bool(requirements) or bool(directions) or identifies_candidate


def _mentioned_entities(
    registry: EntityRegistry,
    utterance: str,
    source_instructions: Sequence[DomuxInstruction],
) -> tuple[EntitySpec, ...]:
    normalized = normalize_text(utterance)
    hinted_domains = {
        domain for instruction in source_instructions
        if (domain := EntityRegistry._domain_hint(instruction)) is not None
    }
    mentioned: list[EntitySpec] = []
    for entity in registry.entities:
        if hinted_domains and entity.domain not in hinted_domains:
            continue
        discriminators = (entity.room, *entity.aliases)
        if any(_phrase_in(normalized, value) for value in discriminators):
            mentioned.append(entity)
    return tuple(mentioned)


@dataclass(frozen=True)
class _ExplicitSelectorMatch:
    present: bool
    discriminating: bool
    candidates: tuple[EntitySpec, ...]
    slots: tuple[str, ...]
    conflicting_slots: tuple[str, ...]


def _explicit_selector_match(
    registry: EntityRegistry,
    utterance: str,
    *,
    allow_bare_meaningful: bool = False,
) -> _ExplicitSelectorMatch:
    """Bind user-authored inventory selectors independently of model output.

    Values are ORed within a slot (``Study or Living Room``) and ANDed across
    slots (``Kitchen light``).  A present selector with zero matches remains an
    explicit contradiction; callers must never fall back to model/global
    candidates in that case.
    """

    normalized = normalize_text(utterance)
    labels: dict[str, tuple[str, ...]] = {
        "domain": tuple(GENERIC_DEVICE_ALIASES),
        "room": tuple(entity.room for entity in registry.entities),
        "floor": tuple(entity.floor for entity in registry.entities),
        "device": tuple(
            entity.device
            for entity in registry.entities
            if normalize_text(entity.device) not in GENERIC_DEVICE_ALIASES
        ),
        "alias": tuple(alias for entity in registry.entities for alias in entity.aliases),
        "entity": tuple(entity.entity_id for entity in registry.entities),
    }
    selector_spans = _registry_selector_spans(normalized, registry)
    matches = {
        category: tuple(
            match
            for match in _positive_named_matches(
                normalized,
                values,
                selector_spans=selector_spans,
            )
            if _selector_match_is_anchored(
                normalized,
                match[0],
                match[1],
                match[2],
                category,
                registry,
            )
            or (allow_bare_meaningful and not _label_is_grammar_collision(match[2]))
        )
        for category, values in labels.items()
    }
    # A generic device word can be part of a registered spatial label (for
    # example ``AC Room`` or ``Light Room``).  Once the complete room/floor
    # label is anchored, that contained token is spatial metadata, not a
    # second device-domain selector.  A separate target token outside the
    # spatial span remains available and is still intersected below.
    spatial_spans = tuple(
        (start, end)
        for category in ("room", "floor")
        for start, end, _value in matches[category]
    )
    matches["domain"] = tuple(
        match
        for match in matches["domain"]
        if not any(
            spatial_start <= match[0] and match[1] <= spatial_end
            for spatial_start, spatial_end in spatial_spans
        )
    )
    longer_specific_spans = tuple(
        (start, end)
        for category in ("device", "alias", "entity")
        for start, end, _value in matches[category]
    )
    matches["domain"] = tuple(
        match
        for match in matches["domain"]
        if not any(
            start <= match[0] and match[1] <= end and (start, end) != match[:2]
            for start, end in longer_specific_spans
        )
    )
    slot_for = {
        "domain": "device",
        "device": "device",
        "room": "room",
        "floor": "floor",
        "alias": "entity",
        "entity": "entity",
    }
    slots = list(dict.fromkeys(
        slot_for[category]
        for category, category_matches in matches.items()
        if category_matches
    ))
    present = bool(slots)
    if not present:
        return _ExplicitSelectorMatch(False, False, (), (), ())
    discriminating = any(matches[category] for category in matches if category != "domain")

    values = {
        category: {value for _start, _end, value in category_matches}
        for category, category_matches in matches.items()
    }

    conflicting_slots: list[str] = []
    mentioned_domains = {
        GENERIC_DEVICE_ALIASES[value] for value in values["domain"]
    }
    if len(mentioned_domains) > 1 or len(values["device"]) > 1:
        conflicting_slots.append("device")
    if len(values["room"]) > 1:
        conflicting_slots.append("room")
    if len(values["floor"]) > 1:
        conflicting_slots.append("floor")
    if len(values["alias"]) > 1 or len(values["entity"]) > 1:
        conflicting_slots.append("entity")

    def matching_ids(category: str, value: str) -> set[str]:
        if category == "domain":
            domain = GENERIC_DEVICE_ALIASES[value]
            return {entity.entity_id for entity in registry.entities if entity.domain == domain}
        if category == "room":
            return {
                entity.entity_id for entity in registry.entities
                if normalize_text(entity.room) == value
            }
        if category == "floor":
            return {
                entity.entity_id for entity in registry.entities
                if normalize_text(entity.floor) == value
            }
        if category == "device":
            return {
                entity.entity_id for entity in registry.entities
                if normalize_text(entity.device) == value
            }
        if category == "alias":
            return {
                entity.entity_id for entity in registry.entities
                if any(normalize_text(alias) == value for alias in entity.aliases)
            }
        return {
            entity.entity_id for entity in registry.entities
            if normalize_text(entity.entity_id) == value
        }

    # Overlapping text spans can have several inventory meanings (for example
    # a room ``Study`` and a device ``Study Light``).  A connected overlap
    # component is ORed; treating its interpretations as independent AND
    # constraints could silently pick one target from an ambiguous phrase.
    entries: list[tuple[int, int, str, str]] = []
    for category, category_matches in matches.items():
        for start, end, value in category_matches:
            entries.append((start, end, category, value))
    components: list[list[tuple[int, int, str, str]]] = []
    remaining = set(entries)
    while remaining:
        component = [remaining.pop()]
        changed = True
        while changed:
            changed = False
            component_start = min(item[0] for item in component)
            component_end = max(item[1] for item in component)
            overlapping = {
                item for item in remaining
                if item[0] < component_end and component_start < item[1]
            }
            if overlapping:
                component.extend(overlapping)
                remaining.difference_update(overlapping)
                changed = True
        components.append(component)
    ambiguous_entries = {
        entry
        for component in components
        if len({entry[2] for entry in component}) > 1
        for entry in component
    }
    constraints: list[set[str]] = []
    for component in components:
        if not any(entry in ambiguous_entries for entry in component):
            continue
        ids: set[str] = set()
        for _start, _end, category, value in component:
            ids.update(matching_ids(category, value))
        constraints.append(ids)
    for category, category_matches in matches.items():
        unambiguous = {
            value
            for start, end, value in category_matches
            if (start, end, category, value) not in ambiguous_entries
        }
        if unambiguous:
            ids: set[str] = set()
            for value in unambiguous:
                ids.update(matching_ids(category, value))
            constraints.append(ids)

    candidate_ids = {entity.entity_id for entity in registry.entities}
    for constraint in constraints:
        candidate_ids.intersection_update(constraint)
    candidates = tuple(
        entity for entity in registry.entities if entity.entity_id in candidate_ids
    )
    return _ExplicitSelectorMatch(
        True,
        discriminating,
        candidates,
        tuple(slots),
        tuple(conflicting_slots),
    )


def _explicit_mentioned_entities(
    registry: EntityRegistry,
    utterance: str,
) -> tuple[EntitySpec, ...]:
    """Compatibility wrapper for callers that need only explicit candidates."""

    return _explicit_selector_match(registry, utterance).candidates


def _request_candidates(
    utterance: str,
    source_instructions: Sequence[DomuxInstruction],
    registry: EntityRegistry,
    context: SessionContext,
) -> tuple[tuple[DomuxInstruction, ...], tuple[EntitySpec, ...]]:
    selectors = tuple(
        _source_selector(utterance, instruction, registry)
        for instruction in source_instructions
    )

    explicit = _explicit_selector_match(registry, utterance)
    deictic = _has_deictic_reference(utterance)
    if _has_other_reference(utterance) and not context.recent_entity_ids:
        return selectors, ()
    if deictic and context.recent_entity_ids:
        contextual = tuple(
            registry._by_id[entity_id]
            for entity_id in dict.fromkeys(context.recent_entity_ids)
            if entity_id in registry._by_id
        )
        if explicit.present:
            explicitly_mentioned = {entity.entity_id for entity in explicit.candidates}
            contextual = tuple(
                entity for entity in contextual if entity.entity_id in explicitly_mentioned
            )
        if _has_other_reference(utterance) and len(contextual) < 2:
            return selectors, ()
        return selectors, tuple(sorted(contextual, key=registry._sort_key))

    if explicit.present:
        return selectors, explicit.candidates

    by_id: dict[str, EntitySpec] = {}
    for selector in selectors:
        for entity in registry.candidates(selector, context):
            by_id[entity.entity_id] = entity
    if _has_uncertainty_or_conflict(utterance):
        for entity in _mentioned_entities(registry, utterance, source_instructions):
            by_id[entity.entity_id] = entity
    return selectors, tuple(sorted(by_id.values(), key=registry._sort_key))


def _has_other_reference(text: str) -> bool:
    return bool(re.search(OTHER_REFERENCE_RE, normalize_text(text)))


def _has_deictic_reference(text: str) -> bool:
    normalized = normalize_text(text)
    return _has_other_reference(normalized) or any(
        _phrase_in(normalized, phrase)
        for phrase in ("it", "that", "that one", "this", "this one")
    )


@dataclass(frozen=True)
class GroundedRequest:
    utterance: str
    raw_output: str
    source_instructions: tuple[DomuxInstruction, ...]
    selector_instructions: tuple[DomuxInstruction, ...]
    context_entity_ids: tuple[str, ...]
    negated_entity_ids: tuple[str, ...]
    excluded_operation_value_tokens: tuple[str, ...]
    candidates: tuple[EntitySpec, ...]
    clarification: Clarification
    request_digest: str


@dataclass(frozen=True)
class ResolvedRequest:
    grounded: GroundedRequest
    chosen: EntitySpec
    confirmed_instruction: DomuxInstruction
    clarification_digest: str


def ground_domux_request(
    utterance: str,
    raw_output: str,
    registry: EntityRegistry,
    context: SessionContext | None = None,
) -> GroundedRequest:
    if not isinstance(utterance, str) or not utterance.strip():
        raise GroundingError("user utterance is empty")
    if len(utterance) > MAX_UTTERANCE_CHARS:
        raise GroundingError("user utterance exceeds the supported length")
    context = context or SessionContext()
    source = parse_domux_output(raw_output)
    selectors, candidates = _request_candidates(utterance, source, registry, context)
    authorization_utterance = _final_authorization_clause(utterance, registry)
    operational_utterance = _authorized_operational_text(
        utterance,
        registry,
        candidates or registry.entities,
    )
    negated_entity_ids = _negated_entity_ids(registry, utterance)
    excluded_value_tokens = _excluded_operation_value_tokens(utterance, source)
    reasons: list[str] = []
    unresolved: list[str] = []
    explicit_selector = _explicit_selector_match(registry, utterance)
    valid_context_ids = tuple(
        entity_id
        for entity_id in dict.fromkeys(context.recent_entity_ids)
        if entity_id in registry._by_id
    )
    other_reference = _has_other_reference(utterance)
    if not candidates:
        reasons.append("no_registry_match")
    elif len(candidates) > 1:
        reasons.append("multiple_registry_matches")
    if len(source) != 1:
        reasons.append("multiple_model_instructions")
    if _has_uncertainty_or_conflict(utterance):
        reasons.append("uncertainty_or_conflict")
    if explicit_selector.conflicting_slots:
        reasons.append("multiple_explicit_selectors")
        unresolved.extend(explicit_selector.conflicting_slots)
    if _has_negative_or_cancelled_intent(authorization_utterance, registry):
        reasons.append("negative_or_cancelled_intent")
    if not _has_supported_request_grammar(utterance, registry, source):
        reasons.append("unsupported_request_grammar")
        unresolved.append("authorization")
    if (
        _has_deictic_reference(utterance)
        and not context.recent_entity_ids
        and not explicit_selector.discriminating
    ):
        reasons.append("unsupported_request_grammar")
        unresolved.append("authorization")
    if other_reference:
        reasons.append("other_reference_requires_selection")
        unresolved.append("context")
        if len(valid_context_ids) < 2:
            reasons.append("unsupported_request_grammar")
            unresolved.append("authorization")
    if _has_deictic_reference(utterance) and any(
        entity_id not in registry._by_id
        for entity_id in context.recent_entity_ids
    ):
        reasons.append("stale_context_reference")
        unresolved.append("context")
    if (
        _has_unresolved_generic_exclusion(authorization_utterance, registry)
        and not _excluded_generic_domains(authorization_utterance, registry)
        and not (other_reference and len(valid_context_ids) >= 2)
    ):
        reasons.append("unsupported_request_grammar")
        unresolved.append("authorization")
    informational_request = _has_informational_request(utterance)
    if informational_request:
        reasons.append("informational_request")
        unresolved.append("authorization")
    elif _has_unsupported_condition_or_time(utterance):
        reasons.append("unsupported_condition_or_time")
        unresolved.append("condition_or_time")
    if negated_entity_ids:
        reasons.append("negated_selector")
    if excluded_value_tokens:
        reasons.append("excluded_operation_value")
        unresolved.append("value")
    explicit_requirements = _explicit_operational_requirements(operational_utterance)
    unresolved.extend(_partial_registry_selector_slots(utterance, registry))
    unresolved.extend(_unanchored_registry_selector_slots(utterance, registry))
    unresolved.extend(_unresolved_selector_phrase_slots(utterance))
    if _positional_unknown_selector_words(utterance, registry):
        unresolved.append("device")
    if _positional_unknown_attribute_words(utterance, registry):
        unresolved.append("attribute")
        reasons.append("unknown_operation_modifier")
    for instruction, selector in zip(source, selectors):
        for slot in ("device", "attribute", "room", "floor"):
            if getattr(instruction, slot) != getattr(selector, slot):
                unresolved.append(slot)
        unresolved.extend(_missing_required_slots(instruction))
        if not _action_supported(operational_utterance, instruction):
            unresolved.append("action")
        if not _attribute_supported(operational_utterance, instruction) and not (
            len(candidates) == 1
            and _attribute_supported_for_entity(operational_utterance, instruction, candidates[0])
        ):
            unresolved.append("attribute")
        if not _value_supported(operational_utterance, instruction.value):
            unresolved.append("value")
        if not _unit_supported(operational_utterance, instruction):
            unresolved.append("unit")
        unresolved.extend(sorted(_operational_conflicts(operational_utterance, instruction)))
        if normalize_text(instruction.action) == "turnon" and any(
            candidate.domain == "climate" for candidate in candidates
        ):
            reasons.append("climate_mode_confirmation_required")
            unresolved.extend(("attribute", "value"))
        for slot in sorted(explicit_requirements):
            if getattr(instruction, slot) == "*":
                unresolved.append(slot)
    if unresolved:
        reasons.append("ungrounded_or_missing_slots")
    reasons = list(dict.fromkeys(reasons))
    unresolved_slots = tuple(dict.fromkeys(unresolved))

    base = clarification_for(candidates)
    required = bool(reasons)
    if not required:
        clarification = base
    elif not candidates or any(reason in reasons for reason in (
        "negative_or_cancelled_intent", "informational_request", "unsupported_condition_or_time",
        "unsupported_request_grammar",
    )):
        clarification = Clarification(
            True,
            next((reason for reason in (
                "negative_or_cancelled_intent", "informational_request", "unsupported_condition_or_time",
                "unsupported_request_grammar",
            ) if reason in reasons), reasons[0]),
            tuple(candidates[:3]),
            None,
            tuple(reasons),
            unresolved_slots,
        )
    elif len(candidates) > 3:
        displayed = tuple(candidates[:3])
        clarification = Clarification(
            True,
            "too_many_candidates",
            displayed,
            "More than three devices match. Please provide a room or floor before choosing.",
            tuple(dict.fromkeys((*reasons, "too_many_candidates"))),
            unresolved_slots,
        )
    else:
        displayed = tuple(candidates[:3])
        options = "; ".join(
            _candidate_option(index, candidate)
            for index, candidate in enumerate(displayed, start=1)
        )
        detail = ", ".join(unresolved_slots) if unresolved_slots else "device or value"
        clarification = Clarification(
            True,
            reasons[0],
            displayed,
            f"Please confirm {detail}. Candidates: {options}.",
            tuple(reasons),
            unresolved_slots,
        )
    context_ids = tuple(dict.fromkeys(context.recent_entity_ids))
    payload = {
        "utterance_sha256": hashlib.sha256(utterance.encode("utf-8")).hexdigest(),
        "raw_output_sha256": hashlib.sha256(raw_output.encode("utf-8")).hexdigest(),
        "source": [instruction.to_pipe() for instruction in source],
        "selectors": [instruction.to_pipe() for instruction in selectors],
        "context_entity_ids": context_ids,
        "negated_entity_ids": negated_entity_ids,
        "excluded_operation_value_tokens": excluded_value_tokens,
        "candidate_ids": [candidate.entity_id for candidate in candidates],
        "reasons": clarification.reasons,
        "unresolved_slots": clarification.unresolved_slots,
    }
    return GroundedRequest(
        utterance=utterance,
        raw_output=raw_output,
        source_instructions=source,
        selector_instructions=selectors,
        context_entity_ids=context_ids,
        negated_entity_ids=negated_entity_ids,
        excluded_operation_value_tokens=excluded_value_tokens,
        candidates=candidates,
        clarification=clarification,
        request_digest=digest_json(payload),
    )


def resolve_clarification(
    answer: str,
    candidates: Sequence[EntitySpec],
    *,
    registry: EntityRegistry | None = None,
) -> EntitySpec:
    if not isinstance(answer, str) or not answer.strip():
        raise GroundingError("clarification answer is empty")
    if not candidates:
        raise GroundingError("clarification has no candidates")
    answer_norm = normalize_text(answer)
    answer_registry = EntityRegistry(candidates)
    scope_registry = registry or answer_registry
    answer_selector_spans = _registry_selector_spans(
        answer_norm,
        scope_registry,
    )
    answer_selector = _explicit_selector_match(
        answer_registry,
        answer,
        allow_bare_meaningful=True,
    )
    if answer_selector.present and (
        answer_selector.conflicting_slots or not answer_selector.candidates
    ):
        raise GroundingError("clarification answer has inconsistent target selectors")
    if _has_unresolved_generic_exclusion(answer_norm, scope_registry):
        selected = (
            answer_selector.candidates[0]
            if len(answer_selector.candidates) == 1
            else None
        )
        excluded_domains = _excluded_generic_domains(answer, scope_registry)
        if not (
            answer_selector.discriminating
            and selected is not None
            and not answer_selector.conflicting_slots
            and excluded_domains
            and selected.domain not in excluded_domains
        ):
            raise GroundingError("clarification answer uses an unresolved generic exclusion")
    other = re.search(OTHER_REFERENCE_RE, answer_norm)
    if other is not None:
        suffix = answer_norm[other.end():]
        explicit_after = any(
            _phrase_in(suffix, label)
            for entity in candidates
            for label in (entity.entity_id, entity.room, entity.floor, *entity.aliases)
        )
        if not explicit_after:
            raise GroundingError("clarification answer does not identify which other candidate")
    if answer_norm.isdigit():
        index = int(answer_norm) - 1
        if 0 <= index < len(candidates):
            return candidates[index]
        raise GroundingError("clarification index is outside the displayed candidates")
    exact_id = [entity for entity in candidates if normalize_text(entity.entity_id) == answer_norm]
    if len(exact_id) == 1:
        return exact_id[0]
    if len(candidates) == 1:
        candidate = candidates[0]
        exclusion_labels = [candidate.room, candidate.floor, *candidate.aliases]
        if normalize_text(candidate.device) not in GENERIC_DEVICE_ALIASES:
            exclusion_labels.append(candidate.device)
        for label in exclusion_labels:
            if _label_is_excluded(
                answer_norm,
                label,
                selector_spans=answer_selector_spans,
            ):
                raise GroundingError("clarification answer excludes the only candidate")
        if _answer_is_noncommittal(answer_norm):
            raise GroundingError("clarification answer is noncommittal")
        identifies_candidate = any(
            _selector_label_evidence(answer, label, category, scope_registry)
            for category, label in (
                ("entity", candidate.entity_id),
                ("room", candidate.room),
                ("floor", candidate.floor),
                *(("alias", alias) for alias in candidate.aliases),
            )
        )
        supplies_operation = bool(_explicit_operational_requirements(answer)) or bool(
            _directional_actions(answer_norm)
        ) or bool(_numbers_in(answer_norm)) or any(
            _phrase_in(answer_norm, value)
            for value in (*COLOR_RGB, "cool", "heat", "dry", "fan only", "auto", "low", "medium", "high")
        )
        explicitly_affirms = bool(re.match(
            r"^(?:yes\b|confirm\b|confirmed\b|proceed\b|do\s+it\b|that\s+one\b)",
            answer_norm,
        ))
        if not (identifies_candidate or supplies_operation or explicitly_affirms):
            raise GroundingError("clarification answer provides no confirmation evidence")
        return candidate
    feature_counts: dict[tuple[str, str], int] = {}
    for entity in candidates:
        for kind, value in (("room", entity.room), ("floor", entity.floor), ("device", entity.device)):
            key = (kind, normalize_text(value))
            feature_counts[key] = feature_counts.get(key, 0) + 1
    scored: list[tuple[int, EntitySpec]] = []
    selector_candidate_ids = {
        entity.entity_id for entity in answer_selector.candidates
    } if answer_selector.present else {entity.entity_id for entity in candidates}
    for entity in candidates:
        if entity.entity_id not in selector_candidate_ids:
            continue
        negative = False
        exclusion_labels = [entity.room, entity.floor, *entity.aliases]
        if normalize_text(entity.device) not in GENERIC_DEVICE_ALIASES:
            exclusion_labels.append(entity.device)
        for label in exclusion_labels:
            if _label_is_excluded(
                answer_norm,
                label,
                selector_spans=answer_selector_spans,
            ):
                negative = True
        if negative:
            continue
        score = 0
        combined = normalize_text(f"{entity.floor} {entity.room} {entity.device}")
        if _phrase_in(answer_norm, combined):
            score += 20
        for kind, value, weight in (
            ("room", entity.room, 8), ("floor", entity.floor, 5), ("device", entity.device, 4)
        ):
            key = (kind, normalize_text(value))
            selector_category = (
                "domain" if normalize_text(value) in GENERIC_DEVICE_ALIASES else kind
            )
            if feature_counts[key] < len(candidates) and _selector_label_evidence(
                answer, value, selector_category, scope_registry,
            ):
                score += weight
        score += 10 * sum(
            _selector_label_evidence(answer, alias, "alias", scope_registry)
            for alias in entity.aliases
        )
        if score:
            scored.append((score, entity))
    if not scored:
        raise GroundingError("clarification answer does not select a candidate")
    best = max(score for score, _ in scored)
    matches = [entity for score, entity in scored if score == best]
    if len(matches) != 1:
        raise GroundingError(f"clarification answer selects {len(matches)} candidates")
    return matches[0]


def _answer_repairs_target_slots(
    answer: str,
    chosen: EntitySpec,
    candidates: Sequence[EntitySpec],
    unresolved_slots: Sequence[str],
    registry: EntityRegistry,
) -> bool:
    answer_normalized = normalize_text(answer)
    exact_global = answer_normalized.isdigit() or _phrase_in(answer_normalized, chosen.entity_id)
    if exact_global:
        return True
    alias_evidence = any(
        _selector_label_evidence(answer, alias, "alias", registry)
        and sum(
            normalize_text(alias) in {normalize_text(item) for item in candidate.aliases}
            for candidate in candidates
        ) == 1
        for alias in chosen.aliases
    )
    if alias_evidence:
        return True

    answer_selector = _explicit_selector_match(registry, answer)
    strong_target_evidence = (
        answer_selector.present
        and tuple(entity.entity_id for entity in answer_selector.candidates) == (chosen.entity_id,)
    )

    room_evidence = _selector_label_evidence(answer, chosen.room, "room", registry)
    floor_evidence = _selector_label_evidence(answer, chosen.floor, "floor", registry)
    specific_device_evidence = (
        normalize_text(chosen.device) not in GENERIC_DEVICE_ALIASES
        and _selector_label_evidence(answer, chosen.device, "device", registry)
    )
    room_unique = room_evidence and sum(
        normalize_text(candidate.room) == normalize_text(chosen.room)
        for candidate in candidates
    ) == 1
    floor_unique = floor_evidence and sum(
        normalize_text(candidate.floor) == normalize_text(chosen.floor)
        for candidate in candidates
    ) == 1
    device_unique = specific_device_evidence and sum(
        normalize_text(candidate.device) == normalize_text(chosen.device)
        for candidate in candidates
    ) == 1
    for slot in set(unresolved_slots).intersection({
        "device", "room", "floor", "entity", "context",
    }):
        if slot == "room" and not room_evidence:
            return False
        if slot == "floor" and not (floor_evidence or strong_target_evidence):
            return False
        if slot == "device" and not (
            specific_device_evidence or strong_target_evidence
            or room_unique or (room_evidence and floor_evidence)
        ):
            return False
        if slot == "entity" and not (
            strong_target_evidence or room_unique or floor_unique or device_unique
        ):
            return False
        if slot == "context" and not (
            strong_target_evidence or room_unique or floor_unique or device_unique
        ):
            return False
    return True


def _initial_target_is_fully_bound(
    grounded: GroundedRequest,
    chosen: EntitySpec,
    registry: EntityRegistry,
) -> bool:
    """Return whether the user's original selector already fixes the target.

    A clarification may then repair only operation slots.  Model-proposed
    room/floor fields, context-only references, partial labels, and unknown or
    relational selectors never qualify as an already-bound target.
    """

    explicit = _explicit_selector_match(registry, grounded.utterance)
    return (
        explicit.discriminating
        and tuple(entity.entity_id for entity in explicit.candidates) == (chosen.entity_id,)
        and not explicit.conflicting_slots
        and not _partial_registry_selector_slots(grounded.utterance, registry)
        and not _unanchored_registry_selector_slots(grounded.utterance, registry)
        and not _unresolved_selector_phrase_slots(grounded.utterance)
        and not _positional_unknown_selector_words(grounded.utterance, registry)
        and not _has_other_reference(grounded.utterance)
        and "stale_context_reference" not in grounded.clarification.reasons
        and chosen.entity_id not in grounded.negated_entity_ids
    )


def _validate_confirmed_instruction(
    grounded: GroundedRequest,
    answer: str,
    confirmed: DomuxInstruction,
    chosen: EntitySpec,
    registry: EntityRegistry,
) -> None:
    answer_normalized = normalize_text(answer)
    answer_selector_spans = _registry_selector_spans(answer_normalized, registry)
    if _answer_cancels(answer_normalized) or _has_negative_or_cancelled_intent(answer) or any(
        _phrase_in(answer_normalized, phrase)
        for phrase in ("do not act", "do not execute", "don't act", "don't execute")
    ):
        raise GroundingError("clarification answer cancels the request")
    if _has_unsupported_condition_or_time(answer):
        raise GroundingError("clarification answer contains an unsupported condition or time")
    if _has_informational_request(answer):
        raise GroundingError("clarification answer is informational, not an authorization")
    if chosen.entity_id in grounded.negated_entity_ids:
        raise GroundingError("clarification selected an entity explicitly excluded by the user")
    chosen_exclusion_labels = [
        chosen.entity_id,
        chosen.room,
        chosen.floor,
        *chosen.aliases,
    ]
    if normalize_text(chosen.device) not in GENERIC_DEVICE_ALIASES:
        chosen_exclusion_labels.append(chosen.device)
    if any(
        _label_is_excluded(
            answer_normalized,
            label,
            selector_spans=answer_selector_spans,
        )
        for label in chosen_exclusion_labels
    ):
        raise GroundingError("clarification answer explicitly excludes the selected entity")
    confirmed_value_token = _operation_value_token(confirmed.value)
    if (
        confirmed_value_token in grounded.excluded_operation_value_tokens
        or _value_is_explicitly_excluded(grounded.utterance, confirmed.value)
        or _value_is_explicitly_excluded(answer, confirmed.value)
    ):
        raise GroundingError("confirmed value was explicitly excluded by the user")
    answer_is_candidate_index = answer_normalized.isdigit()
    operational_slots = ("action", "attribute", "value", "unit")
    if answer_is_candidate_index and any(
        slot in grounded.clarification.unresolved_slots for slot in operational_slots
    ):
        raise GroundingError(
            "a candidate index cannot supply a missing or conflicting operation slot"
        )
    # A displayed index is selector UI, not an operational number.  Keeping it
    # out of value parsing prevents option 2 from becoming 2 percent/degrees.
    operational_answer = (
        "" if answer_is_candidate_index
        else _clarification_operational_text(answer, chosen)
    )
    if _has_informational_request(operational_answer):
        raise GroundingError("clarification answer is informational, not an authorization")
    if _has_unsupported_condition_or_time(operational_answer):
        raise GroundingError("clarification answer contains an unsupported condition or time")
    if _has_negative_or_cancelled_intent(operational_answer):
        raise GroundingError("clarification answer cancels the request")
    if not _clarification_has_positive_authorization(answer, chosen, registry.entities):
        raise GroundingError("clarification answer has no positive authorization evidence")
    target_was_bound = _initial_target_is_fully_bound(grounded, chosen, registry)
    if not target_was_bound and not _answer_repairs_target_slots(
        answer, chosen, grounded.candidates,
        grounded.clarification.unresolved_slots, registry,
    ):
        raise GroundingError(
            "clarification answer must explicitly identify the repaired target"
        )
    answer_requirements = _answer_operational_requirements(answer, chosen)
    for slot in answer_requirements:
        if getattr(confirmed, slot) == "*":
            raise GroundingError(f"confirmed plan drops explicit {slot} from the clarification answer")
    answer_directions = _directional_actions(answer_normalized)
    confirmed_action = normalize_text(confirmed.action)
    if len(answer_directions) > 1:
        raise GroundingError("clarification answer contains opposing actions")
    if answer_directions and confirmed_action not in answer_directions:
        # "Open it to 35 percent" is a set-position answer, not a full-open
        # authorization.  Other cross-action patches fail closed.
        if not (confirmed_action == "set" and "value" in answer_requirements):
            raise GroundingError("confirmed action conflicts with the clarification answer")
    if answer_requirements and any(
        _phrase_in(answer_normalized, verb) for verb in ("set", "change", "make", "use")
    ) and confirmed_action not in {"set", "adjustup", "adjustdown"}:
        raise GroundingError("clarification answer introduces an unbound set operation")
    if not EntityRegistry._device_matches(chosen, confirmed.device):
        raise GroundingError("confirmed device does not match the selected entity")
    if normalize_text(confirmed.room) != normalize_text(chosen.room):
        raise GroundingError("confirmed room does not match the selected entity")
    if normalize_text(confirmed.floor) != normalize_text(chosen.floor):
        raise GroundingError("confirmed floor does not match the selected entity")
    confirmed_candidates = registry.candidates(confirmed)
    if tuple(entity.entity_id for entity in confirmed_candidates) != (chosen.entity_id,):
        raise GroundingError("confirmed instruction does not resolve uniquely to the selected entity")
    operational_utterance = _authorized_operational_text(
        grounded.utterance,
        registry,
        grounded.candidates or registry.entities,
    )
    answer_conflicts = _operational_conflicts(operational_answer, confirmed)
    if answer_conflicts:
        detail = ", ".join(sorted(answer_conflicts))
        raise GroundingError(f"clarification answer has unresolved operational conflicts: {detail}")
    evidence = f"{operational_utterance}\n{operational_answer}"
    original_conflicts = set(_operational_conflicts(operational_utterance, confirmed))
    if not (
        _action_supported(evidence, confirmed)
        or _action_supported(operational_answer, confirmed)
    ):
        raise GroundingError("confirmed action is not supported by the user text")
    if not _attribute_supported_for_entity(evidence, confirmed, chosen):
        raise GroundingError("confirmed attribute is not supported by the user text")
    if not _value_supported(evidence, confirmed.value) and not (
        "value" in original_conflicts and _value_supported(operational_answer, confirmed.value)
    ):
        raise GroundingError("confirmed value is not supported by the user text")
    if not _unit_supported(evidence, confirmed) and not (
        "unit" in original_conflicts and _unit_supported(operational_answer, confirmed)
    ):
        raise GroundingError("confirmed unit is not supported by the user text")
    answer_replaces_conflicted_action = (
        "action" in original_conflicts
        and _action_supported(operational_answer, confirmed)
        and normalize_text(confirmed.action) in {"turnon", "turnoff"}
    )
    for slot in _explicit_operational_requirements(operational_utterance):
        if getattr(confirmed, slot) == "*":
            if answer_replaces_conflicted_action and slot in {"attribute", "value", "unit"}:
                continue
            raise GroundingError(f"confirmed plan drops explicit {slot} from the user request")

    def answer_supports(slot: str) -> bool:
        if answer_is_candidate_index and slot in operational_slots:
            return False
        value = getattr(confirmed, slot)
        if value == "*":
            return True
        normalized_value = normalize_text(value)
        if re.search(
            rf"\b(?:not|instead\s+of)\b(?:\s+[a-z0-9]+){{0,3}}\s+{re.escape(normalized_value)}\b",
            answer_normalized,
        ):
            return False
        if slot == "action":
            if re.search(r"\bdo\s+not\s+(?:turn|switch|open|close|set|change|make)\b", answer_normalized):
                return False
            return _action_supported(operational_answer, confirmed)
        if slot == "attribute":
            return _attribute_supported_for_entity(operational_answer, confirmed, chosen)
        if slot == "value":
            return _value_supported(operational_answer, confirmed.value)
        return _unit_supported(operational_answer, confirmed)

    if (
        "unknown_operation_modifier" in grounded.clarification.reasons
        and not answer_supports("attribute")
    ):
        raise GroundingError("unknown operation modifier is not repaired by the answer")

    # A conflicted value/action must be selected in the answer itself.  It may
    # not be assembled from unrelated clauses in the original request.
    original_normalized = normalize_text(operational_utterance)
    numbers = set(_numbers_in(original_normalized))
    if _phrase_in(original_normalized, "halfway"):
        numbers.add(50.0)
    matched_named_values = {
        value for value in (*COLOR_RGB, "cool", "heat", "dry", "fan", "auto", "low", "medium", "high")
        if _phrase_in(original_normalized, value)
    }
    named_values = {
        value for value in matched_named_values
        if not any(
            value != longer and _phrase_in(normalize_text(longer), value)
            for longer in matched_named_values
        )
    }
    conflicted_slots: set[str] = set(original_conflicts)
    if len(numbers) > 1 or len(named_values) > 1:
        conflicted_slots.add("value")
    has_on = any(_phrase_in(original_normalized, term) for term in ("turn on", "switch on", "open"))
    has_off = any(_phrase_in(original_normalized, term) for term in ("turn off", "switch off", "close"))
    if has_on and has_off:
        conflicted_slots.add("action")
    for slot in conflicted_slots:
        if not answer_supports(slot):
            raise GroundingError(f"conflicting {slot} is not independently confirmed by the answer")

    missing_slots = {
        slot
        for source in grounded.source_instructions
        for slot in _missing_required_slots(source)
    }
    for slot in missing_slots:
        if slot in operational_slots and not answer_supports(slot):
            raise GroundingError(f"missing {slot} is not supplied by the answer")

    # The confirmed operational tuple must be one source tuple plus only
    # answer-supported patches.  This prevents cross-clause Frankenstein plans.
    context = SessionContext(grounded.context_entity_ids)
    patchable = False
    for source in grounded.source_instructions:
        changed = [
            slot for slot in operational_slots
            if normalize_text(getattr(source, slot)) != normalize_text(getattr(confirmed, slot))
        ]
        if not all(answer_supports(slot) for slot in changed):
            continue
        source_candidates = registry.candidates(
            _source_selector(grounded.utterance, source, registry),
            context,
        )
        source_targets_chosen = any(entity.entity_id == chosen.entity_id for entity in source_candidates)
        if source_targets_chosen or all(answer_supports(slot) for slot in conflicted_slots):
            patchable = True
            break
    if not patchable:
        raise GroundingError("confirmed plan is not a valid answer-supported patch of one source instruction")


def _answer_cancels(answer_normalized: str) -> bool:
    return _has_negative_action_authorization(answer_normalized) or bool(
        re.match(r"^(?:actually\s+)?no(?:\s+thanks?)?(?:\b|[,.!])", answer_normalized)
    ) or bool(
        re.search(r"\b(?:do\s+not|don't|dont)\s*[.!]?\s*$", answer_normalized)
    ) or bool(
        re.search(
            r"\b(?:cancel(?:\s+(?:it|that|this|the\s+request))?|never\s+mind|do\s+nothing|"
            r"stop(?:\s+(?:it|that|this))?|not\s+now|wait|hold(?:\s+on)?|"
            r"(?:do\s+not|don't)\s+(?:do|proceed|act|execute)\b(?:.{0,20}\byet\b)?|"
            r"(?:do\s+not|don't|rather\s+not)\s+(?:go\s+ahead|proceed|do|act|execute|"
            r"turn|switch|open|close|set|change|make|move|adjust|touch)\b|"
            r"(?:i\s+)?(?:do\s+not|don't)\s+want\b|"
            r"forget\s+(?:it|that|this)|(?:i\s+)?changed?\s+my\s+mind|"
            r"not\s+(?:anymore|any\s+longer)|skip\s+(?:it|that|this)|"
            r"refrain\s+from|"
            r"(?:leave|keep)\s+(?:(?:the\s+)?[a-z0-9_.-]+\s+|it\s+|that\s+|this\s+)?"
            r"(?:on|off|open|closed|unchanged|as\s+is))\b",
            answer_normalized,
        )
    )


def _answer_is_noncommittal(answer_normalized: str) -> bool:
    return bool(re.search(
        r"\b(?:do\s+not\s+know|don't\s+know|not\s+sure|still\s+not\s+sure|unsure|"
        r"whatever|maybe|perhaps|ask\s+me\s+later|later)\b",
        answer_normalized,
    ))


def resolve_clarification_submission(
    grounded: GroundedRequest,
    *,
    answer: str,
    confirmed_instruction: DomuxInstruction,
    registry: EntityRegistry,
) -> ResolvedRequest:
    if not grounded.clarification.required:
        raise GroundingError("request is already unique; use resolve_unique_request")
    if "negative_or_cancelled_intent" in grounded.clarification.reasons:
        raise GroundingError("cancelled or negated requests cannot be confirmed from this turn")
    if "informational_request" in grounded.clarification.reasons:
        raise GroundingError("informational questions cannot authorize execution from this turn")
    if "unsupported_condition_or_time" in grounded.clarification.reasons:
        raise GroundingError("conditional or timed requests require a new immediate command")
    if "unsupported_request_grammar" in grounded.clarification.reasons:
        raise GroundingError("unsupported request language requires a new immediate command")
    confirmed_value_token = _operation_value_token(confirmed_instruction.value)
    if confirmed_value_token in grounded.excluded_operation_value_tokens:
        raise GroundingError("confirmed value was explicitly excluded by the user")
    if not answer.strip():
        raise GroundingError("clarification answer is empty")
    if _answer_is_noncommittal(normalize_text(answer)):
        raise GroundingError("clarification answer is noncommittal")
    if len(grounded.candidates) > len(grounded.clarification.candidates):
        raise GroundingError("candidate set is not narrow enough to present safely")
    answer_selector = _explicit_selector_match(
        registry,
        answer,
        allow_bare_meaningful=True,
    )
    if answer_selector.candidates and all(
        entity.entity_id in grounded.negated_entity_ids
        for entity in answer_selector.candidates
    ):
        raise GroundingError("clarification selects an explicitly excluded entity")
    chosen = resolve_clarification(
        answer,
        grounded.candidates,
        registry=registry,
    )
    _validate_confirmed_instruction(grounded, answer, confirmed_instruction, chosen, registry)
    clarification_digest = digest_json({
        "request_digest": grounded.request_digest,
        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "chosen_entity_id": chosen.entity_id,
        "confirmed_instruction": confirmed_instruction.to_pipe(),
    })
    return ResolvedRequest(grounded, chosen, confirmed_instruction, clarification_digest)


def resolve_unique_request(grounded: GroundedRequest, registry: EntityRegistry) -> ResolvedRequest:
    if grounded.clarification.required:
        raise GroundingError("request still requires clarification")
    if len(grounded.source_instructions) != 1 or len(grounded.candidates) != 1:
        raise GroundingError("unique request invariant failed")
    chosen = grounded.candidates[0]
    confirmed = grounded.source_instructions[0]
    # A unique source may retain '*' room/floor; candidate uniqueness, not a
    # client-provided selector, binds the target in this path.
    if not EntityRegistry._device_matches(chosen, confirmed.device):
        raise GroundingError("unique request device does not match its candidate")
    if confirmed.room != "*" and normalize_text(confirmed.room) != normalize_text(chosen.room):
        raise GroundingError("unique request room does not match its candidate")
    if confirmed.floor != "*" and normalize_text(confirmed.floor) != normalize_text(chosen.floor):
        raise GroundingError("unique request floor does not match its candidate")
    clarification_digest = digest_json({
        "request_digest": grounded.request_digest,
        "answer": "unique_without_clarification",
        "chosen_entity_id": chosen.entity_id,
        "confirmed_instruction": confirmed.to_pipe(),
    })
    return ResolvedRequest(grounded, registry.get(chosen.entity_id), confirmed, clarification_digest)


@dataclass(frozen=True, init=False)
class CanonicalPlan:
    _source_slots_json: str
    entity_id: str
    domain: str
    service: str
    _service_data_json: str
    _expected_projection_json: str

    def __init__(
        self,
        *,
        source_slots: Mapping[str, str],
        entity_id: str,
        domain: str,
        service: str,
        service_data: Mapping[str, object],
        expected_projection: Mapping[str, object],
    ) -> None:
        object.__setattr__(self, "_source_slots_json", canonical_json(dict(source_slots)))
        object.__setattr__(self, "entity_id", entity_id)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "service", service)
        object.__setattr__(self, "_service_data_json", canonical_json(dict(service_data)))
        object.__setattr__(self, "_expected_projection_json", canonical_json(dict(expected_projection)))

    @property
    def source_slots(self) -> Mapping[str, str]:
        return json.loads(self._source_slots_json)

    @property
    def service_data(self) -> Mapping[str, object]:
        return json.loads(self._service_data_json)

    @property
    def expected_projection(self) -> Mapping[str, object]:
        return json.loads(self._expected_projection_json)

    def stable_dict(self) -> dict[str, object]:
        return {
            "source_slots": dict(self.source_slots),
            "entity_id": self.entity_id,
            "domain": self.domain,
            "service": self.service,
            "service_data": dict(self.service_data),
            "expected_projection": dict(self.expected_projection),
        }

    @property
    def digest(self) -> str:
        return digest_json(self.stable_dict())


def _numeric(value: str, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise GroundingError(f"expected numeric value, got {value!r}") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise GroundingError(f"numeric value {parsed} is outside [{minimum}, {maximum}]")
    return parsed


def _require_unit(instruction: DomuxInstruction, expected: str) -> None:
    if normalize_text(instruction.unit) != normalize_text(expected):
        raise GroundingError(
            f"{instruction.attribute} requires unit {expected!r}, got {instruction.unit!r}"
        )


def _require_placeholders(instruction: DomuxInstruction, *slots: str) -> None:
    invalid = [slot for slot in slots if getattr(instruction, slot) != "*"]
    if invalid:
        raise GroundingError(
            f"{instruction.action} requires '*' for unused slots: {', '.join(invalid)}"
        )


def _require_adjust_unit(instruction: DomuxInstruction, numeric_unit: str) -> None:
    expected = "*" if instruction.value == "*" else numeric_unit
    _require_unit(instruction, expected)


def _require_temperature_alignment(
    attributes: Mapping[str, object],
    temperature: float,
    minimum: float,
) -> None:
    if "target_temp_step" not in attributes:
        return
    step = float(attributes["target_temp_step"])
    if not math.isfinite(step) or step <= 0:
        raise GroundingError("climate target_temp_step must be a positive number")
    offset_steps = (temperature - minimum) / step
    if not math.isclose(offset_steps, round(offset_steps), rel_tol=0, abs_tol=1e-8):
        raise GroundingError(
            f"temperature {temperature} does not align with the advertised {step} degree step"
        )


def controlled_projection(raw_state: Mapping[str, object], domain: str) -> dict[str, object]:
    attributes = raw_state.get("attributes")
    attrs = attributes if isinstance(attributes, Mapping) else {}
    projected: dict[str, object] = {
        "entity_id": raw_state.get("entity_id"),
        "state": raw_state.get("state"),
    }
    keys = {
        "light": ("brightness", "color_temp_kelvin", "rgb_color"),
        "cover": ("current_position",),
        "climate": ("temperature", "fan_mode"),
    }[domain]
    for key in keys:
        if key in attrs:
            value = attrs[key]
            # Home Assistant's LightEntity state surface reports active color
            # attributes as null while off, even when an integration retains
            # the last value internally for the next turn-on.
            if domain == "light" and raw_state.get("state") == "off":
                value = None
            projected[key] = list(value) if isinstance(value, tuple) else value
    return projected


def planning_projection(raw_state: Mapping[str, object], domain: str) -> dict[str, object]:
    """Bind every state field that can affect ``build_plan``.

    The smaller controlled projection is used for outcome assertions.  This
    projection is deliberately separate and includes advertised capabilities,
    units, and ranges so a previously approved plan cannot outlive a capability
    change that leaves the visible value untouched.
    """

    projected = controlled_projection(raw_state, domain)
    attributes = raw_state.get("attributes")
    attrs = attributes if isinstance(attributes, Mapping) else {}
    keys = {
        "light": ("supported_color_modes", "min_color_temp_kelvin", "max_color_temp_kelvin"),
        "cover": ("supported_features",),
        "climate": (
            "hvac_modes", "fan_modes", "supported_features", "temperature_unit", "min_temp", "max_temp",
            "target_temp_step",
        ),
    }[domain]
    for key in keys:
        if key in attrs:
            value = attrs[key]
            projected[key] = list(value) if isinstance(value, tuple) else value
    return projected


def validate_state_shape(raw_state: Mapping[str, object], expected_entity_id: str | None = None) -> None:
    entity_id = raw_state.get("entity_id")
    if not isinstance(entity_id, str) or not ENTITY_ID_RE.fullmatch(entity_id):
        raise AdapterError("Home Assistant state has an invalid entity_id")
    if expected_entity_id is not None and entity_id != expected_entity_id:
        raise AdapterError("Home Assistant returned state for a different entity")
    if not isinstance(raw_state.get("state"), str):
        raise AdapterError("Home Assistant state value must be a string")
    if not isinstance(raw_state.get("attributes"), Mapping):
        raise AdapterError("Home Assistant state attributes must be an object")


def build_plan(
    instruction: DomuxInstruction,
    entity: EntitySpec,
    current_state: Mapping[str, object],
) -> CanonicalPlan:
    action = normalize_text(instruction.action)
    attribute = normalize_text(instruction.attribute)
    value = instruction.value.strip()
    service: str
    service_data: dict[str, object] = {"entity_id": entity.entity_id}
    expected = controlled_projection(current_state, entity.domain)

    if entity.domain == "light":
        light_attributes = current_state.get("attributes", {})
        if not isinstance(light_attributes, Mapping):
            raise GroundingError("light attributes must be an object")
        color_modes = {
            normalize_text(mode) for mode in light_attributes.get("supported_color_modes", ())
            if isinstance(mode, str)
        }
        if action == "turnon":
            _require_placeholders(instruction, "attribute", "value", "unit")
            service, expected["state"] = "turn_on", "on"
            # HA does not expose the retained brightness/color values while a
            # light is off, so a bare turn-on can only promise the on state.
            for key in ("brightness", "color_temp_kelvin", "rgb_color"):
                expected.pop(key, None)
        elif action == "turnoff":
            _require_placeholders(instruction, "attribute", "value", "unit")
            service, expected["state"] = "turn_off", "off"
            for key in ("brightness", "color_temp_kelvin", "rgb_color"):
                if key in expected:
                    expected[key] = None
        elif action == "set" and attribute == "brightness":
            _require_unit(instruction, "Percent")
            if not color_modes.intersection({"brightness", "white", "color temp", "color_temp", "rgb", "rgbw", "rgbww", "hs", "xy"}):
                raise GroundingError("light entity does not advertise brightness support")
            percent = _numeric(value, minimum=0, maximum=100)
            service_data["brightness_pct"] = percent
            service = "turn_on"
            expected["state"] = "off" if percent == 0 else "on"
            expected["brightness"] = None if percent == 0 else round(percent * 255 / 100)
        elif action == "set" and attribute == "color":
            _require_unit(instruction, "*")
            if not color_modes.intersection({"rgb", "rgbw", "rgbww", "hs", "xy"}):
                raise GroundingError("light entity does not advertise color support")
            color = normalize_text(value)
            if color not in COLOR_RGB:
                raise GroundingError(f"unsupported light color: {value!r}")
            service_data["rgb_color"] = COLOR_RGB[color]
            service, expected["state"] = "turn_on", "on"
            expected["rgb_color"] = COLOR_RGB[color]
        elif action == "set" and attribute == "colortemperature":
            _require_unit(instruction, "Kelvin")
            if not color_modes.intersection({"color temp", "color_temp"}):
                raise GroundingError("light entity does not advertise color-temperature support")
            minimum = float(light_attributes.get("min_color_temp_kelvin", 3000))
            maximum = float(light_attributes.get("max_color_temp_kelvin", 6500))
            kelvin = _numeric(value, minimum=minimum, maximum=maximum)
            if not kelvin.is_integer():
                raise GroundingError("light color temperature must be an integer Kelvin value")
            kelvin = int(kelvin)
            service_data["color_temp_kelvin"] = kelvin
            service, expected["state"] = "turn_on", "on"
            expected["color_temp_kelvin"] = round(kelvin)
        elif action in {"adjustup", "adjustdown"} and attribute == "brightness":
            _require_adjust_unit(instruction, "Percent")
            if not color_modes.intersection({"brightness", "white", "color temp", "color_temp", "rgb", "rgbw", "rgbww", "hs", "xy"}):
                raise GroundingError("light entity does not advertise brightness support")
            before = current_state.get("attributes", {})
            current = float(before.get("brightness", 0)) * 100 / 255
            step = 10.0 if value == "*" else _numeric(value, minimum=0, maximum=100)
            target = min(100.0, current + step) if action == "adjustup" else max(0.0, current - step)
            # Bind the postcondition to the value actually sent to Home
            # Assistant.  Rounding only the service payload can otherwise
            # create a one-level mismatch at half-step boundaries (for
            # example, 128/255 minus 10 percent).
            dispatched_target = round(target, 2)
            service_data["brightness_pct"] = dispatched_target
            service = "turn_on"
            expected["state"] = "off" if dispatched_target == 0 else "on"
            expected["brightness"] = (
                None
                if dispatched_target == 0
                else round(dispatched_target * 255 / 100)
            )
        else:
            raise GroundingError(f"unsupported light operation: {instruction.to_pipe()}")
    elif entity.domain == "cover":
        cover_attributes = current_state.get("attributes", {})
        if not isinstance(cover_attributes, Mapping):
            raise GroundingError("cover attributes must be an object")
        supported_features = int(cover_attributes.get("supported_features", 0))
        if action == "turnon":
            _require_placeholders(instruction, "attribute", "value", "unit")
            if not (supported_features & 1):
                raise GroundingError("cover entity does not advertise open support")
            service, expected["state"] = "open_cover", "open"
            if "current_position" in expected:
                expected["current_position"] = 100
        elif action == "turnoff":
            _require_placeholders(instruction, "attribute", "value", "unit")
            if not (supported_features & 2):
                raise GroundingError("cover entity does not advertise close support")
            service, expected["state"] = "close_cover", "closed"
            if "current_position" in expected:
                expected["current_position"] = 0
        elif action == "set" and attribute in {"position", "openness"}:
            _require_unit(instruction, "Percent")
            if not (supported_features & 4):
                raise GroundingError("cover entity does not advertise position support")
            position = _numeric(value, minimum=0, maximum=100)
            if not position.is_integer():
                raise GroundingError("cover position must be an integer percent")
            position = int(position)
            service_data["position"] = position
            service = "set_cover_position"
            expected["state"] = "closed" if position == 0 else "open"
            expected["current_position"] = round(position)
        elif action in {"adjustup", "adjustdown"} and attribute in {"position", "openness"}:
            _require_adjust_unit(instruction, "Percent")
            if not (supported_features & 4):
                raise GroundingError("cover entity does not advertise position support")
            if "current_position" not in cover_attributes:
                raise GroundingError("cover adjustment requires an observed current_position")
            current = float(cover_attributes["current_position"])
            if not math.isfinite(current) or not current.is_integer():
                raise GroundingError("cover current_position must be an integer percent")
            current = int(current)
            step = 10.0 if value == "*" else _numeric(value, minimum=0, maximum=100)
            if not step.is_integer():
                raise GroundingError("cover position adjustment must be an integer percent")
            step = int(step)
            target = min(100.0, current + step) if action == "adjustup" else max(0.0, current - step)
            target = int(target)
            service_data["position"] = target
            service = "set_cover_position"
            expected["state"] = "closed" if target == 0 else "open"
            expected["current_position"] = round(target)
        else:
            raise GroundingError(f"unsupported cover operation: {instruction.to_pipe()}")
    else:
        attributes = current_state.get("attributes", {})
        if not isinstance(attributes, Mapping):
            raise GroundingError("climate attributes must be an object")
        hvac_modes = {
            normalize_text(mode): mode for mode in attributes.get("hvac_modes", ())
            if isinstance(mode, str)
        }
        fan_modes = {
            normalize_text(mode): mode for mode in attributes.get("fan_modes", ())
            if isinstance(mode, str)
        }
        supported_features = int(attributes.get("supported_features", 0))
        temperature_unit = str(attributes.get("temperature_unit", ""))
        if not hvac_modes:
            raise GroundingError("climate entity does not advertise hvac_modes")
        if action == "turnon":
            _require_placeholders(instruction, "attribute", "value", "unit")
            active_modes = [value for key, value in hvac_modes.items() if key != "off"]
            if len(active_modes) != 1:
                raise GroundingError(
                    "climate turnOn requires exactly one advertised active mode; confirm a mode explicitly"
                )
            service_data["hvac_mode"] = active_modes[0]
            service, expected["state"] = "set_hvac_mode", active_modes[0]
        elif action == "turnoff":
            _require_placeholders(instruction, "attribute", "value", "unit")
            if "off" in hvac_modes:
                service = "set_hvac_mode"
                service_data["hvac_mode"] = hvac_modes["off"]
            elif supported_features & 128:
                # Some integrations expose TURN_OFF as a feature instead of
                # listing off in hvac_modes.
                service = "turn_off"
            else:
                raise GroundingError("climate entity does not advertise turn-off support")
            expected["state"] = "off"
        elif action == "set" and attribute == "temperature":
            _require_unit(instruction, "Celsius")
            if temperature_unit not in {"°C", "C", "Celsius"}:
                raise GroundingError("only Celsius climate entities are supported")
            if not (supported_features & 1):
                raise GroundingError("climate entity does not support target temperature")
            minimum = float(attributes.get("min_temp", 16))
            maximum = float(attributes.get("max_temp", 30))
            temperature = _numeric(value, minimum=minimum, maximum=maximum)
            _require_temperature_alignment(attributes, temperature, minimum)
            service_data["temperature"] = temperature
            service = "set_temperature"
            expected["temperature"] = temperature
        elif action == "set" and attribute == "mode":
            _require_unit(instruction, "*")
            mode = normalize_text(value)
            mode = "fan only" if mode == "fan" else mode
            if mode not in hvac_modes or mode == "off":
                raise GroundingError(f"unsupported climate mode: {value!r}")
            advertised_mode = hvac_modes[mode]
            service_data["hvac_mode"] = advertised_mode
            service = "set_hvac_mode"
            expected["state"] = advertised_mode
        elif action == "set" and attribute in {"windspeed", "wind speed", "fan speed"}:
            _require_unit(instruction, "Level")
            fan_mode = normalize_text(value)
            if not (supported_features & 8) or fan_mode not in fan_modes:
                raise GroundingError(f"unsupported fan mode: {value!r}")
            advertised_fan_mode = fan_modes[fan_mode]
            service_data["fan_mode"] = advertised_fan_mode
            service = "set_fan_mode"
            expected["fan_mode"] = advertised_fan_mode
        elif action in {"adjustup", "adjustdown"} and attribute == "temperature":
            _require_adjust_unit(instruction, "Celsius")
            if temperature_unit not in {"°C", "C", "Celsius"} or not (supported_features & 1):
                raise GroundingError("climate entity does not support Celsius target temperature")
            current = float(current_state.get("attributes", {}).get("temperature", 24))
            if not math.isfinite(current):
                raise GroundingError("current climate temperature must be finite")
            step = 1.0 if value == "*" else _numeric(value, minimum=0, maximum=14)
            minimum = float(attributes.get("min_temp", 16))
            maximum = float(attributes.get("max_temp", 30))
            target = min(maximum, current + step) if action == "adjustup" else max(minimum, current - step)
            _require_temperature_alignment(attributes, target, minimum)
            service_data["temperature"] = target
            service = "set_temperature"
            expected["temperature"] = target
        else:
            raise GroundingError(f"unsupported climate operation: {instruction.to_pipe()}")

    return CanonicalPlan(
        source_slots=instruction.canonical_slots(),
        entity_id=entity.entity_id,
        domain=entity.domain,
        service=service,
        service_data=service_data,
        expected_projection=expected,
    )


def projection_matches(actual: Mapping[str, object], expected: Mapping[str, object]) -> bool:
    """Match all operation-controlled fields while allowing extra HA state."""

    if not set(expected).issubset(actual):
        return False
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if isinstance(expected_value, float) and isinstance(actual_value, (int, float)):
            if not math.isclose(float(actual_value), expected_value, rel_tol=0, abs_tol=0.01):
                return False
        elif actual_value != expected_value:
            return False
    return True


class InMemoryHAAdapter:
    """Deterministic adapter used for policy evaluation and offline replay."""

    def __init__(self, states: Mapping[str, Mapping[str, object]]):
        self._states = json.loads(json.dumps(states))
        for entity_id, state in self._states.items():
            validate_state_shape(state, entity_id)
        self.sut_calls: list[dict[str, object]] = []
        self.setup_calls: list[dict[str, object]] = []
        self.force_postcondition_mismatch = False

    def get_state(self, entity_id: str) -> dict[str, object]:
        try:
            state = json.loads(json.dumps(self._states[entity_id]))
        except KeyError as exc:
            raise AdapterError(f"state not found for allowed entity: {entity_id}") from exc
        validate_state_shape(state, entity_id)
        return state

    def set_state_for_setup(self, entity_id: str, state: Mapping[str, object]) -> None:
        validate_state_shape(state, entity_id)
        self._states[entity_id] = json.loads(json.dumps(state))
        self.setup_calls.append({"kind": "setup", "entity_id": entity_id})

    def mutate_state_for_setup(self, entity_id: str) -> None:
        state = self.get_state(entity_id)
        domain = entity_id.split(".", 1)[0]
        if domain == "light":
            state["state"] = "off" if state.get("state") == "on" else "on"
        elif domain == "cover":
            current = int(state.get("attributes", {}).get("current_position", 0))
            updated = 100 - current
            state.setdefault("attributes", {})["current_position"] = updated
            state["state"] = "closed" if updated == 0 else "open"
        else:
            current = float(state.get("attributes", {}).get("temperature", 24))
            state.setdefault("attributes", {})["temperature"] = 25 if current != 25 else 24
        self.set_state_for_setup(entity_id, state)

    def call_service(self, domain: str, service: str, data: Mapping[str, object]) -> ServiceCallResult:
        entity_id = str(data["entity_id"])
        if not entity_id.startswith(f"{domain}."):
            raise ServiceCallError(
                "service domain does not match entity",
                attempted=False,
                acknowledged=False,
                outcome_unknown=False,
            )
        try:
            before = self.get_state(entity_id)
        except Exception as exc:
            raise ServiceCallError(
                "state read failed before in-memory dispatch",
                attempted=False,
                acknowledged=False,
                outcome_unknown=False,
            ) from exc
        after = self.get_state(entity_id)
        attrs = after.setdefault("attributes", {})
        event = {
            "kind": "sut",
            "domain": domain,
            "service": service,
            "data": dict(data),
            "before": controlled_projection(before, domain),
            "after": None,
            "acknowledged": False,
            "outcome": "attempted",
        }
        self.sut_calls.append(event)
        try:
            if domain == "light":
                if service == "turn_on":
                    if "brightness_pct" in data:
                        brightness_pct = float(data["brightness_pct"])
                        if brightness_pct == 0:
                            after["state"] = "off"
                        else:
                            after["state"] = "on"
                            attrs["brightness"] = round(brightness_pct * 255 / 100)
                    else:
                        after["state"] = "on"
                    if "rgb_color" in data:
                        attrs["rgb_color"] = list(data["rgb_color"])
                    if "color_temp_kelvin" in data:
                        attrs["color_temp_kelvin"] = round(float(data["color_temp_kelvin"]))
                elif service == "turn_off":
                    after["state"] = "off"
                else:
                    raise AdapterError(f"unsupported light service: {service}")
            elif domain == "cover":
                if service == "open_cover":
                    after["state"], attrs["current_position"] = "open", 100
                elif service == "close_cover":
                    after["state"], attrs["current_position"] = "closed", 0
                elif service == "set_cover_position":
                    position = round(float(data["position"]))
                    after["state"] = "closed" if position == 0 else "open"
                    attrs["current_position"] = position
                else:
                    raise AdapterError(f"unsupported cover service: {service}")
            elif domain == "climate":
                if service == "turn_on":
                    after["state"] = "cool"
                elif service == "turn_off":
                    after["state"] = "off"
                elif service == "set_temperature":
                    attrs["temperature"] = float(data["temperature"])
                elif service == "set_hvac_mode":
                    after["state"] = data["hvac_mode"]
                elif service == "set_fan_mode":
                    attrs["fan_mode"] = data["fan_mode"]
                else:
                    raise AdapterError(f"unsupported climate service: {service}")
            else:
                raise AdapterError(f"unsupported service domain: {domain}")
        except Exception as exc:
            event["outcome"] = "rejected_before_acknowledgement"
            raise ServiceCallError(
                "in-memory service rejected the request",
                attempted=True,
                acknowledged=False,
                outcome_unknown=False,
            ) from exc
        if not self.force_postcondition_mismatch:
            self._states[entity_id] = after
        observed = self.get_state(entity_id)
        event["acknowledged"] = True
        event["outcome"] = "observed"
        event["after"] = controlled_projection(observed, domain)
        return ServiceCallResult(
            after=observed,
            attempted=True,
            acknowledged=True,
            outcome_unknown=False,
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class _RequestFailure(AdapterError):
    def __init__(
        self,
        message: str,
        *,
        response_received: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.response_received = response_received
        self.status_code = status_code


class HomeAssistantRESTAdapter:
    """Minimal client for Home Assistant's official REST API.

    The default loopback restriction prevents an example token from being used
    against a public or production Home Assistant instance by accident.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 10.0,
        poll_seconds: float = 5.0,
        allow_non_loopback: bool = False,
    ):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if not allow_non_loopback and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("only loopback Home Assistant URLs are allowed by default")
        if allow_non_loopback and parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and parsed.scheme != "https":
            raise ValueError("non-loopback Home Assistant URLs require HTTPS")
        if not token:
            raise ValueError("Home Assistant token is required")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._poll_seconds = poll_seconds
        self._opener = urllib.request.build_opener(_NoRedirect())
        self.sut_calls: list[dict[str, object]] = []

    def _request(self, method: str, path: str, payload: Mapping[str, object] | None = None) -> object:
        body = None if payload is None else canonical_json(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            exc.close()
            raise _RequestFailure(
                f"Home Assistant REST request failed for {method} {path}",
                response_received=True,
                status_code=status_code,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise _RequestFailure(
                f"Home Assistant REST request failed for {method} {path}",
                response_received=False,
            ) from exc
        return json.loads(raw) if raw else None

    def get_state(self, entity_id: str) -> dict[str, object]:
        encoded = urllib.parse.quote(entity_id, safe=".")
        result = self._request("GET", f"/api/states/{encoded}")
        if not isinstance(result, dict):
            raise AdapterError("Home Assistant returned a non-object state")
        validate_state_shape(result, entity_id)
        if entity_id.startswith("climate.") and "temperature_unit" not in result["attributes"]:
            config = self._request("GET", "/api/config")
            if not isinstance(config, dict):
                raise AdapterError("Home Assistant returned a non-object config")
            unit_system = config.get("unit_system")
            if not isinstance(unit_system, Mapping):
                raise AdapterError("Home Assistant config has no unit_system object")
            temperature_unit = unit_system.get("temperature")
            if not isinstance(temperature_unit, str) or not temperature_unit.strip():
                raise AdapterError("Home Assistant config has no temperature unit")
            result = copy.deepcopy(result)
            attributes = result["attributes"]
            if not isinstance(attributes, dict):
                attributes = dict(attributes)
                result["attributes"] = attributes
            attributes["temperature_unit"] = temperature_unit
        return result

    def call_service(self, domain: str, service: str, data: Mapping[str, object]) -> ServiceCallResult:
        entity_id = str(data["entity_id"])
        if not entity_id.startswith(f"{domain}."):
            raise ServiceCallError(
                "service domain does not match entity",
                attempted=False,
                acknowledged=False,
                outcome_unknown=False,
            )
        try:
            before = self.get_state(entity_id)
        except Exception as exc:
            raise ServiceCallError(
                "Home Assistant state read failed before dispatch",
                attempted=False,
                acknowledged=False,
                outcome_unknown=False,
            ) from exc
        event = {
            "kind": "sut",
            "domain": domain,
            "service": service,
            "data": dict(data),
            "before": controlled_projection(before, domain),
            "after": None,
            "acknowledged": False,
            "outcome": "attempted",
        }
        self.sut_calls.append(event)
        try:
            self._request("POST", f"/api/services/{domain}/{service}", data)
        except _RequestFailure as exc:
            # A 4xx is an explicit rejection.  A 5xx can arrive after HA (or an
            # integration it called) has already applied a side effect, so the
            # exact outcome remains unknown even though an HTTP response exists.
            unknown = not exc.response_received or (
                exc.status_code is not None and 500 <= exc.status_code < 600
            )
            event["outcome"] = "request_error_outcome_unknown" if unknown else "request_rejected"
            raise ServiceCallError(
                "Home Assistant service request failed",
                attempted=True,
                acknowledged=False,
                outcome_unknown=unknown,
            ) from exc
        event["acknowledged"] = True
        event["outcome"] = "acknowledged_state_pending"
        deadline = time.monotonic() + self._poll_seconds
        try:
            after = self.get_state(entity_id)
            while time.monotonic() < deadline and after == before:
                time.sleep(0.1)
                after = self.get_state(entity_id)
        except Exception as exc:
            event["outcome"] = "acknowledged_state_unknown"
            raise ServiceCallError(
                "Home Assistant acknowledged the service but state observation failed",
                attempted=True,
                acknowledged=True,
                outcome_unknown=True,
            ) from exc
        event["after"] = controlled_projection(after, domain)
        event["outcome"] = "observed"
        return ServiceCallResult(
            after=after,
            attempted=True,
            acknowledged=True,
            outcome_unknown=False,
        )

    def wait_for_projection(
        self,
        entity_id: str,
        domain: str,
        expected: Mapping[str, object],
    ) -> dict[str, object]:
        """Poll through transient HA states until the approved projection is visible."""

        deadline = time.monotonic() + self._poll_seconds
        latest = self.get_state(entity_id)
        while not projection_matches(controlled_projection(latest, domain), expected):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
            latest = self.get_state(entity_id)
        return latest


def state_binding(
    adapter: Any,
    registry: EntityRegistry,
    entity_ids: Iterable[str],
    *,
    for_planning: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for entity_id in sorted(set(entity_ids)):
        entity = registry.get(entity_id)
        raw = adapter.get_state(entity_id)
        result[entity_id] = (
            planning_projection(raw, entity.domain)
            if for_planning
            else controlled_projection(raw, entity.domain)
        )
    return result


@dataclass(frozen=True)
class Confirmation:
    actor_id: str
    session_id: str
    nonce: str
    request_digest: str
    clarification_digest: str
    plan_digest: str
    candidate_digest: str


@dataclass(frozen=True)
class PreparedAction:
    actor_id: str
    session_id: str
    nonce: str
    request_digest: str
    clarification_digest: str
    plan_digest: str
    candidate_digest: str
    entity_id: str
    created_at: float
    expires_at: float

    def confirmation(self) -> Confirmation:
        return Confirmation(
            actor_id=self.actor_id,
            session_id=self.session_id,
            nonce=self.nonce,
            request_digest=self.request_digest,
            clarification_digest=self.clarification_digest,
            plan_digest=self.plan_digest,
            candidate_digest=self.candidate_digest,
        )


@dataclass
class _StoredAction:
    actor_id: str
    session_id: str
    nonce: str
    utterance: str
    raw_output: str
    context_entity_ids: tuple[str, ...]
    request_digest: str
    clarification_digest: str
    plan: CanonicalPlan
    plan_digest: str
    candidate_ids: tuple[str, ...]
    state_entity_ids: tuple[str, ...]
    candidate_digest: str
    state_digest: str
    created_at: float
    expires_at: float
    status: str = "PREPARED"
    consumed: bool = False


@dataclass(frozen=True)
class _ActionTombstone:
    nonce: str
    plan_digest: str
    candidate_digest: str
    created_at: float
    expires_at: float
    status: str
    consumed: bool


@dataclass(frozen=True)
class CommitResult:
    accepted: bool
    dispatched: bool
    reason: str
    status: str
    nonce: str
    plan_digest: str | None = None
    before: Mapping[str, object] | None = None
    after: Mapping[str, object] | None = None
    acknowledged: bool = False
    outcome_unknown: bool = False
    before_registry_digest: str | None = None
    after_registry_digest: str | None = None


class PreparedActionStore:
    """Thread-safe one-time authorization store for a single process."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        nonce_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
        max_items: int = 10_000,
    ):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        self._ttl = ttl_seconds
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._max_items = max_items
        self._items: dict[str, _StoredAction | _ActionTombstone] = {}
        self._entity_locks: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()

    def prepare(
        self,
        *,
        actor_id: str,
        session_id: str,
        grounded: GroundedRequest,
        registry: EntityRegistry,
        adapter: Any,
        clarification_answer: str | None = None,
        confirmed_instruction: DomuxInstruction | None = None,
        state_dependencies: Sequence[str] = (),
    ) -> PreparedAction:
        if not actor_id or not session_id:
            raise ValueError("actor_id and session_id are required")
        context = SessionContext(grounded.context_entity_ids)
        server_grounded = ground_domux_request(
            grounded.utterance,
            grounded.raw_output,
            registry,
            context,
        )
        if server_grounded.request_digest != grounded.request_digest:
            raise GroundingError("grounded request does not match the server-side reconstruction")
        if server_grounded.clarification.required:
            if clarification_answer is None or confirmed_instruction is None:
                raise GroundingError("clarification answer and complete confirmed instruction are required")
            resolved = resolve_clarification_submission(
                server_grounded,
                answer=clarification_answer,
                confirmed_instruction=confirmed_instruction,
                registry=registry,
            )
        else:
            if clarification_answer is not None or confirmed_instruction is not None:
                raise GroundingError("a unique request cannot be replaced by client confirmation fields")
            resolved = resolve_unique_request(server_grounded, registry)

        candidate_ids = tuple(sorted(entity.entity_id for entity in server_grounded.candidates))
        chosen = registry.get(resolved.chosen.entity_id)
        context_entity_ids = tuple(server_grounded.context_entity_ids)
        state_entity_ids = tuple(sorted({chosen.entity_id, *context_entity_ids, *state_dependencies}))
        for entity_id in state_entity_ids:
            registry.get(entity_id)
        raw_states: dict[str, Mapping[str, object]] = {}
        for entity_id in state_entity_ids:
            raw_states[entity_id] = adapter.get_state(entity_id)
        plan = build_plan(resolved.confirmed_instruction, chosen, raw_states[chosen.entity_id])
        plan_digest = plan.digest
        candidate_digest = registry.metadata_digest(candidate_ids)
        bound_projection = {
            entity_id: planning_projection(raw_states[entity_id], registry.get(entity_id).domain)
            for entity_id in state_entity_ids
        }
        state_digest = digest_json(bound_projection)
        created = self._clock()
        nonce = self._nonce_factory()
        if not nonce:
            raise ValueError("nonce factory returned an empty value")
        action = _StoredAction(
            actor_id=actor_id,
            session_id=session_id,
            nonce=nonce,
            utterance=server_grounded.utterance,
            raw_output=server_grounded.raw_output,
            context_entity_ids=context_entity_ids,
            request_digest=server_grounded.request_digest,
            clarification_digest=resolved.clarification_digest,
            plan=plan,
            plan_digest=plan_digest,
            candidate_ids=candidate_ids,
            state_entity_ids=state_entity_ids,
            candidate_digest=candidate_digest,
            state_digest=state_digest,
            created_at=created,
            expires_at=created + self._ttl,
        )
        with self._lock:
            self._purge_expired_locked(created)
            if len(self._items) >= self._max_items:
                now = self._clock()
                removable = [
                    key for key, item in self._items.items()
                    if item.consumed or item.status != "PREPARED" or now > item.expires_at
                ]
                for key in removable:
                    self._items.pop(key, None)
            if len(self._items) >= self._max_items:
                raise RuntimeError("prepared-action store capacity exceeded")
            if nonce in self._items:
                raise ValueError("nonce collision")
            self._items[nonce] = action
        return PreparedAction(
            actor_id=actor_id,
            session_id=session_id,
            nonce=nonce,
            request_digest=action.request_digest,
            clarification_digest=action.clarification_digest,
            plan_digest=action.plan_digest,
            candidate_digest=action.candidate_digest,
            entity_id=action.plan.entity_id,
            created_at=action.created_at,
            expires_at=action.expires_at,
        )

    def _reject(
        self,
        action: _StoredAction | None,
        nonce: str,
        reason: str,
        status: str,
        *,
        mutate: bool = True,
    ) -> CommitResult:
        if action is not None and mutate:
            action.status = status
        result = CommitResult(
            accepted=False,
            dispatched=False,
            reason=reason,
            status=status,
            nonce=nonce,
            plan_digest=None if action is None else action.plan_digest,
        )
        if action is not None and mutate and status != "PREPARED":
            with self._lock:
                self._redact_locked(action)
        return result

    def _redact_locked(self, action: _StoredAction) -> None:
        """Replace terminal actions with a digest-only replay tombstone."""

        if self._items.get(action.nonce) is not action:
            return
        self._items[action.nonce] = _ActionTombstone(
            nonce=action.nonce,
            plan_digest=action.plan_digest,
            candidate_digest=action.candidate_digest,
            created_at=action.created_at,
            expires_at=action.expires_at,
            status=action.status,
            consumed=action.consumed,
        )

    def _purge_expired_locked(self, now: float) -> int:
        expired = [
            item for item in self._items.values()
            if isinstance(item, _StoredAction)
            and item.status == "PREPARED"
            and now > item.expires_at
        ]
        for item in expired:
            item.status = "EXPIRED"
            self._redact_locked(item)
        return len(expired)

    def purge_expired(self) -> int:
        """Redact abandoned prepared requests; applications may call this from a timer."""

        with self._lock:
            return self._purge_expired_locked(self._clock())

    @staticmethod
    def _tombstone_result(action: _ActionTombstone) -> CommitResult:
        return CommitResult(
            accepted=False,
            dispatched=False,
            reason="replayed_nonce" if action.consumed else "action_not_prepared",
            status=action.status,
            nonce=action.nonce,
            plan_digest=action.plan_digest,
        )

    def _confirmation_error(
        self,
        action: _StoredAction,
        confirmation: Confirmation,
        *,
        enforce_lifecycle: bool,
    ) -> str | None:
        if enforce_lifecycle and action.consumed:
            return "replayed_nonce"
        if enforce_lifecycle and action.status != "PREPARED":
            return "action_not_prepared"
        checks = (
            (confirmation.actor_id, action.actor_id, "actor_mismatch"),
            (confirmation.session_id, action.session_id, "session_mismatch"),
            (confirmation.request_digest, action.request_digest, "request_mismatch"),
            (confirmation.clarification_digest, action.clarification_digest, "clarification_mismatch"),
            (confirmation.plan_digest, action.plan_digest, "plan_mismatch"),
            (confirmation.candidate_digest, action.candidate_digest, "confirmation_candidate_mismatch"),
        )
        return next((reason for actual, expected, reason in checks if actual != expected), None)

    def _execute(
        self,
        action: _StoredAction,
        *,
        registry: EntityRegistry,
        adapter: Any,
        before_all: Mapping[str, Mapping[str, object]],
    ) -> CommitResult:
        before = dict(before_all[action.plan.entity_id])
        before_digest = digest_json(before_all)
        try:
            receipt = adapter.call_service(
                action.plan.domain,
                action.plan.service,
                action.plan.service_data,
            )
            if not isinstance(receipt, ServiceCallResult):
                raise ServiceCallError(
                    "adapter violated the ServiceCallResult contract",
                    attempted=True,
                    acknowledged=False,
                    outcome_unknown=True,
                )
            after_raw = receipt.after
        except ServiceCallError as exc:
            action.status = "FAILED_DISPATCH"
            return CommitResult(
                accepted=True,
                dispatched=exc.attempted,
                acknowledged=exc.acknowledged,
                outcome_unknown=exc.outcome_unknown,
                reason="dispatch_failed",
                status=action.status,
                nonce=action.nonce,
                plan_digest=action.plan_digest,
                before=before,
                after=None,
                before_registry_digest=before_digest,
            )
        except Exception:
            action.status = "FAILED_DISPATCH"
            return CommitResult(
                accepted=True,
                dispatched=True,
                acknowledged=False,
                outcome_unknown=True,
                reason="dispatch_failed",
                status=action.status,
                nonce=action.nonce,
                plan_digest=action.plan_digest,
                before=before,
                after=None,
                before_registry_digest=before_digest,
            )
        wait_for_projection = getattr(adapter, "wait_for_projection", None)
        if callable(wait_for_projection):
            try:
                after_raw = wait_for_projection(
                    action.plan.entity_id,
                    action.plan.domain,
                    action.plan.expected_projection,
                )
            except Exception:
                action.status = "FAILED_POSTCONDITION"
                return CommitResult(
                    accepted=True,
                    dispatched=receipt.attempted,
                    acknowledged=receipt.acknowledged,
                    outcome_unknown=True,
                    reason="postcondition_state_unknown",
                    status=action.status,
                    nonce=action.nonce,
                    plan_digest=action.plan_digest,
                    before=before,
                    after=controlled_projection(after_raw, action.plan.domain),
                    before_registry_digest=before_digest,
                )
        try:
            after_all = state_binding(adapter, registry, (entity.entity_id for entity in registry.entities))
        except Exception:
            action.status = "FAILED_POSTCONDITION"
            return CommitResult(
                accepted=True,
                dispatched=receipt.attempted,
                acknowledged=receipt.acknowledged,
                outcome_unknown=True,
                reason="postcondition_state_unknown",
                status=action.status,
                nonce=action.nonce,
                plan_digest=action.plan_digest,
                before=before,
                after=controlled_projection(after_raw, action.plan.domain),
                before_registry_digest=before_digest,
            )
        after = after_all[action.plan.entity_id]
        exact = set(after_all) == set(before_all) and all(
            (
                projection_matches(after_all[entity_id], action.plan.expected_projection)
                if entity_id == action.plan.entity_id
                else after_all[entity_id] == before_all[entity_id]
            )
            for entity_id in before_all
        )
        after_digest = digest_json(after_all)
        if not exact:
            action.status = "FAILED_POSTCONDITION"
            return CommitResult(
                accepted=True,
                dispatched=receipt.attempted,
                acknowledged=receipt.acknowledged,
                outcome_unknown=receipt.outcome_unknown,
                reason="postcondition_mismatch",
                status=action.status,
                nonce=action.nonce,
                plan_digest=action.plan_digest,
                before=before,
                after=after,
                before_registry_digest=before_digest,
                after_registry_digest=after_digest,
            )
        action.status = "COMMITTED"
        return CommitResult(
            accepted=True,
            dispatched=receipt.attempted,
            acknowledged=receipt.acknowledged,
            outcome_unknown=receipt.outcome_unknown,
            reason="committed",
            status=action.status,
            nonce=action.nonce,
            plan_digest=action.plan_digest,
            before=before,
            after=after,
            before_registry_digest=before_digest,
            after_registry_digest=after_digest,
        )

    def commit(
        self,
        confirmation: Confirmation,
        *,
        registry: EntityRegistry,
        adapter: Any,
    ) -> CommitResult:
        with self._lock:
            action = self._items.get(confirmation.nonce)
            if action is None:
                return self._reject(None, confirmation.nonce, "unknown_nonce", "INVALIDATED")
            if isinstance(action, _ActionTombstone):
                return self._tombstone_result(action)
            error = self._confirmation_error(action, confirmation, enforce_lifecycle=True)
            if error:
                return self._reject(action, action.nonce, error, action.status, mutate=False)
            if self._clock() > action.expires_at:
                return self._reject(action, action.nonce, "expired", "EXPIRED")
            # The postcondition asserts that no registered entity changed as a
            # side effect.  Lock that same in-process scope, in sorted order,
            # so concurrent commits cannot invalidate one another's evidence.
            lock_entity_ids = tuple(sorted(entity.entity_id for entity in registry.entities))
            entity_locks = tuple(
                self._entity_locks.setdefault(entity_id, threading.Lock())
                for entity_id in lock_entity_ids
            )

        with ExitStack() as lock_stack:
            for entity_lock in entity_locks:
                lock_stack.enter_context(entity_lock)
            with self._lock:
                action = self._items.get(confirmation.nonce)
                if action is None:
                    return self._reject(None, confirmation.nonce, "unknown_nonce", "INVALIDATED")
                if isinstance(action, _ActionTombstone):
                    return self._tombstone_result(action)
                error = self._confirmation_error(action, confirmation, enforce_lifecycle=True)
                if error:
                    return self._reject(action, action.nonce, error, action.status, mutate=False)
                if self._clock() > action.expires_at:
                    return self._reject(action, action.nonce, "expired", "EXPIRED")

            context = SessionContext(action.context_entity_ids)
            regrounded = ground_domux_request(action.utterance, action.raw_output, registry, context)
            current_candidate_ids = tuple(sorted(entity.entity_id for entity in regrounded.candidates))
            current_candidate_digest = registry.metadata_digest(current_candidate_ids)
            if (
                current_candidate_ids != action.candidate_ids
                or current_candidate_digest != action.candidate_digest
            ):
                with self._lock:
                    return self._reject(action, action.nonce, "candidate_set_changed", "INVALIDATED")
            try:
                before_all = state_binding(
                    adapter,
                    registry,
                    (entity.entity_id for entity in registry.entities),
                )
            except Exception:
                return CommitResult(
                    accepted=False,
                    dispatched=False,
                    reason="predispatch_state_read_failed",
                    status=action.status,
                    nonce=action.nonce,
                    plan_digest=action.plan_digest,
                )
            try:
                current_bound = state_binding(
                    adapter,
                    registry,
                    action.state_entity_ids,
                    for_planning=True,
                )
            except Exception:
                return CommitResult(
                    accepted=False,
                    dispatched=False,
                    reason="predispatch_state_read_failed",
                    status=action.status,
                    nonce=action.nonce,
                    plan_digest=action.plan_digest,
                )
            current_state_digest = digest_json(current_bound)
            if current_state_digest != action.state_digest:
                with self._lock:
                    return self._reject(action, action.nonce, "state_changed", "INVALIDATED")
            with self._lock:
                error = self._confirmation_error(action, confirmation, enforce_lifecycle=True)
                if error:
                    return self._reject(action, action.nonce, error, action.status, mutate=False)
                if self._clock() > action.expires_at:
                    return self._reject(action, action.nonce, "expired", "EXPIRED")
                action.consumed = True
                action.status = "DISPATCHING"
            result = self._execute(action, registry=registry, adapter=adapter, before_all=before_all)
            with self._lock:
                self._redact_locked(action)
            return result

    def snapshot(self, nonce: str) -> dict[str, object]:
        with self._lock:
            action = self._items[nonce]
            if isinstance(action, _ActionTombstone):
                return {
                    "nonce": action.nonce,
                    "plan_digest": action.plan_digest,
                    "candidate_digest": action.candidate_digest,
                    "created_at": action.created_at,
                    "expires_at": action.expires_at,
                    "status": action.status,
                    "consumed": action.consumed,
                    "redacted": True,
                }
            return {
                "actor_id": action.actor_id,
                "session_id": action.session_id,
                "nonce": action.nonce,
                "request_digest": action.request_digest,
                "clarification_digest": action.clarification_digest,
                "plan_digest": action.plan_digest,
                "candidate_ids": list(action.candidate_ids),
                "state_entity_ids": list(action.state_entity_ids),
                "candidate_digest": action.candidate_digest,
                "state_digest": action.state_digest,
                "created_at": action.created_at,
                "expires_at": action.expires_at,
                "status": action.status,
                "consumed": action.consumed,
                "plan": action.plan.stable_dict(),
                "redacted": False,
            }


class ClarifyPrepareStore(PreparedActionStore):
    """Credible baseline: server plan/session binding without temporal guards.

    It deliberately does not revalidate candidate metadata, relevant state, TTL,
    or one-time use.  Those are the only independent variables added by the
    Clarify-and-Commit store above.
    """

    def commit(
        self,
        confirmation: Confirmation,
        *,
        registry: EntityRegistry,
        adapter: Any,
    ) -> CommitResult:
        with self._lock:
            action = self._items.get(confirmation.nonce)
            if action is None:
                return self._reject(None, confirmation.nonce, "unknown_nonce", "INVALIDATED")
            error = self._confirmation_error(action, confirmation, enforce_lifecycle=False)
            if error:
                return self._reject(action, action.nonce, error, action.status, mutate=False)
            action.status = "DISPATCHING"
        try:
            before_all = state_binding(
                adapter,
                registry,
                (entity.entity_id for entity in registry.entities),
            )
        except Exception:
            return CommitResult(
                accepted=False,
                dispatched=False,
                reason="predispatch_state_read_failed",
                status=action.status,
                nonce=action.nonce,
                plan_digest=action.plan_digest,
            )
        return self._execute(action, registry=registry, adapter=adapter, before_all=before_all)


def altered_confirmation(confirmation: Confirmation, **changes: object) -> Confirmation:
    """Small explicit helper used by mutation tests and the frozen evaluator."""

    return replace(confirmation, **changes)
