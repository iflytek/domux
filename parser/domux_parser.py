"""Offline parser for Domux pipe-delimited slot output.

Turns raw model output (``action|device|attribute|value|unit|room|floor``,
one slot per line) into validated, structured JSON. Pure stdlib, no
Home Assistant dependency, no entity resolution -- that is intentionally
out of scope (see parser/README.md).

The action set, ``*`` (don't-care) semantics, ``<think>`` stripping and
newline segmentation match training/rewards/reward_plugin_slot.py so the
parser and the GRPO reward agree on what "well-formed" means.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict, field
from typing import Any

# field order: action|device|attribute|value|unit|room|floor
FIELD_NAMES = ("action", "device", "attribute", "value", "unit", "room", "floor")
NUM_FIELDS = len(FIELD_NAMES)
VALID_ACTIONS = {
    "turnOn", "turnOff", "set", "adjustUp", "adjustDown",
    "pause", "activate", "deactivate",
}
SEG_SEP = "\n"
DONT_CARE = "*"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Title-case normalization per COMMAND_SPEC.md (devices, colors, units).
# Lowercase variants map to the canonical Title Case form.
_DEVICE_NORM = {
    "light": "Light",
    "strip light": "Strip Light",
    "floor lamp": "Floor Lamp",
    "spot light": "Spot Light",
    "spotlight": "Spot Light",  # common typo
    "desk lamp": "Desk Lamp",
    "tv light strip": "TV Light Strip",
    "ac": "AC",
    "air conditioner": "AC",
    "curtain": "Curtain",
    "blind": "Blind",
    "sheer": "Sheer",
    "music": "Music",
}
_COLOR_NORM = {
    "blue": "Blue", "red": "Red", "green": "Green", "yellow": "Yellow",
    "orange": "Orange", "pink": "Pink", "purple": "Purple", "cyan": "Cyan",
    "lavender": "Lavender", "white": "White",
    "warm white": "Warm White", "cool white": "Cool White", "sky blue": "Sky Blue",
}
_UNIT_NORM = {
    "percent": "Percent", "kelvin": "Kelvin", "celsius": "Celsius",
}
_MODE_NORM = {
    "fan": "Fan", "dry": "Dry", "heat": "Heat", "cool": "Cool",
    "reading": "Reading",
}


def _normalize_token(field_name: str, token: str | None) -> str | None:
    """Apply COMMAND_SPEC.md Title Case normalization.

    The spec mandates device names, colors, and units in Title Case. This
    function maps common lowercase variants to the canonical form so the
    parser's output matches the GRPO reward's expected format.
    """
    if token is None or token == DONT_CARE:
        return token
    lower = token.lower().strip()
    if field_name == "device":
        return _DEVICE_NORM.get(lower, token)  # fallback: keep original if unknown
    if field_name == "unit":
        return _UNIT_NORM.get(lower, token)
    # value field might be a color or mode string (not just numeric)
    if field_name == "value" and isinstance(token, str):
        if lower in _COLOR_NORM:
            return _COLOR_NORM[lower]
        if lower in _MODE_NORM:
            return _MODE_NORM[lower]
    return token


@dataclass
class Slot:
    """One parsed slot line. ``*`` fields become ``None``."""
    action: str | None = None
    device: str | None = None
    attribute: str | None = None
    value: Any = None  # str, int or float
    unit: str | None = None
    room: str | None = None
    floor: Any = None  # str or int
    raw: str = ""
    valid: bool = True
    errors: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    input: str
    kind: str = "control"          # "control" | "non_control" | "empty"
    text: str | None = None         # natural-language passthrough for non_control
    slots: list[Slot] = field(default_factory=list)
    valid: bool = True
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def _coerce(name: str, token: str) -> Any:
    """Map a raw field token to a Python value.

    ``*`` -> None. The numeric fields (value, floor) are coerced to int/float
    when possible so downstream consumers don't re-parse strings.
    """
    token = token.strip()
    if token == DONT_CARE or token == "":
        return None
    if name in ("value", "floor"):
        try:
            return int(token)
        except ValueError:
            try:
                return float(token)
            except ValueError:
                return token  # leave non-numeric value (e.g. mode names) as-is
    return token


def _parse_segment(raw_seg: str) -> Slot:
    """Parse one ``a|b|c|...`` line into a Slot, recording any format errors.

    A segment is kept even when malformed (wrong field count / bad action) so
    callers can see exactly what the model emitted; ``valid`` flags whether it
    is safe to act on.

    Applies COMMAND_SPEC.md normalization (Title Case for devices/colors/units).
    """
    fields = [f.strip() for f in raw_seg.split("|")]
    errors: list[str] = []

    if len(fields) != NUM_FIELDS:
        errors.append(
            f"expected {NUM_FIELDS} fields, got {len(fields)}"
        )
    if any(f == "" for f in fields):
        errors.append("contains empty field (use '*' for unspecified)")

    # Map by position; tolerate short/long segments without IndexError.
    values = {
        name: _coerce(name, fields[i]) if i < len(fields) else None
        for i, name in enumerate(FIELD_NAMES)
    }

    # Apply COMMAND_SPEC.md normalization: Title Case for device/unit/colors.
    if values["device"]:
        norm = _normalize_token("device", str(values["device"]))
        if norm != values["device"]:
            values["device"] = norm
    if values["unit"]:
        norm = _normalize_token("unit", str(values["unit"]))
        if norm != values["unit"]:
            values["unit"] = norm
    if values["value"] and isinstance(values["value"], str):
        norm = _normalize_token("value", values["value"])
        if norm != values["value"]:
            values["value"] = norm

    # Validate action (camelCase, not normalized)
    action_tok = fields[0].strip() if fields else ""
    if action_tok not in VALID_ACTIONS:
        errors.append(f"invalid action: {action_tok!r}")

    # Warn about floor type inconsistency (string vs int), which the spec
    # doesn't resolve (all examples use `*`). Don't hard-error, just note it.
    floor_val = values.get("floor")
    if floor_val is not None and isinstance(floor_val, str) and floor_val not in {"1", "2", "3", "4", "5"}:
        errors.append(
            f"floor is string '{floor_val}' (training data uses int 1/2/3); "
            "spec ambiguous — verify intended format"
        )

    return Slot(
        **values,
        raw=raw_seg.strip(),
        valid=not errors,
        errors=errors,
    )


def parse(text: str) -> ParseResult:
    """Parse raw model output into a ParseResult.

    The model emits pipe-delimited slots ONLY for control commands; for
    questions / chit-chat it answers in natural language. So we first classify:

    - ``empty``       : nothing emitted (valid, no slots)
    - ``non_control`` : no ``|`` anywhere -> natural-language passthrough,
                        returned verbatim in ``text`` (NOT flagged as malformed)
    - ``control``     : has pipes -> split on newlines and parse each slot

    Strips ``<think>...</think>`` first. A control result is ``valid`` only if
    every segment is valid.
    """
    cleaned = _THINK_RE.sub("", text or "").strip()

    if not cleaned:
        return ParseResult(input=text, kind="empty", valid=True)

    # No pipe anywhere => the model chose natural language (non-control turn).
    if "|" not in cleaned:
        return ParseResult(
            input=text, kind="non_control", text=cleaned, valid=True
        )

    raw_segs = [s.strip() for s in cleaned.split(SEG_SEP) if s.strip()]
    slots = [_parse_segment(s) for s in raw_segs]
    errors: list[str] = []
    for i, s in enumerate(slots):
        for e in s.errors:
            errors.append(f"segment {i}: {e}")

    return ParseResult(
        input=text,
        kind="control",
        slots=slots,
        valid=all(s.valid for s in slots),
        errors=errors,
    )


def _main(argv: list[str]) -> int:
    """CLI: read commands and emit JSON.

    Usage:
        echo "turnOn|light|*|*|*|living room|1" | python domux_parser.py
        python domux_parser.py --jsonl predictions.txt > parsed.jsonl

    With ``--jsonl`` each input line is parsed independently and one compact
    JSON object is emitted per line (suitable for dataset / eval pipelines).
    Without it, the whole stdin is treated as a single (possibly multi-line)
    model output and pretty-printed.
    """
    jsonl = "--jsonl" in argv
    paths = [a for a in argv if not a.startswith("--")]

    if jsonl:
        src = open(paths[0], encoding="utf-8") if paths else sys.stdin
        try:
            for line in src:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                res = parse(line)
                sys.stdout.write(res.to_json(indent=None) + "\n")
        finally:
            if src is not sys.stdin:
                src.close()
    else:
        text = open(paths[0], encoding="utf-8").read() if paths else sys.stdin.read()
        sys.stdout.write(parse(text).to_json() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
