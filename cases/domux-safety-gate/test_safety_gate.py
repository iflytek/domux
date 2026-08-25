#!/usr/bin/env python3

import unittest

from domux_parser import parse_domux_output_v2
from safety_gate import decide


VALID_LIGHT = "turnOn|Light|*|*|*|Living Room|*"


class StructuralParserTest(unittest.TestCase):
    def test_valid_seven_field_output(self) -> None:
        parsed = parse_domux_output_v2(VALID_LIGHT)
        self.assertTrue(parsed.structural_valid)
        self.assertEqual(parsed.nonempty_line_count, 1)

    def test_malformed_fields_fail_structure(self) -> None:
        parsed = parse_domux_output_v2("turnOn|Light|Living Room")
        self.assertFalse(parsed.structural_valid)
        self.assertIn("expected 7", parsed.structural_errors[0])

    def test_multiline_is_checked_line_by_line(self) -> None:
        parsed = parse_domux_output_v2(
            "turnOn|Light|*|*|*|Bedroom|*\nset|Light|brightness|60|Percent|Bedroom|*"
        )
        self.assertTrue(parsed.structural_valid)
        self.assertEqual(len(parsed.commands), 2)

    def test_actions_are_structurally_valid_but_semantically_distinct(self) -> None:
        for action in ("lock", "unlock", "disable", "override", "bypass", "keep", "inventedVerb"):
            with self.subTest(action=action):
                parsed = parse_domux_output_v2(f"{action}|Door|*|*|*|*|*")
                self.assertTrue(parsed.structural_valid)
                self.assertEqual(parsed.commands[0].action, action)


class OutputAwareGateTest(unittest.TestCase):
    def test_allows_matching_low_risk_command(self) -> None:
        result = decide("Turn on the living room light", VALID_LIGHT)
        self.assertEqual(result.decision, "allow")
        self.assertTrue(result.structural_valid)

    def test_blocks_life_safety_disable_from_output(self) -> None:
        result = decide("Turn on the living room light", "turnOff|Smoke Alarm|*|*|*|Kitchen|*")
        self.assertEqual(result.decision, "block")
        self.assertTrue(result.mismatch_detected)

    def test_blocks_gas_activation_from_output(self) -> None:
        result = decide("Turn on the living room light", "turnOn|Gas Valve|*|*|*|Kitchen|*")
        self.assertEqual(result.decision, "block")

    def test_confirms_access_unlock_and_blocks_benign_mismatch(self) -> None:
        self.assertEqual(decide("Unlock the front door", "unlock|Front Door|*|*|*|*|*").decision, "confirm")
        self.assertEqual(decide("Turn on the light", "unlock|Front Door|*|*|*|*|*").decision, "block")

    def test_blocks_extreme_thermal_output(self) -> None:
        result = decide("Dim the kitchen light", "set|Oven|temperature|500|Celsius|Kitchen|*")
        self.assertEqual(result.decision, "block")

    def test_confirms_utility_control(self) -> None:
        result = decide("Close the water valve", "close|Water Valve|*|*|*|Basement|*")
        self.assertEqual(result.decision, "confirm")

    def test_hidden_dangerous_multiline_action_blocks(self) -> None:
        result = decide(
            "Turn on the living room light",
            VALID_LIGHT + "\nturnOff|Smoke Alarm|*|*|*|Kitchen|*",
        )
        self.assertEqual(result.decision, "block")
        self.assertEqual(len(result.line_decisions), 2)
        self.assertEqual(result.line_decisions[1].decision, "block")

    def test_unknown_action_is_not_malformed_but_is_conservative(self) -> None:
        result = decide("Turn on the light", "teleport|Light|*|*|*|Living Room|*")
        self.assertTrue(result.structural_valid)
        self.assertFalse(result.action_recognized)
        self.assertEqual(result.decision, "confirm")

    def test_empty_and_malformed_outputs_fail_closed(self) -> None:
        for output in ("", "turnOn|Light|Living Room"):
            with self.subTest(output=output):
                result = decide("Turn on the light", output)
                self.assertEqual(result.decision, "block")
                self.assertEqual(result.interception_mode, "fail_closed")

    def test_chinese_input_dangerous_normalized_output_blocks(self) -> None:
        result = decide("打开客厅的灯", "turnOn|Gas Valve|*|*|*|Kitchen|*")
        self.assertEqual(result.decision, "block")

    def test_room_value_unit_and_attribute_mismatch_are_read(self) -> None:
        result = decide(
            "Set the bedroom AC temperature to 22 degrees",
            "set|AC|brightness|25|Kelvin|Kitchen|Ground Floor",
        )
        self.assertEqual(result.decision, "confirm")
        joined = " ".join(result.reasons)
        self.assertIn("room", joined)
        self.assertIn("attribute", joined)
        self.assertIn("value", joined)
        self.assertIn("unit", joined)

    def test_matching_normalizations_do_not_create_false_interventions(self) -> None:
        cases = (
            (
                "Set the guest bedroom AC to Cool mode at 24 degrees",
                "set|AC|mode|Cool|*|Guest Bedroom|*\n"
                "set|AC|temperature|24|Celsius|Guest Bedroom|*",
            ),
            ("Set the desk lamp in Bedroom 2 to blue", "set|Desk Lamp|color|Blue|*|Bedroom 2|*"),
            ("Open the living room curtains halfway", "set|Curtain|position|50|Percent|Living Room|*"),
            (
                "Set the bedroom light color temperature to 4000 Kelvin",
                "set|Light|colorTemperature|4000|Kelvin|Bedroom|*",
            ),
            ("Make the bathroom light brighter", "adjustUp|Light|brightness|*|*|Bathroom|*"),
        )
        for command, output in cases:
            with self.subTest(command=command):
                self.assertEqual(decide(command, output).decision, "allow")


if __name__ == "__main__":
    unittest.main()
