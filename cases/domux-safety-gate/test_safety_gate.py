#!/usr/bin/env python3

import unittest

from safety_gate import decide, parse_domux_output


VALID_LIGHT = "turnOn|Light|*|*|*|Living Room|*"


class SafetyGateTest(unittest.TestCase):
    def test_allows_low_risk_well_formed_command(self) -> None:
        result = decide("Turn on the living room light", VALID_LIGHT)
        self.assertEqual(result.decision, "allow")
        self.assertTrue(result.format_valid)

    def test_confirms_high_consequence_device(self) -> None:
        result = decide(
            "Unlock the front door",
            "turnOn|Front Door|*|*|*|Entrance Hall|Ground Floor",
        )
        self.assertEqual(result.decision, "confirm")

    def test_blocks_safety_alarm_disable(self) -> None:
        result = decide(
            "Disable the smoke alarm in the kitchen",
            "turnOff|Smoke Alarm|*|*|*|Kitchen|*",
        )
        self.assertEqual(result.decision, "block")

    def test_blocks_malformed_model_output(self) -> None:
        result = decide("Turn on the light", "turnOn|Light|Living Room")
        self.assertEqual(result.decision, "block")
        self.assertFalse(result.format_valid)

    def test_multiline_output_is_parsed(self) -> None:
        parsed, errors = parse_domux_output(
            "turnOn|Light|*|*|*|Bedroom|*\nset|Light|brightness|60|Percent|Bedroom|*"
        )
        self.assertEqual(len(parsed), 2)
        self.assertEqual(errors, [])

    def test_confirms_out_of_range_temperature(self) -> None:
        result = decide(
            "Set the bedroom AC to 34 degrees",
            "set|AC|temperature|34|Celsius|Bedroom|*",
        )
        self.assertEqual(result.decision, "confirm")


if __name__ == "__main__":
    unittest.main()
