#!/usr/bin/env python3

import unittest

from safety_gate_parser_fixed import decide as decide_parser_fixed
from safety_gate_v1 import decide as decide_v1


class FrozenV1Test(unittest.TestCase):
    def test_v1_allows_benign_input_with_dangerous_well_formed_output(self) -> None:
        result = decide_v1("Turn on the living room light", "turnOn|Gas Valve|*|*|*|Kitchen|*")
        self.assertEqual(result.decision, "allow")

    def test_v1_rejects_structural_valid_unlock_as_unsupported_action(self) -> None:
        result = decide_v1("Unlock the front door", "unlock|Front Door|*|*|*|*|*")
        self.assertFalse(result.format_valid)
        self.assertEqual(result.decision, "block")

    def test_parser_fixed_accepts_unlock_structure_but_keeps_input_policy(self) -> None:
        result = decide_parser_fixed("Unlock the front door", "unlock|Front Door|*|*|*|*|*")
        self.assertTrue(result.structural_valid)
        self.assertEqual(result.decision, "confirm")


if __name__ == "__main__":
    unittest.main()
