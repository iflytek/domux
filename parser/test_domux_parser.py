"""Unit tests for domux_parser. Run: python -m pytest parser/test_domux_parser.py
(falls back to plain `python parser/test_domux_parser.py`)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from domux_parser import parse, Slot  # noqa: E402


def test_single_basic():
    res = parse("turnOn|light|*|*|*|living room|1")
    assert res.valid
    assert len(res.slots) == 1
    s = res.slots[0]
    assert s.action == "turnOn"
    assert s.device == "Light"  # normalized to Title Case
    assert s.attribute is None  # '*' -> None
    assert s.room == "living room"
    assert s.floor == 1  # numeric coercion


def test_numeric_value_and_unit():
    res = parse("set|air conditioner|temperature|22|celsius|bedroom|*")
    s = res.slots[0]
    assert res.valid
    assert s.device == "AC"  # air conditioner -> AC
    assert s.value == 22 and isinstance(s.value, int)
    assert s.unit == "Celsius"  # normalized
    assert s.floor is None


def test_float_value():
    res = parse("set|air conditioner|temperature|22.5|celsius|bedroom|*")
    assert res.slots[0].value == 22.5


def test_non_numeric_value_kept():
    # e.g. AC mode as a string value
    res = parse("set|air conditioner|mode|cool|*|bedroom|*")
    assert res.slots[0].device == "AC"
    assert res.slots[0].value == "Cool"  # normalized


def test_compound_multiline():
    text = "turnOff|light|*|*|*|kitchen|*\nadjustDown|curtain|openness|100|percent|*|*"
    res = parse(text)
    assert res.valid
    assert len(res.slots) == 2
    assert res.slots[0].action == "turnOff"
    assert res.slots[1].value == 100


def test_strips_think_block():
    res = parse("<think>the user wants light on</think>\nturnOn|light|*|*|*|hall|*")
    assert res.valid
    assert len(res.slots) == 1
    assert res.slots[0].room == "hall"


def test_invalid_action():
    res = parse("explode|light|*|*|*|kitchen|*")
    assert not res.valid
    assert not res.slots[0].valid
    assert any("invalid action" in e for e in res.slots[0].errors)


def test_wrong_field_count():
    res = parse("turnOn|light|kitchen")
    assert not res.valid
    assert any("expected 7 fields" in e for e in res.errors)


def test_empty_field_flagged():
    res = parse("turnOn|light||*|*|kitchen|1")  # empty attribute, not '*'
    assert not res.valid
    assert any("empty field" in e for e in res.slots[0].errors)


def test_empty_output_is_valid_empty():
    res = parse("")
    assert res.valid
    assert res.kind == "empty"
    assert res.slots == []


def test_non_control_passthrough():
    # questions / chit-chat have no pipes -> natural-language passthrough,
    # NOT a malformed control output
    res = parse("I'm sorry, I can't control the weather.")
    assert res.valid
    assert res.kind == "non_control"
    assert res.text == "I'm sorry, I can't control the weather."
    assert res.slots == []


def test_non_control_strips_think():
    res = parse("<think>user is just greeting</think>\nHello! How can I help?")
    assert res.kind == "non_control"
    assert res.text == "Hello! How can I help?"


def test_multi_intent_compound_light():
    # the 5-line single-device multi-attribute case
    text = (
        "turnOn|light|*|*|*|master bedroom|2\n"
        "set|light|brightness|80|percent|master bedroom|2\n"
        "set|light|colorTemperature|4000|kelvin|master bedroom|2\n"
        "set|light|color|blue|*|master bedroom|2\n"
        "set|light|mode|reading|*|master bedroom|2"
    )
    res = parse(text)
    assert res.kind == "control"
    assert res.valid
    assert len(res.slots) == 5
    assert res.slots[1].value == 80
    assert res.slots[2].attribute == "colorTemperature"
    assert res.slots[3].value == "Blue"  # normalized


def test_title_case_normalization():
    # COMMAND_SPEC.md mandates Title Case for devices/colors/units
    res = parse("turnOn|light|*|*|*|hall|1")
    assert res.slots[0].device == "Light"

    res = parse("set|spotlight|brightness|50|percent|*|*")
    assert res.slots[0].device == "Spot Light"  # spotlight -> Spot Light
    assert res.slots[0].unit == "Percent"

    res = parse("set|floor lamp|color|warm white|*|bedroom|*")
    assert res.slots[0].device == "Floor Lamp"
    assert res.slots[0].value == "Warm White"


def test_floor_string_warning():
    # floor as string (e.g. "Second Floor") triggers a warning, but is not hard-invalid
    res = parse("turnOn|Light|*|*|*|bedroom|Second Floor")
    assert res.kind == "control"
    # the segment itself gets an error about floor type
    assert not res.slots[0].valid
    assert any("floor is string" in e for e in res.slots[0].errors)


def test_partial_validity_in_compound():
    text = "turnOn|light|*|*|*|kitchen|*\nbogus|line"
    res = parse(text)
    assert not res.valid
    assert res.slots[0].valid
    assert not res.slots[1].valid


def test_json_roundtrip():
    res = parse("turnOn|light|*|*|*|living room|1")
    js = res.to_json()
    assert '"action": "turnOn"' in js
    assert '"floor": 1' in js


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
