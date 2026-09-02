from __future__ import annotations

import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


CASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE_DIR))

from clarify_commit import (  # noqa: E402
    AdapterError,
    ClarifyPrepareStore,
    DomuxInstruction,
    EntityRegistry,
    EntitySpec,
    GroundingError,
    HomeAssistantRESTAdapter,
    InMemoryHAAdapter,
    MAX_UTTERANCE_CHARS,
    ParseError,
    PreparedActionStore,
    ServiceCallError,
    ServiceCallResult,
    SessionContext,
    altered_confirmation,
    build_plan,
    clarification_for,
    controlled_projection,
    ground_domux_request,
    parse_domux_output,
    planning_projection,
    projection_matches,
    resolve_clarification,
    resolve_clarification_submission,
    resolve_unique_request,
)


def fixture() -> tuple[EntityRegistry, dict[str, dict[str, object]]]:
    entities = (
        EntitySpec("light.living_ceiling", "light", "Ceiling Light", "Living Room", "Ground Floor"),
        EntitySpec("light.study_ceiling", "light", "Ceiling Light", "Study", "Ground Floor"),
        EntitySpec("light.utility", "light", "Utility Light", "Utility Room", "Ground Floor"),
        EntitySpec("cover.study_curtain", "cover", "Curtain", "Study", "Ground Floor"),
        EntitySpec("climate.study_ac", "climate", "AC", "Study", "Ground Floor"),
    )
    states = {
        "light.living_ceiling": {
            "entity_id": "light.living_ceiling", "state": "on", "attributes": {
                "brightness": 204, "supported_color_modes": ["brightness", "color_temp", "rgb"],
                "min_color_temp_kelvin": 3000, "max_color_temp_kelvin": 6500,
            },
        },
        "light.study_ceiling": {
            "entity_id": "light.study_ceiling", "state": "on", "attributes": {
                "brightness": 153, "supported_color_modes": ["brightness", "color_temp", "rgb"],
                "min_color_temp_kelvin": 3000, "max_color_temp_kelvin": 6500,
            },
        },
        "light.utility": {
            "entity_id": "light.utility", "state": "off", "attributes": {
                "brightness": 0, "supported_color_modes": ["brightness", "color_temp", "rgb"],
                "min_color_temp_kelvin": 3000, "max_color_temp_kelvin": 6500,
            },
        },
        "cover.study_curtain": {
            "entity_id": "cover.study_curtain", "state": "open",
            "attributes": {"current_position": 80, "supported_features": 7},
        },
        "climate.study_ac": {
            "entity_id": "climate.study_ac", "state": "cool",
            "attributes": {
                "temperature": 24.0, "fan_mode": "medium",
                "hvac_modes": ["off", "cool", "heat", "dry", "fan_only", "auto"],
                "fan_modes": ["low", "medium", "medium_high", "high"],
                "supported_features": 9, "temperature_unit": "°C",
                "min_temp": 16.0, "max_temp": 30.0, "target_temp_step": 0.5,
            },
        },
    }
    return EntityRegistry(entities), states


class MutableClock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class ParserTests(unittest.TestCase):
    def test_single_and_multiple_instructions(self) -> None:
        one = parse_domux_output("turnOff|Ceiling Light|*|*|*|Study|Ground Floor")
        self.assertEqual(one[0].room, "Study")
        many = parse_domux_output(
            "turnOn|Light|*|*|*|Study|*\nset|Light|brightness|50|Percent|Study|*"
        )
        self.assertEqual(len(many), 2)

    def test_ampersand_is_an_explicit_multi_instruction_separator(self) -> None:
        parsed = parse_domux_output(
            "turnOn|Light|*|*|*|Study|*&turnOff|Light|*|*|*|Bedroom|*"
        )
        self.assertEqual(len(parsed), 2)

    def test_malformed_raw_outputs_fail_closed(self) -> None:
        invalid = ("", "hello", "turnOn|Light", "*|Light|*|*|*|Study|*", "turnOn||*|*|*|Study|*")
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ParseError):
                parse_domux_output(raw)

    def test_raw_text_is_not_rewritten(self) -> None:
        raw = " turnOff | Ceiling Light | * | * | * | Study | Ground Floor "
        parsed = parse_domux_output(raw)
        self.assertEqual(parsed[0].device, "Ceiling Light")
        self.assertEqual(raw, " turnOff | Ceiling Light | * | * | * | Study | Ground Floor ")


class GroundingTests(unittest.TestCase):
    def test_oversized_utterance_is_rejected_before_grounding(self) -> None:
        registry, _states = fixture()
        utterance = "Turn off the Study Ceiling Light. " + (
            "x" * MAX_UTTERANCE_CHARS
        )
        with self.assertRaisesRegex(GroundingError, "exceeds the supported length"):
            ground_domux_request(
                utterance,
                "turnOff|Ceiling Light|*|*|*|Study|Ground Floor",
                registry,
            )

    def setUp(self) -> None:
        self.registry, self.states = fixture()

    def test_explicit_room_is_unique(self) -> None:
        instruction = parse_domux_output("turnOff|Ceiling Light|*|*|*|Study|Ground Floor")[0]
        candidates = self.registry.candidates(instruction)
        self.assertEqual([item.entity_id for item in candidates], ["light.study_ceiling"])
        self.assertFalse(clarification_for(candidates).required)

    def test_omitted_room_is_ambiguous_and_ordered(self) -> None:
        instruction = parse_domux_output("turnOff|Ceiling Light|*|*|*|*|*")[0]
        candidates = self.registry.candidates(instruction)
        self.assertEqual(
            [item.entity_id for item in candidates],
            ["light.living_ceiling", "light.study_ceiling"],
        )
        prompt = clarification_for(candidates)
        self.assertTrue(prompt.required)
        self.assertLessEqual(len(prompt.candidates), 3)
        self.assertEqual(resolve_clarification("Study", prompt.candidates).entity_id, "light.study_ceiling")

    def test_duplicate_human_labels_remain_visibly_distinguishable(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study_a", "light", "Ceiling Light", "Study", "Ground Floor",
                ("North circuit",),
            ),
            EntitySpec(
                "light.study_b", "light", "Ceiling Light", "Study", "Ground Floor",
                ("South circuit",),
            ),
        ))
        grounded = ground_domux_request(
            "Turn off the ceiling light.",
            "turnOff|Ceiling Light|*|*|*|*|*",
            registry,
        )
        prompt = grounded.clarification.prompt
        self.assertIsNotNone(prompt)
        self.assertIn("alias: North circuit", prompt)
        self.assertIn("alias: South circuit", prompt)
        self.assertIn("id: light.study_a", prompt)
        self.assertIn("id: light.study_b", prompt)
        self.assertEqual(
            resolve_clarification("light.study_b", grounded.candidates).entity_id,
            "light.study_b",
        )

    def test_context_limits_pronoun_candidates_but_does_not_guess(self) -> None:
        instruction = parse_domux_output("turnOff|*|*|*|*|*|*")[0]
        context = SessionContext(("light.living_ceiling", "light.study_ceiling"))
        candidates = self.registry.candidates(instruction, context)
        self.assertEqual(len(candidates), 2)
        self.assertTrue(clarification_for(candidates).required)

    def test_directionless_adjust_never_authorizes_a_model_direction(self) -> None:
        cases = (
            (
                "Adjust the Study curtain by 10 percent.",
                "adjustUp|Curtain|position|10|Percent|Study|Ground Floor",
            ),
            (
                "Adjust the Study light by 10 percent.",
                "adjustDown|Light|brightness|10|Percent|Study|Ground Floor",
            ),
            (
                "Adjust the Study AC by 2 degrees.",
                "adjustUp|AC|temperature|2|Celsius|Study|Ground Floor",
            ),
        )
        for utterance, raw_output in cases:
            with self.subTest(raw_output=raw_output):
                grounded = ground_domux_request(utterance, raw_output, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn("action", grounded.clarification.unresolved_slots)

    def test_adjust_direction_words_are_operation_scoped(self) -> None:
        registry = EntityRegistry((
            EntitySpec("climate.lower", "climate", "AC", "Landing", "Lower Floor"),
        ))
        rejected = (
            (
                "Adjust the Lower Floor AC.",
                "adjustDown|AC|temperature|*|*|*|Lower Floor",
            ),
            (
                "Set the Lower Floor AC to Cool mode.",
                "adjustDown|AC|temperature|*|*|*|Lower Floor",
            ),
            (
                "Make the Lower Floor AC cooler mode.",
                "adjustDown|AC|temperature|*|*|*|Lower Floor",
            ),
            (
                "Raise the Lower Floor AC by 2 degrees.",
                "adjustDown|AC|temperature|2|Celsius|*|Lower Floor",
            ),
            (
                "Lower the Lower Floor AC by 2 degrees.",
                "adjustUp|AC|temperature|2|Celsius|*|Lower Floor",
            ),
            (
                "Make the Lower Floor AC warmer or cooler.",
                "adjustUp|AC|temperature|*|*|*|Lower Floor",
            ),
        )
        for utterance, raw_output in rejected:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw_output, registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn("action", grounded.clarification.unresolved_slots)

        accepted = (
            (
                "Make the Lower Floor AC warmer.",
                "adjustUp|AC|temperature|*|*|*|Lower Floor",
            ),
            (
                "Make the Lower Floor AC cooler.",
                "adjustDown|AC|temperature|*|*|*|Lower Floor",
            ),
        )
        for utterance, raw_output in accepted:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw_output, registry)
                self.assertNotIn("action", grounded.clarification.unresolved_slots)

    def test_unregistered_floor_is_unresolved_not_an_ontology_guess(self) -> None:
        registry = EntityRegistry((
            EntitySpec("climate.landing", "climate", "AC", "Landing", "First Floor"),
            EntitySpec("climate.loft", "climate", "AC", "Loft", "Second Floor"),
        ))
        grounded = ground_domux_request(
            "Set the upstairs AC to 21 degrees.",
            "set|AC|temperature|21|Celsius|*|Upstairs",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("climate.landing", "climate.loft"),
        )
        self.assertTrue(grounded.clarification.required)
        self.assertIn("floor", grounded.clarification.unresolved_slots)
        resolved = resolve_clarification_submission(
            grounded,
            answer="Use 21 Celsius for the Landing AC on the First Floor.",
            confirmed_instruction=DomuxInstruction(
                "set", "AC", "temperature", "21", "Celsius", "Landing", "First Floor"
            ),
            registry=registry,
        )
        self.assertEqual(resolved.chosen.entity_id, "climate.landing")

        singleton = EntityRegistry((
            EntitySpec("climate.only", "climate", "AC", "Landing", "First Floor"),
        ))
        singleton_grounded = ground_domux_request(
            "Set the upstairs AC to 21 degrees.",
            "set|AC|temperature|21|Celsius|*|Upstairs",
            singleton,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in singleton_grounded.candidates),
            ("climate.only",),
        )
        self.assertTrue(singleton_grounded.clarification.required)
        self.assertIn("floor", singleton_grounded.clarification.unresolved_slots)

        omitted_by_model = ground_domux_request(
            "Set the upstairs AC to 21 degrees.",
            "set|AC|temperature|21|Celsius|*|*",
            singleton,
        )
        self.assertTrue(omitted_by_model.clarification.required)
        self.assertIn("device", omitted_by_model.clarification.unresolved_slots)
        with self.assertRaisesRegex(GroundingError, "explicitly identify"):
            resolve_clarification_submission(
                omitted_by_model,
                answer="Yes.",
                confirmed_instruction=DomuxInstruction(
                    "set", "AC", "temperature", "21", "Celsius", "Landing", "First Floor"
                ),
                registry=singleton,
            )

    def test_unregistered_compound_device_falls_back_only_to_clarification(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.dining", "light", "Light", "Dining Room", "Ground Floor"),
            EntitySpec("cover.dining", "cover", "Curtain", "Dining Room", "Ground Floor"),
        ))
        grounded = ground_domux_request(
            "Make the dining light Blue—no, perhaps Green; confirm the color.",
            "set|Dining Light|color|Green|*|Dining Room|*",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("light.dining",),
        )
        self.assertTrue(grounded.clarification.required)
        self.assertIn("device", grounded.clarification.unresolved_slots)
        resolved = resolve_clarification_submission(
            grounded,
            answer="Confirm Green for the Dining Room light.",
            confirmed_instruction=DomuxInstruction(
                "set", "Light", "color", "Green", "*", "Dining Room", "Ground Floor"
            ),
            registry=registry,
        )
        self.assertEqual(resolved.chosen.entity_id, "light.dining")

    def test_partial_room_token_with_generic_device_never_becomes_canonical(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.dining", "light", "Light", "Dining Room", "Ground Floor"),
            EntitySpec("cover.dining", "cover", "Curtain", "Dining Room", "Ground Floor"),
        ))
        for raw_output in (
            "set|Dining Light|color|Green|*|Dining Room|*",
            "set|Light|color|Green|*|*|*",
            "set|Light|color|Green|*|Dining Room|*",
        ):
            with self.subTest(raw_output=raw_output):
                grounded = ground_domux_request(
                    "Set the dining light to Green.",
                    raw_output,
                    registry,
                )
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    ("light.dining",),
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("room", grounded.clarification.unresolved_slots)

        canonical = ground_domux_request(
            "Set the Dining Room light to Green.",
            "set|Light|color|Green|*|Dining Room|*",
            registry,
        )
        self.assertFalse(canonical.clarification.required)

        registered_compound = EntityRegistry((
            EntitySpec("light.dining_named", "light", "Dining Light", "Atrium", "Ground Floor"),
        ))
        compound = ground_domux_request(
            "Set the Dining Light in the Atrium to Green.",
            "set|Dining Light|color|Green|*|Atrium|*",
            registered_compound,
        )
        self.assertFalse(compound.clarification.required)

        overlapping_rooms = EntityRegistry((
            EntitySpec("light.reading_room", "light", "Light", "Reading Room", "First Floor"),
            EntitySpec("light.reading_nook", "light", "Light", "Reading Nook", "First Floor"),
        ))
        exact_overlap = ground_domux_request(
            "Turn off the Reading Room light.",
            "turnOff|Light|*|*|*|Reading Room|*",
            overlapping_rooms,
        )
        self.assertFalse(exact_overlap.clarification.required)

    def test_deictic_context_precedes_a_model_only_domain_guess(self) -> None:
        context = SessionContext(("light.living_ceiling", "light.study_ceiling"))
        grounded = ground_domux_request(
            "Make that one warmer.",
            "adjustUp|AC|temperature|*|*|*|*",
            self.registry,
            context,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("light.living_ceiling", "light.study_ceiling"),
        )
        self.assertTrue(grounded.clarification.required)
        resolved = resolve_clarification_submission(
            grounded,
            answer="Set the Living Room light to 3000 Kelvin.",
            confirmed_instruction=DomuxInstruction(
                "set", "Light", "colorTemperature", "3000", "Kelvin",
                "Living Room", "Ground Floor",
            ),
            registry=self.registry,
        )
        self.assertEqual(resolved.chosen.entity_id, "light.living_ceiling")

        wrong_room = ground_domux_request(
            "Turn it off.",
            "turnOff|AC|*|*|*|Utility Room|Ground Floor",
            self.registry,
            context,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in wrong_room.candidates),
            ("light.living_ceiling", "light.study_ceiling"),
        )
        self.assertNotIn("climate.study_ac", {
            entity.entity_id for entity in wrong_room.candidates
        })
        self.assertTrue(wrong_room.clarification.required)

    def test_deictic_context_can_be_narrowed_by_an_explicit_user_domain(self) -> None:
        context = SessionContext(("light.study_ceiling", "cover.study_curtain"))
        grounded = ground_domux_request(
            "Turn that light off.",
            "turnOff|Light|*|*|*|*|*",
            self.registry,
            context,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("light.study_ceiling",),
        )
        self.assertFalse(grounded.clarification.required)

        wrong_model_domain = ground_domux_request(
            "Turn that light off.",
            "turnOff|AC|*|*|*|*|*",
            self.registry,
            context,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in wrong_model_domain.candidates),
            ("light.study_ceiling",),
        )
        self.assertTrue(wrong_model_domain.clarification.required)

        no_context_match = ground_domux_request(
            "Turn that light off.",
            "turnOff|Light|*|*|*|*|*",
            self.registry,
            SessionContext(("cover.study_curtain",)),
        )
        self.assertEqual(no_context_match.candidates, ())
        self.assertTrue(no_context_match.clarification.required)

    def test_stale_deictic_context_never_falls_back_to_a_global_singleton(self) -> None:
        context = SessionContext(("missing.old_entity", "light.study_ceiling"))
        grounded = ground_domux_request(
            "Turn it off.",
            "turnOff|*|*|*|*|*|*",
            self.registry,
            context,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("light.study_ceiling",),
        )
        self.assertTrue(grounded.clarification.required)
        self.assertIn("stale_context_reference", grounded.clarification.reasons)
        self.assertIn("context", grounded.clarification.unresolved_slots)

        all_stale = ground_domux_request(
            "Turn it off.",
            "turnOff|*|*|*|*|*|*",
            self.registry,
            SessionContext(("missing.old_entity",)),
        )
        self.assertEqual(all_stale.candidates, ())
        self.assertTrue(all_stale.clarification.required)

    def test_deictic_request_without_context_requires_an_explicit_target(self) -> None:
        singleton = EntityRegistry((
            EntitySpec("light.only", "light", "Light", "Study", "Ground Floor"),
        ))
        unresolved = ground_domux_request(
            "Turn it off.",
            "turnOff|*|*|*|*|*|*",
            singleton,
        )
        self.assertTrue(unresolved.clarification.required)
        self.assertIn("unsupported_request_grammar", unresolved.clarification.reasons)
        self.assertIn("authorization", unresolved.clarification.unresolved_slots)

        explicit = ground_domux_request(
            "Turn that Study light off.",
            "turnOff|Light|*|*|*|Study|*",
            singleton,
        )
        self.assertFalse(explicit.clarification.required)

    def test_explicit_selector_zero_match_never_falls_back_to_model_or_context(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec("cover.kitchen", "cover", "Curtain", "Kitchen", "Ground Floor"),
        ))
        for raw_output in (
            "turnOff|Light|*|*|*|*|*",
            "turnOff|Light|*|*|*|Kitchen|*",
            "turnOff|Light|*|*|*|Study|*",
        ):
            with self.subTest(raw_output=raw_output):
                grounded = ground_domux_request(
                    "Turn off the Kitchen light.",
                    raw_output,
                    registry,
                )
                self.assertEqual(grounded.candidates, ())
                self.assertTrue(grounded.clarification.required)

        contextual = ground_domux_request(
            "Turn that Kitchen light off.",
            "turnOff|Light|*|*|*|Kitchen|*",
            registry,
            SessionContext(("light.study",)),
        )
        self.assertEqual(contextual.candidates, ())
        self.assertTrue(contextual.clarification.required)

        with_kitchen_light = EntityRegistry((
            *registry.entities,
            EntitySpec("light.kitchen", "light", "Light", "Kitchen", "Ground Floor"),
        ))
        positive = ground_domux_request(
            "Turn off the Kitchen light.",
            "turnOff|Light|*|*|*|*|*",
            with_kitchen_light,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in positive.candidates),
            ("light.kitchen",),
        )
        self.assertFalse(positive.clarification.required)

    def test_same_span_room_and_alias_interpretations_are_not_anded(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study_a", "light", "Light", "Study", "Ground Floor"),
            EntitySpec(
                "light.study_b", "light", "Light", "Study", "Ground Floor", ("Study",),
            ),
        ))
        grounded = ground_domux_request(
            "Turn off the Study light.",
            "turnOff|Light|*|*|*|*|*",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("light.study_a", "light.study_b"),
        )
        self.assertTrue(grounded.clarification.required)

    def test_overlapping_room_and_specific_device_interpretations_remain_ambiguous(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec("light.named", "light", "Study Light", "Atrium", "Ground Floor"),
        ))
        grounded = ground_domux_request(
            "Turn off the Study light.",
            "turnOff|Light|*|*|*|*|*",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("light.named", "light.study"),
        )
        self.assertTrue(grounded.clarification.required)

    def test_inventory_words_in_ordinary_prose_are_not_target_selectors(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.right", "light", "Light", "Right", "Ground Floor"),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        grounded = ground_domux_request(
            "Turn the light off right now.",
            "turnOff|Light|*|*|*|*|*",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("light.right", "light.study"),
        )
        contextual = ground_domux_request(
            "Turn that light off right now.",
            "turnOff|Light|*|*|*|*|*",
            registry,
            SessionContext(("light.right", "light.study")),
        )
        self.assertEqual(len(contextual.candidates), 2)

        alias_registry = EntityRegistry((
            EntitySpec(
                "light.alias", "light", "Light", "Living Room", "Ground Floor", ("Right",),
            ),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        alias_collision = ground_domux_request(
            "Turn the light off right now.",
            "turnOff|Light|*|*|*|*|*",
            alias_registry,
        )
        self.assertEqual(len(alias_collision.candidates), 2)

    def test_relational_room_phrases_never_fall_back_to_a_global_singleton(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec("cover.kitchen", "cover", "Curtain", "Kitchen", "Ground Floor"),
        ))
        for phrase in ("my room", "the right room", "any room"):
            with self.subTest(phrase=phrase):
                grounded = ground_domux_request(
                    f"Turn off the light in {phrase}.",
                    "turnOff|Light|*|*|*|*|*",
                    registry,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("room", grounded.clarification.unresolved_slots)
                with self.assertRaisesRegex(GroundingError, "repaired target"):
                    resolve_clarification_submission(
                        grounded,
                        answer="Yes.",
                        confirmed_instruction=DomuxInstruction(
                            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
                        ),
                        registry=registry,
                    )

        explicit = ground_domux_request(
            "Turn off the light in Study.",
            "turnOff|Light|*|*|*|Study|*",
            registry,
        )
        self.assertFalse(explicit.clarification.required)

    def test_partial_selector_detection_is_limited_to_selector_positions(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "First Floor"),
        ))
        sequence_word = ground_domux_request(
            "First, turn off the Study light on the First Floor.",
            "turnOff|Light|*|*|*|Study|First Floor",
            registry,
        )
        self.assertNotIn("floor", sequence_word.clarification.unresolved_slots)

        lower_registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Lower Floor"),
        ))
        operation_word = ground_domux_request(
            "Lower the Study light brightness.",
            "adjustDown|Light|brightness|*|*|Study|*",
            lower_registry,
        )
        self.assertNotIn("floor", operation_word.clarification.unresolved_slots)

    def test_context_selector_is_a_true_intersection(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec("light.kitchen", "light", "Light", "Kitchen", "Ground Floor"),
        ))
        only_study = ground_domux_request(
            "Turn that Kitchen light off.",
            "turnOff|Light|*|*|*|*|*",
            registry,
            SessionContext(("light.study",)),
        )
        self.assertEqual(only_study.candidates, ())

        both = ground_domux_request(
            "Turn that Kitchen light off.",
            "turnOff|Light|*|*|*|*|*",
            registry,
            SessionContext(("light.study", "light.kitchen")),
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in both.candidates),
            ("light.kitchen",),
        )

        stale = ground_domux_request(
            "Turn that Kitchen light off.",
            "turnOff|Light|*|*|*|*|*",
            registry,
            SessionContext(("missing.kitchen", "light.study")),
        )
        self.assertEqual(stale.candidates, ())
        self.assertIn("stale_context_reference", stale.clarification.reasons)

    def test_other_reference_never_collapses_to_a_single_context_target(self) -> None:
        no_context = ground_domux_request(
            "Turn the other light off.",
            "turnOff|Light|*|*|*|*|*",
            self.registry,
        )
        self.assertEqual(no_context.candidates, ())
        self.assertIn("unsupported_request_grammar", no_context.clarification.reasons)

        one_context = ground_domux_request(
            "Turn the other light off.",
            "turnOff|Light|*|*|*|*|*",
            self.registry,
            SessionContext(("light.study_ceiling",)),
        )
        self.assertEqual(one_context.candidates, ())

        heterogeneous = ground_domux_request(
            "Turn the other light off.",
            "turnOff|Light|*|*|*|Study|*",
            self.registry,
            SessionContext(("light.study_ceiling", "cover.study_curtain")),
        )
        self.assertEqual(heterogeneous.candidates, ())

        two_lights = ground_domux_request(
            "Turn the other light off.",
            "turnOff|Light|*|*|*|Study|*",
            self.registry,
            SessionContext(("light.living_ceiling", "light.study_ceiling")),
        )
        self.assertEqual(len(two_lights.candidates), 2)
        self.assertIn("other_reference_requires_selection", two_lights.clarification.reasons)
        with self.assertRaises(GroundingError):
            resolve_clarification_submission(
                two_lights,
                answer="Yes.",
                confirmed_instruction=DomuxInstruction(
                    "turnOff", "Ceiling Light", "*", "*", "*", "Study", "Ground Floor"
                ),
                registry=self.registry,
            )
        selected = resolve_clarification_submission(
            two_lights,
            answer="2",
            confirmed_instruction=DomuxInstruction(
                "turnOff", "Ceiling Light", "*", "*", "*", "Study", "Ground Floor"
            ),
            registry=self.registry,
        )
        self.assertEqual(selected.chosen.entity_id, "light.study_ceiling")

    def test_multi_token_selector_spans_do_not_hide_a_second_partial_selector(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.dining", "light", "Light", "Dining Room", "Ground Floor"),
            EntitySpec("cover.study", "cover", "Curtain", "Study", "Ground Floor"),
        ))
        contradictory = ground_domux_request(
            "Set the dining light in Study to Green.",
            "set|Light|color|Green|*|*|*",
            registry,
        )
        self.assertEqual(contradictory.candidates, ())
        self.assertIn("room", contradictory.clarification.unresolved_slots)

        exact = ground_domux_request(
            "Set the Dining Room light to Green.",
            "set|Light|color|Green|*|Dining Room|*",
            registry,
        )
        self.assertFalse(exact.clarification.required)

        named_device = EntityRegistry((
            EntitySpec("light.named", "light", "Dining Light", "Atrium", "Ground Floor"),
        ))
        complete_device = ground_domux_request(
            "Set the Dining Light in the Atrium to Green.",
            "set|Dining Light|color|Green|*|Atrium|*",
            named_device,
        )
        self.assertFalse(complete_device.clarification.required)

    def test_negated_relative_directions_cannot_authorize_adjustment(self) -> None:
        cases = (
            ("Do not increase the Study light brightness.", "adjustUp"),
            ("Don't decrease the Study light brightness.", "adjustDown"),
            ("Never make the Study light brighter.", "adjustUp"),
            ("No increase for the Study light brightness.", "adjustUp"),
            ("Make the Study AC not warmer.", "adjustUp"),
            ("No dimmer for the Study light.", "adjustDown"),
        )
        for utterance, action in cases:
            raw_output = (
                f"{action}|AC|temperature|*|*|Study|*"
                if "AC" in utterance
                else f"{action}|Light|brightness|*|*|Study|*"
            )
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw_output, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn("negative_or_cancelled_intent", grounded.clarification.reasons)
                self.assertIn("action", grounded.clarification.unresolved_slots)

        for utterance, action in (
            ("Make the Study light not any brighter.", "adjustUp"),
            ("Make the Study light not any dimmer.", "adjustDown"),
            ("Make the Study light anything but brighter.", "adjustUp"),
            ("Make the Study light other than dimmer.", "adjustDown"),
            ("I don't want the Study light brighter.", "adjustUp"),
            ("No need for a brighter Study light.", "adjustUp"),
            ("No need for a dimmer Study light.", "adjustDown"),
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    f"{action}|Light|brightness|*|*|Study|*",
                    self.registry,
                )
                self.assertIn("negative_or_cancelled_intent", grounded.clarification.reasons)
                with self.assertRaisesRegex(GroundingError, "negated"):
                    resolve_clarification_submission(
                        grounded,
                        answer="Confirm the Study light on the Ground Floor.",
                        confirmed_instruction=DomuxInstruction(
                            action, "Light", "brightness", "*", "*", "Study", "Ground Floor"
                        ),
                        registry=self.registry,
                    )

    def test_negative_selector_spans_never_become_positive_target_constraints(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.living", "light", "Light", "Living Room", "Ground Floor"),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        for utterance in (
            "Turn off the light, but not the light in Study.",
            "Turn off the light, but not any one in Study.",
            "Turn off the light, but not a light in Study.",
            "Turn off the light, anything but a light in Study.",
            "Turn off a light, but don't use the light in Study.",
            "Turn off a light, but do not use my device in Study.",
            "Turn off every light but Study.",
            "Turn off every light, but Study.",
            "Turn off every light (but Study).",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|*|*",
                    registry,
                )
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    ("light.living", "light.study"),
                )
                self.assertIn("light.study", grounded.negated_entity_ids)
                self.assertTrue(grounded.clarification.required)
                with self.assertRaises(GroundingError):
                    resolve_clarification_submission(
                        grounded,
                        answer="Yes.",
                        confirmed_instruction=DomuxInstruction(
                            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
                        ),
                        registry=registry,
                    )

    def test_punctuated_and_anaphoric_negative_selectors_fail_closed(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        confirmed = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        for utterance in (
            'Turn off the light, but not "Study Lamp".',
            "Turn off the light, but not (Study Lamp).",
            "Turn off the light, but not: Study Lamp.",
            "Turn off the light, but not the light that I mean, the one in Study.",
            "Turn off the light, but do not use the lamp I mean, namely Study Lamp.",
            "Turn off everything but Study Lamp.",
            "Turn off every light but Study Lamp.",
            "Turn off every light, but Study Lamp.",
            "Turn off every light (but Study Lamp).",
            "Turn off all lights but Study Lamp.",
            "Turn off all the lights but Study Lamp.",
            "Turn off all the lights, but Study Lamp.",
            "Turn off any light but Study Lamp.",
            "Turn off any one light but Study Lamp.",
            "Turn off any of the lights but Study Lamp.",
            "Turn off either light but Study Lamp.",
            "Turn off each light but Study Lamp.",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|*|*",
                    registry,
                )
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    ("light.living", "light.study"),
                )
                self.assertEqual(grounded.negated_entity_ids, ("light.study",))
                self.assertTrue(grounded.clarification.required)
                with self.assertRaises(GroundingError):
                    resolve_unique_request(grounded, registry)

                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    ttl_seconds=30,
                    clock=MutableClock(),
                    nonce_factory=lambda: "negative-selector-nonce",
                )
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Yes.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        for utterance in (
            "Turn off nothing but Study Lamp.",
            "Turn off no light but Study Lamp.",
        ):
            with self.subTest(only_target=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertEqual(grounded.negated_entity_ids, ())
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    ("light.study",),
                )

        corrected = ground_domux_request(
            "Turn off not Study Lamp but Living Lamp.",
            "turnOff|Light|*|*|*|Living Room|Ground Floor",
            registry,
        )
        self.assertEqual(corrected.negated_entity_ids, ("light.study",))
        resolved = resolve_clarification_submission(
            corrected,
            answer="Not Study Lamp; use Living Lamp.",
            confirmed_instruction=DomuxInstruction(
                "turnOff", "Light", "*", "*", "*", "Living Room", "Ground Floor"
            ),
            registry=registry,
        )
        self.assertEqual(resolved.chosen.entity_id, "light.living")
        adapter = InMemoryHAAdapter(states)
        store = PreparedActionStore(
            ttl_seconds=30,
            clock=MutableClock(),
            nonce_factory=lambda: "positive-contrast-nonce",
        )
        action = store.prepare(
            actor_id="actor-a",
            session_id="session-a",
            grounded=corrected,
            registry=registry,
            adapter=adapter,
            clarification_answer="Not Study Lamp; use Living Lamp.",
            confirmed_instruction=DomuxInstruction(
                "turnOff", "Light", "*", "*", "*", "Living Room", "Ground Floor"
            ),
        )
        result = store.commit(action.confirmation(), registry=registry, adapter=adapter)
        self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
        self.assertEqual(adapter.sut_calls[0]["data"]["entity_id"], "light.living")

    def test_negative_selector_scope_resets_only_at_explicit_boundaries(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
            EntitySpec(
                "light.utility", "light", "Light", "Utility Room", "Ground Floor",
                aliases=("Utility Lamp",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        confirmed_living = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Living Room", "Ground Floor"
        )
        for utterance in (
            "Do not use Study Lamp. Turn off Living Lamp.",
            "Do not use Study Lamp; turn off Living Lamp.",
            "Do not use Study Lamp, then turn off Living Lamp.",
            "Do not use Study Lamp, turn off Living Lamp.",
            "Do not use Study Lamp and then turn off Living Lamp.",
            "Do not use Study Lamp: turn off Living Lamp.",
            "Do not use Study Lamp—turn off Living Lamp.",
            "Do not use, choose Study Lamp, then turn off Living Lamp.",
            "Do not use, choose Study Lamp, turn off Living Lamp.",
            "Do not use, choose Study Lamp: turn off Living Lamp.",
            "Turn off not Study Lamp: use Living Lamp.",
            "Turn off not Study Lamp—use Living Lamp.",
        ):
            with self.subTest(positive_restart=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Living Room|Ground Floor",
                    registry,
                )
                self.assertEqual(grounded.negated_entity_ids, ("light.study",))
                self.assertNotIn(
                    "light.living",
                    grounded.negated_entity_ids,
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    ttl_seconds=30,
                    clock=MutableClock(),
                    nonce_factory=lambda: "explicit-boundary-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer="Use Living Lamp.",
                    confirmed_instruction=confirmed_living,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
                self.assertEqual(
                    adapter.sut_calls[0]["data"]["entity_id"],
                    "light.living",
                )

        for utterance in (
            "Turn off any light but use Living Lamp.",
            "Turn off any light, but use Living Lamp.",
        ):
            with self.subTest(direct_positive_restart=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Living Room|Ground Floor",
                    registry,
                )
                self.assertNotIn("light.living", grounded.negated_entity_ids)
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    ("light.living",),
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    ttl_seconds=30,
                    clock=MutableClock(),
                    nonce_factory=lambda: "direct-restart-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer="Use Living Lamp.",
                    confirmed_instruction=confirmed_living,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
                self.assertEqual(
                    adapter.sut_calls[0]["data"]["entity_id"],
                    "light.living",
                )

        excluded_pair = ground_domux_request(
            "Turn off Utility Lamp, but not Study Lamp or Living Lamp.",
            "turnOff|Light|*|*|*|Utility Room|Ground Floor",
            registry,
        )
        self.assertEqual(
            excluded_pair.negated_entity_ids,
            ("light.living", "light.study"),
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in excluded_pair.candidates),
            ("light.utility",),
        )

        plain_and = ground_domux_request(
            "Do not use Study Lamp and turn off Living Lamp.",
            "turnOff|Light|*|*|*|Living Room|Ground Floor",
            registry,
        )
        self.assertEqual(
            plain_and.negated_entity_ids,
            ("light.living", "light.study"),
        )
        adapter = InMemoryHAAdapter(states)
        store = PreparedActionStore(
            ttl_seconds=30,
            clock=MutableClock(),
            nonce_factory=lambda: "plain-and-negation-nonce",
        )
        with self.assertRaises(GroundingError):
            store.prepare(
                actor_id="actor-a",
                session_id="session-a",
                grounded=plain_and,
                registry=registry,
                adapter=adapter,
                clarification_answer="Use Living Lamp.",
                confirmed_instruction=confirmed_living,
            )
        self.assertEqual(adapter.sut_calls, [])

    def test_entity_id_periods_do_not_terminate_negative_scope(self) -> None:
        registry = EntityRegistry((
            EntitySpec("climate.study", "climate", "AC", "Study", "Ground Floor"),
            EntitySpec(
                "climate.living", "climate", "AC", "Living Room", "Ground Floor"
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "cool",
                "attributes": {
                    "temperature": 24.0,
                    "hvac_modes": ["off", "cool"],
                    "fan_modes": ["low", "medium", "high"],
                    "supported_features": 1,
                    "temperature_unit": "°C",
                    "min_temp": 16.0,
                    "max_temp": 30.0,
                    "target_temp_step": 0.5,
                },
            }
            for entity in registry.entities
        }
        confirmed = DomuxInstruction(
            "turnOff", "AC", "*", "*", "*", "Study", "Ground Floor"
        )
        for utterance in (
            "Turn off the AC, but not climate.study.",
            'Turn off the AC, but not "climate.study".',
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|AC|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    ("climate.living", "climate.study"),
                )
                self.assertIn("climate.study", grounded.negated_entity_ids)
                self.assertTrue(grounded.clarification.required)
                with self.assertRaises(GroundingError):
                    resolve_clarification_submission(
                        grounded,
                        answer="Use climate.study.",
                        confirmed_instruction=confirmed,
                        registry=registry,
                    )

                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    ttl_seconds=30,
                    clock=MutableClock(),
                    nonce_factory=lambda: "entity-id-negation-nonce",
                )
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use climate.study.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        separate_sentence = ground_domux_request(
            "Do not use climate.study. Turn off climate.living.",
            "turnOff|AC|*|*|*|Living Room|Ground Floor",
            registry,
        )
        self.assertEqual(separate_sentence.negated_entity_ids, ("climate.study",))
        self.assertEqual(
            tuple(entity.entity_id for entity in separate_sentence.candidates),
            ("climate.living",),
        )
        adapter = InMemoryHAAdapter(states)
        store = PreparedActionStore(
            ttl_seconds=30,
            clock=MutableClock(),
            nonce_factory=lambda: "entity-id-sentence-boundary-nonce",
        )
        action = store.prepare(
            actor_id="actor-a",
            session_id="session-a",
            grounded=separate_sentence,
            registry=registry,
            adapter=adapter,
            clarification_answer="Use climate.living.",
            confirmed_instruction=DomuxInstruction(
                "turnOff", "AC", "*", "*", "*", "Living Room", "Ground Floor"
            ),
        )
        result = store.commit(
            action.confirmation(),
            registry=registry,
            adapter=adapter,
        )
        self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
        self.assertEqual(
            adapter.sut_calls[0]["data"]["entity_id"],
            "climate.living",
        )

    def test_coordinated_negative_actions_never_restart_authorization(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        confirmed = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        for utterance in (
            "Turn off the light but do not use, choose, or mean Study Lamp.",
            "Turn off the light but do not use or choose Study Lamp.",
            "Turn off the light but do not use and choose Study Lamp.",
            "Turn off the light but do not use, select, choose, or mean Study Lamp.",
            "Turn off the light but do not use, choose, or mean the Study Lamp.",
            "Turn off the light but do not use, choose, or mean (Study Lamp).",
            'Turn off the light but do not use, choose, or mean "Study Lamp".',
            "Turn off the light but do not use, choose, or mean my Study Lamp.",
            "Turn off the light but do not use choose Study Lamp.",
            "Turn off the light but do not turn off, switch off, or close Study Lamp.",
            "Turn off the light but do not turn off, switch off, or close the Study Lamp.",
            "Turn off a light, but I don't want Study Lamp.",
            "Turn off a light, but I don't need Study Lamp.",
            "Turn off a light, but I don't have Study Lamp.",
            "Turn off a light, but I don't want to use Study Lamp.",
            "Turn off a light, but I don't need to use Study Lamp.",
            "Turn off a light, but I don't want to choose Study Lamp.",
            "Turn off a light, but I do not want Study Lamp.",
            "Turn off a light, but I do not need to use Study Lamp.",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertIn("light.study", grounded.negated_entity_ids)
                self.assertTrue(grounded.clarification.required)
                with self.assertRaises(GroundingError):
                    resolve_clarification_submission(
                        grounded,
                        answer="Use Study Lamp.",
                        confirmed_instruction=confirmed,
                        registry=registry,
                    )

                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    ttl_seconds=30,
                    clock=MutableClock(),
                    nonce_factory=lambda: "coordinated-negation-nonce",
                )
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

    def test_negator_to_predicate_gaps_cannot_authorize_a_selector(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        confirmed = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        negated = (
            "Turn off a light, but do not: use Study Lamp.",
            "Turn off a light, but don't: use Study Lamp.",
            "Turn off a light, but do not—use Study Lamp.",
            "Turn off a light, but do not, please, use Study Lamp.",
            "Turn off a light, but do not use: choose Study Lamp.",
            "Turn off a light, but I don't want to use, choose Study Lamp.",
            "Turn off a light, but I don't want to use, choose, or mean Study Lamp.",
            "Turn off a light, but I don't need to use, choose Study Lamp.",
            "Turn off a light, but I do not want to use, choose Study Lamp.",
            "Turn off a light, but do not, I am sure, use Study Lamp.",
            "Turn off a light, but don't, would you, use Study Lamp.",
            "Turn off a light, but do not, could you, use Study Lamp.",
            "Turn off a light, but do not, can you, use Study Lamp.",
            "Turn off a light, but do not, for me, use Study Lamp.",
            "Turn off a light, but do not, around, use Study Lamp.",
            "Turn off a light, but not!!! Study Lamp.",
            "Turn off a light, but not... Study Lamp.",
            "Turn off a light, but do not!!! use Study Lamp.",
            "Turn off a light, but don't... use Study Lamp.",
            "Turn off a light, but do not?! choose Study Lamp.",
            "Turn off a light, but no! Study Lamp.",
            "Turn off a light, but do not! use Study Lamp.",
            "Turn off a light, but don't? choose Study Lamp.",
            "Turn off a light, but not! use Study Lamp.",
            "Turn off a light, but do not. use Study Lamp.",
            "Do not! turn off the Study light.",
            "Don't! turn off the Study light.",
            "Do not. turn off the Study light.",
            "Do not? turn off the Study light.",
            "Do not! increase the Study light brightness by 10 percent.",
            "Turn off a light, no, wait, Study Lamp.",
            "Turn off a light, no, wait, use Study Lamp.",
        )
        for utterance in negated:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertIn("light.study", grounded.negated_entity_ids)
                self.assertTrue(grounded.clarification.required)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    ttl_seconds=30,
                    clock=MutableClock(),
                    nonce_factory=lambda: "negator-gap-nonce",
                )
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        for utterance in (
            "Turn off a light, wait, Study Lamp.",
            "Turn off a light, wait, use Study Lamp.",
        ):
            with self.subTest(suspensive_wait=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn(
                    "negative_or_cancelled_intent",
                    grounded.clarification.reasons,
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

    def test_generic_exclusions_cannot_be_laundered_by_clarification(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec(
                "climate.living", "climate", "AC", "Living Room", "Ground Floor"
            ),
        ))
        states = {
            "light.study": {
                "entity_id": "light.study",
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            },
            "climate.living": {
                "entity_id": "climate.living",
                "state": "cool",
                "attributes": {
                    "temperature": 24.0,
                    "hvac_modes": ["off", "cool"],
                    "fan_modes": ["low", "medium", "high"],
                    "supported_features": 1,
                    "temperature_unit": "°C",
                    "min_temp": 16.0,
                    "max_temp": 30.0,
                    "target_temp_step": 0.5,
                },
            },
        }
        confirmed = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )

        def assert_blocked(utterance: str, *, domain_exclusion: bool) -> None:
            grounded = ground_domux_request(
                utterance,
                "turnOff|Light|*|*|*|Study|Ground Floor",
                registry,
            )
            self.assertTrue(grounded.clarification.required)
            if domain_exclusion:
                self.assertIn("light.study", grounded.negated_entity_ids)
            else:
                self.assertIn(
                    "unsupported_request_grammar",
                    grounded.clarification.reasons,
                )
            adapter = InMemoryHAAdapter(states)
            store = PreparedActionStore(
                ttl_seconds=30,
                clock=MutableClock(),
                nonce_factory=lambda: "generic-exclusion-nonce",
            )
            with self.assertRaises(GroundingError):
                store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer="Yes, use the Study light.",
                    confirmed_instruction=confirmed,
                )
            self.assertEqual(adapter.sut_calls, [])

        for utterance in (
            "Turn off a device, but avoid the light.",
            "Turn off a device, but use anything other than the light.",
            "Turn off a device, but use anything besides the light.",
            "Turn off a device, but use anything apart from the light.",
            "Turn off a device instead of the light.",
            "Turn off anything but the light.",
            "Turn off a device without the light.",
            "Turn off a device except for the light.",
            "Turn off a device, but do not use any light.",
        ):
            with self.subTest(domain_exclusion=utterance):
                assert_blocked(utterance, domain_exclusion=True)

        for utterance in (
            "Turn off a device, but avoid the device.",
            "Turn off a device other than the device.",
            "Turn off a device besides the device.",
            "Turn off a device apart from the device.",
            "Turn off a device instead of the device.",
            "Turn off a device, but do not use anything.",
            "Turn off a device, but do not use any device.",
            "Turn off a device, but do not use a device.",
            "Turn off a light. Do not use anything. Study.",
        ):
            with self.subTest(unresolved_generic=utterance):
                assert_blocked(utterance, domain_exclusion=False)

        # Double-negation/"only" semantics are deliberately outside the
        # bounded grammar.  Fail closed even though the conservative parser
        # records the named selector itself as negated.
        double_negative = "Turn off a light, but don't use anything but Study."
        grounded = ground_domux_request(
            double_negative,
            "turnOff|Light|*|*|*|Study|Ground Floor",
            registry,
        )
        self.assertTrue(grounded.clarification.required)
        self.assertIn("light.study", grounded.negated_entity_ids)
        adapter = InMemoryHAAdapter(states)
        store = PreparedActionStore(clock=MutableClock())
        with self.assertRaises(GroundingError):
            store.prepare(
                actor_id="actor-a",
                session_id="session-a",
                grounded=grounded,
                registry=registry,
                adapter=adapter,
                clarification_answer="Yes, use the Study light.",
                confirmed_instruction=confirmed,
            )
        self.assertEqual(adapter.sut_calls, [])

        specific_registry = EntityRegistry((
            EntitySpec("light.right", "light", "Light", "Right", "Ground Floor"),
            EntitySpec("light.left", "light", "Light", "Left", "Ground Floor"),
        ))
        specific_states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in specific_registry.entities
        }
        grounded = ground_domux_request(
            "Do not use the light in Right, turn off the light in Left.",
            "turnOff|Light|*|*|*|Left|Ground Floor",
            specific_registry,
        )
        self.assertEqual(grounded.negated_entity_ids, ("light.right",))
        adapter = InMemoryHAAdapter(specific_states)
        store = PreparedActionStore(
            clock=MutableClock(),
            nonce_factory=lambda: "qualified-selector-nonce",
        )
        action = store.prepare(
            actor_id="actor-a",
            session_id="session-a",
            grounded=grounded,
            registry=specific_registry,
            adapter=adapter,
            clarification_answer="Use the light in Left.",
            confirmed_instruction=DomuxInstruction(
                "turnOff", "Light", "*", "*", "*", "Left", "Ground Floor"
            ),
        )
        result = store.commit(
            action.confirmation(),
            registry=specific_registry,
            adapter=adapter,
        )
        self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
        self.assertEqual(adapter.sut_calls[0]["data"]["entity_id"], "light.left")

    def test_anaphoric_exclusions_bind_the_later_named_referent(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        confirmed = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        utterances = (
            "Turn off a light, but do not use that, but by that I mean Study Lamp.",
            "Turn off a light, but do not use it, but by it I mean Study Lamp.",
            (
                "Turn off a light, but do not use the one that I mean, "
                "but by that I mean Study Lamp."
            ),
            (
                "Turn off a light, but do not use the light that I mean—"
                "but by that I mean Study Lamp."
            ),
            (
                "Turn off a light, but do not use it, "
                "but by it, I mean Study Lamp."
            ),
            (
                "Turn off a light, but do not use that, "
                "but by that, I mean Study Lamp."
            ),
            (
                "Turn off a light, but do not use this, "
                "but by this— I mean Study Lamp."
            ),
            "Turn off a light, but do not use it; by it, I mean Study Lamp.",
        )
        for utterance in utterances:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertIn("light.study", grounded.negated_entity_ids)
                self.assertTrue(grounded.clarification.required)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "anaphoric-exclusion-nonce",
                )
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

    def test_generic_withdrawals_block_but_complete_new_commands_restart_scope(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        confirmed = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        withdrawals = (
            "Turn off a light; I don't need any light.",
            "Turn off a light; I don't want any light.",
            "Turn off a light; I do not need the light.",
            "Turn off a light; I do not want the light.",
            "Turn off a light; no need for any light.",
            "Turn off a light; I have no need for a light.",
            "Turn off a light; I don't have any light.",
            "Turn off a light; I would not use any light.",
            "Turn off a light; I could not use any light.",
            "Turn off a light; I can not use any light.",
            "Turn off a light; I have no use for any light.",
            "Turn off a light; I do not want to use any light.",
            "Turn off a light; I don't need you to use any light.",
            "Turn off a light; I do not want you to use the light.",
            "Turn off a light; no need to use any light.",
            "Turn off a light; no need to choose any light.",
            "Turn off a light; no need to select any light.",
            "Turn off a light; I need not use any light.",
            "Turn off a light; we need not choose any light.",
            "Turn off a light; you need not select any light.",
            "Turn off a light; I want not to use any light.",
            "Turn off a light; we want not to choose any light.",
            "Turn off a light; I have no need to use any light.",
            "Turn off a light; we have no need to choose any light.",
            "Turn off a light; no use of any light.",
            "Turn off a light; I have no use of any light.",
            "Turn off a light; I want no use of any light.",
        )
        for utterance in withdrawals:
            with self.subTest(withdrawal=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|*|*",
                    registry,
                )
                self.assertIn(
                    "negative_or_cancelled_intent",
                    grounded.clarification.reasons,
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Turn off Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        explanatory_colon_withdrawals = (
            "Don't do it: turn off Study Lamp.",
            "Do not execute this: turn off Study Lamp.",
            "Do not confirm this: turn off Study Lamp.",
            "I don't want this: turn off Study Lamp.",
            "I do not need it: turn off Study Lamp.",
            "I have no need for this: turn off Study Lamp.",
            "No need for this: turn off Study Lamp.",
            "Avoid this: turn off Study Lamp.",
        )
        for utterance in explanatory_colon_withdrawals:
            with self.subTest(explanatory_colon_withdrawal=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertIn("light.study", grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Turn off Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        target_only_restatements = (
            "Turn off a light; I don't want any light, I mean Study Lamp.",
            "Turn off a light; I don't need any light, I mean Study Lamp.",
            "Turn off a light; I don't have any light, I mean Study Lamp.",
            "Turn off a light; I would not use any light, I mean Study Lamp.",
            "Turn off a light; no need for any light, I mean Study Lamp.",
            "Turn off a light; I have no use for any light, I mean Study Lamp.",
            "Do not turn off Living Lamp, I mean Study Lamp.",
            "I don't want Living Lamp, I mean Study Lamp.",
        )
        for utterance in target_only_restatements:
            with self.subTest(target_only_restart=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertIn(
                    "negative_or_cancelled_intent",
                    grounded.clarification.reasons,
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        negative_state_restatements = (
            (
                "Turn off a light; do not use or move Living Lamp, "
                "I mean Study Lamp."
            ),
            (
                "Turn off a light; do not use and move Living Lamp, "
                "I mean Study Lamp."
            ),
            (
                "Turn off a light; do not use Living Lamp to move it, "
                "I mean Study Lamp."
            ),
        )
        for utterance in negative_state_restatements:
            with self.subTest(negative_state_restatement=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertIn("light.study", grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        incomplete_restarts = (
            "Turn off a light; I do not want any light; adjust Study Lamp.",
            "Turn off a light; I do not want any light; move Study Lamp.",
            "Turn off a light; I do not want any light; change Study Lamp.",
            "Turn off a light; I do not want any light; make Study Lamp.",
            "Turn off a light; I do not want any light; turn around Study Lamp.",
            "Turn off a light; I do not want any light; turn to Study Lamp.",
            "Turn off a light; I do not want any light; switch around Study Lamp.",
        )
        for utterance in incomplete_restarts:
            with self.subTest(incomplete_restart=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertIn("action", grounded.clarification.unresolved_slots)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use Study Lamp.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        repaired_restart = ground_domux_request(
            "Turn off a light; I do not want any light; adjust Study Lamp.",
            "turnOff|Light|*|*|*|Study|Ground Floor",
            registry,
        )
        adapter = InMemoryHAAdapter(states)
        store = PreparedActionStore(
            clock=MutableClock(),
            nonce_factory=lambda: "repaired-restart-nonce",
        )
        action = store.prepare(
            actor_id="actor-a",
            session_id="session-a",
            grounded=repaired_restart,
            registry=registry,
            adapter=adapter,
            clarification_answer="Turn off Study Lamp.",
            confirmed_instruction=confirmed,
        )
        result = store.commit(action.confirmation(), registry=registry, adapter=adapter)
        self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
        self.assertEqual(adapter.sut_calls[0]["data"]["entity_id"], "light.study")

        direct_generic_exclusions = (
            "Turn off a light; I would not use any light.",
            "Turn off a light; I could not use any light.",
            "Turn off a light; I can not use any light.",
            "Turn off a light; I do not want to use any light.",
            "Turn off a light; I don't need you to use any light.",
            "Turn off a light; I do not want you to use the light.",
            "Turn off a light; no need to use any light.",
            "Turn off a light; I need not choose any light.",
            "Turn off a light; I want not to select any light.",
            "Turn off a light; I have no need to choose any light.",
            "Turn off a light; no use of any light.",
            "Turn off a light; I want no use of any light.",
        )
        for utterance in direct_generic_exclusions:
            with self.subTest(direct_generic_exclusion=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|*|*",
                    registry,
                )
                self.assertEqual(
                    grounded.negated_entity_ids,
                    ("light.living", "light.study"),
                )

        positive_restarts = (
            "Do not turn off Living Lamp; turn off Study Lamp.",
            "Do not use Living Lamp: turn off Study Lamp.",
            "Don't turn off Living Lamp, then turn off Study Lamp.",
            "Never turn off Living Lamp. Turn off Study Lamp.",
            "Do not switch off Living Lamp—but turn off Study Lamp.",
            "I don't need any light; turn off Study Lamp.",
            "I would not use any light. Turn off Study Lamp.",
            "I have no use for any light—turn off Study Lamp.",
            "I do not want any light; can you turn off Study Lamp?",
            "I do not want any light; could you please turn off Study Lamp?",
            "I do not want any light; would you turn off Study Lamp?",
            "I do not want any light; will you turn off Study Lamp?",
            "I do not want any light; I want you to turn off Study Lamp.",
            "I do not want any light; I need you to turn off Study Lamp.",
            "I do not want any light; can you just turn off Study Lamp?",
            "I do not want any light; could you please just turn off Study Lamp?",
            "I do not want any light; I want you to just turn off Study Lamp.",
            "I do not want any light; I just want you to turn off Study Lamp.",
            "I need not choose any light; turn off Study Lamp.",
            "No use of any light; turn off Study Lamp.",
            "I don't need any light; proceed to turn off Study Lamp.",
            "I don't need any light; execute turn off Study Lamp.",
            "Do not proceed; turn off Study Lamp.",
            "Do not execute; turn off Study Lamp.",
            "Do not confirm; turn off Study Lamp.",
            "Do not proceed, then turn off Study Lamp.",
            "Do not execute, then turn off Study Lamp.",
            "Do not confirm, then turn off Study Lamp.",
            "No need to use any light; turn off Study Lamp.",
            "Anything but turn off Living Lamp; turn off Study Lamp.",
            "Turn off any light but use Study Lamp.",
            "Turn off any light, but use Study Lamp.",
        )
        for utterance in positive_restarts:
            with self.subTest(positive_restart=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertNotIn(
                    "negative_or_cancelled_intent",
                    grounded.clarification.reasons,
                )
                self.assertNotIn("light.study", grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "positive-restart-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer="Turn off Study Lamp.",
                    confirmed_instruction=confirmed,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
                self.assertEqual(
                    adapter.sut_calls[0]["data"]["entity_id"],
                    "light.study",
                )

    def test_selector_aware_positive_corrections_remain_executable(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        cases = (
            (
                "Turn off a light, do not use Living Lamp, I mean Study Lamp.",
                "turnOff|Light|*|*|*|Study|Ground Floor",
                "Use Study Lamp.",
            ),
            (
                "Turn off a light, do not use Living Lamp, just use Study Lamp.",
                "turnOff|Light|*|*|*|Study|Ground Floor",
                "Use Study Lamp.",
            ),
            (
                "Turn off a light, do not use Living Lamp, I choose Study Lamp.",
                "turnOff|Light|*|*|*|Study|Ground Floor",
                "Use Study Lamp.",
            ),
            (
                "Turn off a light, do not use Living Lamp, Study Lamp instead.",
                "turnOff|Light|*|*|*|Study|Ground Floor",
                "Use Study Lamp.",
            ),
            (
                "Turn off not Living Lamp: use Study Lamp.",
                "turnOff|Light|*|*|*|Study|Ground Floor",
                "Use Study Lamp.",
            ),
            (
                "Turn off not Living Lamp—use Study Lamp.",
                "turnOff|Light|*|*|*|Study|Ground Floor",
                "Use Study Lamp.",
            ),
            (
                "Do not use Living Lamp, increase Study Lamp brightness by 10 percent.",
                "adjustUp|Light|brightness|10|Percent|Study|Ground Floor",
                "Increase Study Lamp brightness by 10 percent.",
            ),
            (
                "Do not use Living Lamp, raise Study Lamp brightness by 10 percent.",
                "adjustUp|Light|brightness|10|Percent|Study|Ground Floor",
                "Raise Study Lamp brightness by 10 percent.",
            ),
            (
                "Do not use Living Lamp, lower Study Lamp brightness by 10 percent.",
                "adjustDown|Light|brightness|10|Percent|Study|Ground Floor",
                "Lower Study Lamp brightness by 10 percent.",
            ),
            (
                "Do not use Living Lamp, change Study Lamp brightness to 50 percent.",
                "set|Light|brightness|50|Percent|Study|Ground Floor",
                "Change Study Lamp brightness to 50 percent.",
            ),
        )
        for utterance, raw_output, clarification_answer in cases:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw_output, registry)
                self.assertEqual(grounded.negated_entity_ids, ("light.living",))
                self.assertNotIn("light.study", grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "selector-correction-nonce",
                )
                confirmed = parse_domux_output(raw_output)[0]
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer=clarification_answer,
                    confirmed_instruction=confirmed,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
                self.assertEqual(
                    adapter.sut_calls[0]["data"]["entity_id"],
                    "light.study",
                )

    def test_postposed_no_corrections_withdraw_old_selector(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
            EntitySpec(
                "light.kitchen", "light", "Light", "Kitchen", "Ground Floor",
                aliases=("Kitchen Lamp",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        confirmed_living = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Living Room", "Ground Floor"
        )
        confirmed_study = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        for utterance in (
            "Turn off Study Lamp—no, Living Lamp instead.",
            "Turn off Study Lamp, no, Living Lamp instead.",
            "Turn off Study Lamp. No, Living Lamp instead.",
            "Turn off Study Lamp—no, I mean Living Lamp.",
            "Turn off Study Lamp; no, choose Living Lamp instead.",
            "Turn off Study Lamp? No, use the Living Lamp instead.",
            "Turn off Study Lamp - no, Living Lamp instead.",
            "Turn off Study Lamp–no, Living Lamp instead.",
            "Turn off Study Lamp—no: Living Lamp instead.",
            "Turn off Study Lamp—no. Living Lamp instead.",
            "Turn off Study Lamp—no! Living Lamp instead.",
            "Turn off Study Lamp—no? Living Lamp instead.",
            "Turn off Study Lamp—no; Living Lamp instead.",
            "Turn off Study Lamp—no。 Living Lamp instead.",
            "Turn off Study Lamp—no！ Living Lamp instead.",
            "Turn off Study Lamp—no？ Living Lamp instead.",
            "Turn off Study Lamp—no； Living Lamp instead.",
            "Turn off Study Lamp，no, Living Lamp instead.",
            "Turn off Study Lamp—no, turn off Living Lamp instead.",
            "Turn off Study Lamp—no, please turn off the Living Lamp instead.",
            "Turn off Study Lamp—no, switch off Living Lamp instead.",
        ):
            with self.subTest(positive_replacement=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Living Room|Ground Floor",
                    registry,
                )
                self.assertEqual(grounded.negated_entity_ids, ("light.study",))
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    ("light.living",),
                )
                with self.assertRaises(GroundingError):
                    resolve_clarification_submission(
                        grounded,
                        answer="Use Study Lamp.",
                        confirmed_instruction=confirmed_study,
                        registry=registry,
                    )

                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "postposed-correction-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer="Use Living Lamp.",
                    confirmed_instruction=confirmed_living,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
                self.assertEqual(
                    adapter.sut_calls[0]["data"]["entity_id"],
                    "light.living",
                )

        value_correction = ground_domux_request(
            "Set Study Lamp to 30 percent—no, perhaps 60 percent; confirm the value.",
            "set|Light|brightness|60|Percent|Study|Ground Floor",
            registry,
        )
        self.assertEqual(value_correction.negated_entity_ids, ())
        self.assertEqual(
            tuple(entity.entity_id for entity in value_correction.candidates),
            ("light.study",),
        )
        negative_value_correction = ground_domux_request(
            "Set Study Lamp to 30 percent—no, not 30, 60 percent instead.",
            "set|Light|brightness|60|Percent|Study|Ground Floor",
            registry,
        )
        self.assertNotIn(
            "negative_or_cancelled_intent",
            negative_value_correction.clarification.reasons,
        )
        self.assertNotIn("light.study", negative_value_correction.negated_entity_ids)

        chained = ground_domux_request(
            "Turn off Study Lamp—no, Living Lamp, no, Kitchen Lamp instead.",
            "turnOff|Light|*|*|*|Kitchen|Ground Floor",
            registry,
        )
        self.assertEqual(
            chained.negated_entity_ids,
            ("light.living", "light.study"),
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in chained.candidates),
            ("light.kitchen",),
        )
        with self.assertRaises(GroundingError):
            resolve_clarification_submission(
                chained,
                answer="Use Living Lamp.",
                confirmed_instruction=confirmed_living,
                registry=registry,
            )
        confirmed_kitchen = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Kitchen", "Ground Floor"
        )
        chained_adapter = InMemoryHAAdapter(states)
        chained_store = PreparedActionStore(
            clock=MutableClock(),
            nonce_factory=lambda: "chained-correction-nonce",
        )
        chained_action = chained_store.prepare(
            actor_id="actor-a",
            session_id="session-a",
            grounded=chained,
            registry=registry,
            adapter=chained_adapter,
            clarification_answer="Use Kitchen Lamp.",
            confirmed_instruction=confirmed_kitchen,
        )
        chained_result = chained_store.commit(
            chained_action.confirmation(),
            registry=registry,
            adapter=chained_adapter,
        )
        self.assertEqual(
            (chained_result.status, len(chained_adapter.sut_calls)),
            ("COMMITTED", 1),
        )
        self.assertEqual(
            chained_adapter.sut_calls[0]["data"]["entity_id"],
            "light.kitchen",
        )

        corrected_back = ground_domux_request(
            "Turn off Study Lamp—no, Living Lamp, no, Study Lamp instead.",
            "turnOff|Light|*|*|*|Study|Ground Floor",
            registry,
        )
        self.assertEqual(corrected_back.negated_entity_ids, ("light.living",))
        self.assertEqual(
            tuple(entity.entity_id for entity in corrected_back.candidates),
            ("light.study",),
        )
        corrected_back_adapter = InMemoryHAAdapter(states)
        corrected_back_store = PreparedActionStore(
            clock=MutableClock(),
            nonce_factory=lambda: "corrected-back-nonce",
        )
        corrected_back_action = corrected_back_store.prepare(
            actor_id="actor-a",
            session_id="session-a",
            grounded=corrected_back,
            registry=registry,
            adapter=corrected_back_adapter,
            clarification_answer="Use Study Lamp.",
            confirmed_instruction=confirmed_study,
        )
        corrected_back_result = corrected_back_store.commit(
            corrected_back_action.confirmation(),
            registry=registry,
            adapter=corrected_back_adapter,
        )
        self.assertEqual(
            (corrected_back_result.status, len(corrected_back_adapter.sut_calls)),
            ("COMMITTED", 1),
        )
        self.assertEqual(
            corrected_back_adapter.sut_calls[0]["data"]["entity_id"],
            "light.study",
        )

        for utterance in (
            "Turn off Study Lamp, no, wait, Living Lamp.",
            "Turn off Study Lamp—no, not Living Lamp instead.",
            "Do not use Study Lamp—no, Living Lamp instead.",
        ):
            with self.subTest(non_positive_replacement=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Living Room|Ground Floor",
                    registry,
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "blocked-postposed-correction-nonce",
                )
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Use Living Lamp.",
                        confirmed_instruction=confirmed_living,
                    )
                self.assertEqual(adapter.sut_calls, [])

        for utterance in (
            "Turn off Study Lamp—no, not Living Lamp instead.",
            "Turn off Study Lamp—no, please don't use Living Lamp instead.",
            "Turn off Study Lamp—no, please do not use Living Lamp instead.",
            "Turn off Study Lamp—no, avoid Living Lamp instead.",
            "Turn off Study Lamp—no, anything but Living Lamp instead.",
            "Turn off Study Lamp—no, wait, I mean Living Lamp instead.",
        ):
            with self.subTest(block_candidate_expansion=utterance):
                unsafe_replacement = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Kitchen|Ground Floor",
                    registry,
                )
                self.assertIn(
                    "negative_or_cancelled_intent",
                    unsafe_replacement.clarification.reasons,
                )
                expansion_adapter = InMemoryHAAdapter(states)
                expansion_store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    expansion_store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=unsafe_replacement,
                        registry=registry,
                        adapter=expansion_adapter,
                        clarification_answer="Use Kitchen Lamp.",
                        confirmed_instruction=confirmed_kitchen,
                    )
                self.assertEqual(expansion_adapter.sut_calls, [])

    def test_generic_domain_restart_requires_positive_domain_evidence(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.study", "light", "Light", "Study", "Ground Floor",
                aliases=("Study Lamp",),
            ),
            EntitySpec(
                "light.living", "light", "Light", "Living Room", "Ground Floor",
                aliases=("Living Lamp",),
            ),
            EntitySpec(
                "cover.study", "cover", "Curtain", "Study", "Ground Floor",
                aliases=("Study Curtain",),
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on" if entity.domain == "light" else "open",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                } if entity.domain == "light" else {
                    "current_position": 100,
                },
            }
            for entity in registry.entities
        }
        confirmed = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        blocked_restarts = (
            (
                "Turn off a device; I don't need any light; turn off.",
                "turnOff|Light|*|*|*|*|*",
            ),
            (
                "Turn off a device; I don't need any light; turn off a device.",
                "turnOff|Light|*|*|*|*|*",
            ),
            (
                "Turn off a device; I don't need any light; turn off Study.",
                "turnOff|Light|*|*|*|Study|Ground Floor",
            ),
            *(
                (
                    f"Turn off a device; I don't need any light; "
                    f"turn off a device {preposition} light.",
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                )
                for preposition in (
                    "about", "around", "at", "by", "for", "from", "in", "on", "to"
                )
            ),
        )
        for utterance, raw_output in blocked_restarts:
            with self.subTest(blocked_restart=utterance):
                grounded = ground_domux_request(utterance, raw_output, registry)
                self.assertEqual(
                    grounded.negated_entity_ids,
                    ("light.living", "light.study"),
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Turn off Study light on Ground Floor.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        universal_blocked_restarts = (
            "Turn off a device; I don't need any device; turn off.",
            "Turn off a device; I don't want any device; turn off a device.",
            "Turn off a device; do not use any device; turn off.",
            "Turn off a device; I don't need anything; turn off.",
        )
        for utterance in universal_blocked_restarts:
            with self.subTest(universal_blocked_restart=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|*|*",
                    registry,
                )
                self.assertEqual(
                    grounded.negated_entity_ids,
                    ("cover.study", "light.living", "light.study"),
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Turn off Study light on Ground Floor.",
                        confirmed_instruction=confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        for utterance in (
            "Turn off a device; I don't need any light; turn off Study Lamp.",
            "Turn off a device; I don't need any light; turn off a light.",
        ):
            with self.subTest(domain_reauthorized=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertEqual(grounded.negated_entity_ids, ())
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "domain-restart-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer="Turn off Study light on Ground Floor.",
                    confirmed_instruction=confirmed,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
                self.assertEqual(
                    adapter.sut_calls[0]["data"]["entity_id"],
                    "light.study",
                )

        for utterance in (
            "I don't need any device; turn off Study Lamp.",
            "I don't need anything; turn off Study Lamp.",
        ):
            with self.subTest(universal_reauthorized=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertNotIn("light.study", grounded.negated_entity_ids)
                self.assertIn("cover.study", grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "universal-restart-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer="Turn off Study light on Ground Floor.",
                    confirmed_instruction=confirmed,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))

        set_confirmed = DomuxInstruction(
            "set", "Light", "brightness", "25", "Percent", "Study", "Ground Floor"
        )
        attribute_first_restarts = (
            "I don't want any light; set the brightness of Study light to 25 percent.",
            "I don't want any light; change the brightness on Study light to 25 percent.",
            (
                "I don't want any light; could you set the brightness of "
                "Study light to 25 percent?"
            ),
            "I don't want any light; set brightness to 25 percent on Study light.",
        )
        for utterance in attribute_first_restarts:
            with self.subTest(attribute_first_restart=utterance):
                grounded = ground_domux_request(
                    utterance,
                    set_confirmed.to_pipe(),
                    registry,
                )
                self.assertNotIn("light.study", grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "attribute-first-restart-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer=(
                        "Set Study light brightness to 25 percent on Ground Floor."
                    ),
                    confirmed_instruction=set_confirmed,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))

    def test_generic_device_aliases_share_one_exclusion_and_restart_policy(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Lamp", "Study", "Ground Floor"),
            EntitySpec("cover.study", "cover", "Blind", "Study", "Ground Floor"),
            EntitySpec(
                "climate.study", "climate", "Air Conditioner", "Study", "Ground Floor"
            ),
        ))
        states = {
            "light.study": {
                "entity_id": "light.study",
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            },
            "cover.study": {
                "entity_id": "cover.study",
                "state": "open",
                "attributes": {"current_position": 100, "supported_features": 7},
            },
            "climate.study": {
                "entity_id": "climate.study",
                "state": "cool",
                "attributes": {
                    "temperature": 24.0,
                    "hvac_modes": ["off", "cool", "heat"],
                    "supported_features": 1,
                    "temperature_unit": "°C",
                    "min_temp": 16.0,
                    "max_temp": 30.0,
                    "target_temp_step": 0.5,
                },
            },
        }
        climate_confirmed = DomuxInstruction(
            "turnOff", "Air Conditioner", "*", "*", "*", "Study", "Ground Floor"
        )
        climate_exclusions = (
            "Turn off a device; do not use any air conditioning; turn off a device.",
            "Turn off a device; avoid any air conditioning; turn off a device.",
            "Turn off a device; without any air conditioning; turn off a device.",
            "Turn off a device; do not use any a c; turn off a device.",
            "Turn off a device; avoid any A/C; turn off a device.",
        )
        for utterance in climate_exclusions:
            with self.subTest(climate_exclusion=utterance):
                grounded = ground_domux_request(
                    utterance,
                    climate_confirmed.to_pipe(),
                    registry,
                )
                self.assertIn("climate.study", grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=registry,
                        adapter=adapter,
                        clarification_answer="Turn off the Study Air Conditioner.",
                        confirmed_instruction=climate_confirmed,
                    )
                self.assertEqual(adapter.sut_calls, [])

        positive_restarts = (
            (
                "I don't want any lamp; turn off Study lamp.",
                DomuxInstruction(
                    "turnOff", "Lamp", "*", "*", "*", "Study", "Ground Floor"
                ),
                "Turn off Study lamp.",
            ),
            (
                "I don't want any blind; close Study blind.",
                DomuxInstruction(
                    "turnOff", "Blind", "*", "*", "*", "Study", "Ground Floor"
                ),
                "Close Study blind.",
            ),
            (
                "I don't want any air conditioner; turn off Study air conditioner.",
                climate_confirmed,
                "Turn off Study air conditioner.",
            ),
        )
        for utterance, confirmed, clarification_answer in positive_restarts:
            with self.subTest(generic_device_positive_restart=utterance):
                grounded = ground_domux_request(
                    utterance,
                    confirmed.to_pipe(),
                    registry,
                )
                self.assertEqual(grounded.negated_entity_ids, ())
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "generic-device-restart-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer=clarification_answer,
                    confirmed_instruction=confirmed,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))

    def test_domain_aliases_inside_room_names_do_not_reauthorize_that_domain(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.ac_room", "light", "Light", "AC Room", "Ground Floor"),
            EntitySpec("climate.study", "climate", "AC", "Study", "Ground Floor"),
            EntitySpec(
                "cover.light_room", "cover", "Curtain", "Light Room", "Ground Floor"
            ),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        climate_excluded = ground_domux_request(
            "I don't want any AC; turn off the light in AC Room.",
            "turnOff|Light|*|*|*|AC Room|Ground Floor",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in climate_excluded.candidates),
            ("light.ac_room",),
        )
        self.assertIn("climate.study", climate_excluded.negated_entity_ids)
        self.assertNotIn("light.ac_room", climate_excluded.negated_entity_ids)

        light_excluded = ground_domux_request(
            "I don't want any light; close the curtain in Light Room.",
            "turnOff|Curtain|*|*|*|Light Room|Ground Floor",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in light_excluded.candidates),
            ("cover.light_room",),
        )
        self.assertIn("light.study", light_excluded.negated_entity_ids)
        self.assertNotIn("cover.light_room", light_excluded.negated_entity_ids)

    def test_domain_alias_inside_room_preserves_the_explicit_room_constraint(self) -> None:
        registry = EntityRegistry((
            EntitySpec("climate.ac_room", "climate", "AC", "AC Room", "Ground Floor"),
            EntitySpec("climate.study", "climate", "AC", "Study", "Ground Floor"),
            EntitySpec(
                "light.light_room", "light", "Light", "Light Room", "Ground Floor"
            ),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        states = {
            "climate.ac_room": {
                "entity_id": "climate.ac_room",
                "state": "cool",
                "attributes": {
                    "temperature": 24.0,
                    "hvac_modes": ["off", "cool", "heat"],
                    "supported_features": 1,
                    "temperature_unit": "°C",
                    "min_temp": 16.0,
                    "max_temp": 30.0,
                    "target_temp_step": 0.5,
                },
            },
            "climate.study": {
                "entity_id": "climate.study",
                "state": "cool",
                "attributes": {
                    "temperature": 24.0,
                    "hvac_modes": ["off", "cool", "heat"],
                    "supported_features": 1,
                    "temperature_unit": "°C",
                    "min_temp": 16.0,
                    "max_temp": 30.0,
                    "target_temp_step": 0.5,
                },
            },
            "light.light_room": {
                "entity_id": "light.light_room",
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            },
            "light.study": {
                "entity_id": "light.study",
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            },
        }
        cases = (
            (
                "I don't want any AC; turn off the AC in AC Room.",
                DomuxInstruction(
                    "turnOff", "AC", "*", "*", "*", "AC Room", "Ground Floor"
                ),
                "climate.ac_room",
            ),
            (
                "I don't want any light; turn off the light in Light Room.",
                DomuxInstruction(
                    "turnOff", "Light", "*", "*", "*", "Light Room", "Ground Floor"
                ),
                "light.light_room",
            ),
        )
        for utterance, confirmed, expected_entity_id in cases:
            with self.subTest(spatial_domain_alias=utterance):
                grounded = ground_domux_request(
                    utterance,
                    confirmed.to_pipe(),
                    registry,
                )
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    (expected_entity_id,),
                )
                self.assertNotIn(expected_entity_id, grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "spatial-domain-alias-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer=utterance.split("; ", 1)[1],
                    confirmed_instruction=confirmed,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
                self.assertEqual(
                    adapter.sut_calls[0]["data"]["entity_id"],
                    expected_entity_id,
                )

    def test_shared_device_labels_preserve_qualified_negative_scope(self) -> None:
        registry = EntityRegistry((
            EntitySpec(
                "light.living", "light", "Ceiling Light", "Living Room", "Ground Floor"
            ),
            EntitySpec(
                "light.study", "light", "Ceiling Light", "Study", "Ground Floor"
            ),
        ))
        states = {
            entity.entity_id: {
                "entity_id": entity.entity_id,
                "state": "on",
                "attributes": {
                    "brightness": 128,
                    "supported_color_modes": ["brightness"],
                },
            }
            for entity in registry.entities
        }
        confirmed = DomuxInstruction(
            "turnOff", "Ceiling Light", "*", "*", "*", "Study", "Ground Floor"
        )
        utterances = (
            "Do not use the Living Room Ceiling Light; turn off the Study Ceiling Light.",
            "Do not use the Living Room Ceiling Light. Turn off the Study Ceiling Light.",
            "Do not turn off the Living Room Ceiling Light; turn off the Study Ceiling Light.",
            "Avoid the Living Room Ceiling Light; turn off the Study Ceiling Light.",
            "Do not use the light in Living Room; turn off the Study Ceiling Light.",
        )
        for utterance in utterances:
            with self.subTest(qualified_exclusion=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Ceiling Light|*|*|*|Study|Ground Floor",
                    registry,
                )
                self.assertEqual(grounded.negated_entity_ids, ("light.living",))
                self.assertEqual(
                    tuple(entity.entity_id for entity in grounded.candidates),
                    ("light.study",),
                )
                adapter = InMemoryHAAdapter(states)
                store = PreparedActionStore(
                    clock=MutableClock(),
                    nonce_factory=lambda: "shared-device-restart-nonce",
                )
                action = store.prepare(
                    actor_id="actor-a",
                    session_id="session-a",
                    grounded=grounded,
                    registry=registry,
                    adapter=adapter,
                    clarification_answer=(
                        "Turn off the Study Ceiling Light on Ground Floor."
                    ),
                    confirmed_instruction=confirmed,
                )
                result = store.commit(
                    action.confirmation(),
                    registry=registry,
                    adapter=adapter,
                )
                self.assertEqual((result.status, len(adapter.sut_calls)), ("COMMITTED", 1))
                self.assertEqual(
                    adapter.sut_calls[0]["data"]["entity_id"],
                    "light.study",
                )

    def test_target_repair_must_answer_the_unresolved_selector_slot(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec("cover.kitchen", "cover", "Curtain", "Kitchen", "Ground Floor"),
        ))
        grounded = ground_domux_request(
            "Turn off the Study or Kitchen light.",
            "turnOff|Light|*|*|*|Study|Ground Floor",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("light.study",),
        )
        self.assertIn("room", grounded.clarification.unresolved_slots)
        confirmed = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        with self.assertRaisesRegex(GroundingError, "repaired target"):
            resolve_clarification_submission(
                grounded,
                answer="The Ground Floor.",
                confirmed_instruction=confirmed,
                registry=registry,
            )
        resolved = resolve_clarification_submission(
            grounded,
            answer="The Study light.",
            confirmed_instruction=confirmed,
            registry=registry,
        )
        self.assertEqual(resolved.chosen.entity_id, "light.study")

    def test_clarification_selector_slots_cannot_form_a_zero_match_target(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec("climate.kitchen", "climate", "AC", "Kitchen", "First Floor"),
        ))
        grounded = ground_domux_request(
            "Turn off the device.",
            "turnOff|*|*|*|*|*|*",
            registry,
        )
        with self.assertRaisesRegex(GroundingError, "inconsistent target selectors"):
            resolve_clarification_submission(
                grounded,
                answer="Turn off the Study AC.",
                confirmed_instruction=DomuxInstruction(
                    "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
                ),
                registry=registry,
            )

    def test_operation_only_answer_is_allowed_when_original_target_is_fully_bound(self) -> None:
        registry = EntityRegistry((
            EntitySpec("climate.lab", "climate", "AC", "Test Lab", "Ground Floor"),
            EntitySpec("light.utility", "light", "Utility Light", "Utility Room", "Basement"),
        ))
        grounded = ground_domux_request(
            "Set the Test Lab AC.",
            "set|AC|temperature|22|Celsius|Test Lab|Ground Floor",
            registry,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("climate.lab",),
        )
        self.assertTrue(grounded.clarification.required)
        resolved = resolve_clarification_submission(
            grounded,
            answer="Temperature 22 Celsius.",
            confirmed_instruction=DomuxInstruction(
                "set", "AC", "temperature", "22", "Celsius", "Test Lab", "Ground Floor"
            ),
            registry=registry,
        )
        self.assertEqual(resolved.chosen.entity_id, "climate.lab")

    def test_unknown_attribute_position_and_relational_context_require_target_repair(self) -> None:
        registry = EntityRegistry((
            EntitySpec("climate.dining", "climate", "AC", "Dining Room", "Ground Floor"),
            EntitySpec("climate.lounge", "climate", "AC", "Lounge", "Ground Floor"),
        ))
        grounded = ground_domux_request(
            "Set the downstairs temperature to 24.",
            "set|AC|temperature|24|Celsius|Dining Room|Ground Floor",
            registry,
        )
        self.assertNotIn("unsupported_request_grammar", grounded.clarification.reasons)
        self.assertIn("device", grounded.clarification.unresolved_slots)
        resolved = resolve_clarification_submission(
            grounded,
            answer="Set the Dining Room AC on the Ground Floor to 24 Celsius.",
            confirmed_instruction=DomuxInstruction(
                "set", "AC", "temperature", "24", "Celsius", "Dining Room", "Ground Floor"
            ),
            registry=registry,
        )
        self.assertEqual(resolved.chosen.entity_id, "climate.dining")

        singleton = EntityRegistry((
            EntitySpec("climate.study", "climate", "AC", "Study", "Ground Floor"),
        ))
        modified_attribute = ground_domux_request(
            "Set the minimum temperature to 20 degrees.",
            "set|AC|temperature|20|Celsius|*|*",
            singleton,
        )
        self.assertIn("attribute", modified_attribute.clarification.unresolved_slots)
        with self.assertRaisesRegex(GroundingError, "operation modifier"):
            resolve_clarification_submission(
                modified_attribute,
                answer="The Study AC on the Ground Floor.",
                confirmed_instruction=DomuxInstruction(
                    "set", "AC", "temperature", "20", "Celsius", "Study", "Ground Floor"
                ),
                registry=singleton,
            )
        clarified_attribute = resolve_clarification_submission(
            modified_attribute,
            answer="Set the Study AC on the Ground Floor temperature to 20 Celsius.",
            confirmed_instruction=DomuxInstruction(
                "set", "AC", "temperature", "20", "Celsius", "Study", "Ground Floor"
            ),
            registry=singleton,
        )
        self.assertEqual(clarified_attribute.chosen.entity_id, "climate.study")

        lights = EntityRegistry((
            EntitySpec("light.hall", "light", "Light", "Hall", "Ground Floor"),
            EntitySpec("light.kitchen", "light", "Light", "Kitchen", "Ground Floor"),
            EntitySpec("light.bedroom", "light", "Light", "Bedroom", "First Floor"),
        ))
        middle = ground_domux_request(
            "Turn the one in the middle off.",
            "turnOff|Light|*|*|*|Bedroom|First Floor",
            lights,
            SessionContext(("light.hall", "light.kitchen", "light.bedroom")),
        )
        self.assertNotIn("unsupported_request_grammar", middle.clarification.reasons)
        self.assertIn("context", middle.clarification.unresolved_slots)
        self.assertEqual(
            resolve_clarification_submission(
                middle,
                answer="I mean the Bedroom light on the First Floor.",
                confirmed_instruction=DomuxInstruction(
                    "turnOff", "Light", "*", "*", "*", "Bedroom", "First Floor"
                ),
                registry=lights,
            ).chosen.entity_id,
            "light.bedroom",
        )

    def test_negative_and_other_selector_phrases_never_direct_execute(self) -> None:
        singleton = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        for direction, action in (("increase", "adjustUp"), ("decrease", "adjustDown")):
            with self.subTest(direction=direction):
                grounded = ground_domux_request(
                    f"No need to {direction} the Study light brightness.",
                    f"{action}|Light|brightness|*|*|Study|*",
                    singleton,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("negative_or_cancelled_intent", grounded.clarification.reasons)

        climate_registry = EntityRegistry((
            EntitySpec("climate.study", "climate", "AC", "Study", "Ground Floor"),
        ))
        excluded_values = (
            (
                singleton,
                "Set the Study light color to no Blue.",
                "set|Light|color|Blue|*|Study|*",
                "value:blue",
            ),
            (
                singleton,
                "Set the Study light brightness to no 50 percent.",
                "set|Light|brightness|50|Percent|Study|*",
                "number:50",
            ),
            (
                climate_registry,
                "Set the Study AC to no Cool mode.",
                "set|AC|mode|Cool|*|Study|*",
                "value:cool",
            ),
            (
                climate_registry,
                "Set the Study AC fan speed to no High level.",
                "set|AC|fan speed|High|Level|Study|*",
                "value:high",
            ),
            (
                climate_registry,
                "Set the Study AC temperature to no 20 degrees.",
                "set|AC|temperature|20|Celsius|Study|*",
                "number:20",
            ),
        )
        for value_registry, utterance, raw_output, token in excluded_values:
            with self.subTest(excluded_value=utterance):
                grounded = ground_domux_request(
                    utterance,
                    raw_output,
                    value_registry,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("value", grounded.clarification.unresolved_slots)
                self.assertIn(token, grounded.excluded_operation_value_tokens)

        two_lights = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec("light.living", "light", "Light", "Living Room", "Ground Floor"),
        ))
        excluded = ground_domux_request(
            "Turn off a light, no Study light.",
            "turnOff|Light|*|*|*|*|*",
            two_lights,
        )
        self.assertTrue(excluded.clarification.required)
        self.assertIn("light.study", excluded.negated_entity_ids)

        quantified_command_exclusions = (
            "Anything but turn off Study light.",
            "Do anything but turn off Study light.",
            "Please do anything but turn off Study light.",
            "Turn off a light; anything but turn off Study light.",
            "Anything but could you please turn off Study light?",
        )
        confirmed_study = DomuxInstruction(
            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
        )
        for utterance in quantified_command_exclusions:
            with self.subTest(quantified_command_exclusion=utterance):
                grounded = ground_domux_request(
                    utterance,
                    confirmed_study.to_pipe(),
                    two_lights,
                )
                self.assertIn("light.study", grounded.negated_entity_ids)
                adapter = InMemoryHAAdapter({
                    entity.entity_id: {
                        "entity_id": entity.entity_id,
                        "state": "on",
                        "attributes": {
                            "brightness": 128,
                            "supported_color_modes": ["brightness"],
                        },
                    }
                    for entity in two_lights.entities
                })
                store = PreparedActionStore(clock=MutableClock())
                with self.assertRaises(GroundingError):
                    store.prepare(
                        actor_id="actor-a",
                        session_id="session-a",
                        grounded=grounded,
                        registry=two_lights,
                        adapter=adapter,
                        clarification_answer="Turn off Study light.",
                        confirmed_instruction=confirmed_study,
                    )
                self.assertEqual(adapter.sut_calls, [])

        contrastive = ground_domux_request(
            "Turn off not the Study light but the Living Room light.",
            "turnOff|Light|*|*|*|Living Room|Ground Floor",
            two_lights,
        )
        self.assertEqual(contrastive.negated_entity_ids, ("light.study",))
        resolved_contrastive = resolve_clarification_submission(
            contrastive,
            answer="Not the Study light; use the Living Room light.",
            confirmed_instruction=DomuxInstruction(
                "turnOff", "Light", "*", "*", "*", "Living Room", "Ground Floor"
            ),
            registry=two_lights,
        )
        self.assertEqual(resolved_contrastive.chosen.entity_id, "light.living")

        named_singleton = EntityRegistry((
            EntitySpec(
                "light.ceiling", "light", "Ceiling Light", "Study", "Ground Floor"
            ),
        ))
        other = ground_domux_request(
            "Turn other Ceiling Light off.",
            "turnOff|Ceiling Light|*|*|*|*|*",
            named_singleton,
        )
        self.assertTrue(other.clarification.required)
        self.assertIn("other_reference_requires_selection", other.clarification.reasons)

        for device in ("Lights", "Lamps", "Devices", "ACs"):
            with self.subTest(other_device=device):
                plural_registry = EntityRegistry((
                    EntitySpec(
                        f"light.{device.casefold()}",
                        "light",
                        device,
                        "Study",
                        "Ground Floor",
                    ),
                ))
                plural_other = ground_domux_request(
                    f"Turn other {device.casefold()} off.",
                    f"turnOff|{device}|*|*|*|*|*",
                    plural_registry,
                )
                self.assertTrue(plural_other.clarification.required)
                self.assertIn(
                    "other_reference_requires_selection",
                    plural_other.clarification.reasons,
                )

        for utterance in (
            "Turn off a light, but not the light—Study light.",
            "Turn off a light, but not the light that you and I talked about in Study.",
            "Turn off no light.",
        ):
            with self.subTest(unresolved_exclusion=utterance):
                unresolved_exclusion = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|*|*",
                    two_lights,
                )
                self.assertTrue(unresolved_exclusion.clarification.required)
                self.assertTrue(
                    {
                        "unsupported_request_grammar",
                        "negated_selector",
                    }.intersection(unresolved_exclusion.clarification.reasons)
                )
                with self.assertRaisesRegex(
                    GroundingError,
                    "new immediate command|explicitly excluded entity|does not select a candidate",
                ):
                    resolve_clarification_submission(
                        unresolved_exclusion,
                        answer="Yes.",
                        confirmed_instruction=DomuxInstruction(
                            "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
                        ),
                        registry=two_lights,
                    )

    def test_unanchored_inventory_words_never_fall_back_to_a_global_singleton(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
            EntitySpec("cover.kitchen", "cover", "Curtain", "Kitchen", "Ground Floor"),
        ))
        utterances = (
            "Turn off the light for Kitchen.",
            "For Kitchen, turn off the light.",
            "Turn off the light of Kitchen.",
            "Turn off the light by Kitchen.",
            "Turn off the light about Kitchen.",
            "Turn off the light around Kitchen.",
        )
        for utterance in utterances:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|*|*",
                    registry,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("room", grounded.clarification.unresolved_slots)

    def test_command_vocabulary_metadata_collisions_require_explicit_selector_syntax(self) -> None:
        collision_registry = EntityRegistry((
            EntitySpec(
                "light.brightness",
                "light",
                "Brightness",
                "The",
                "First",
                ("Mode",),
            ),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        cases = (
            (
                "Set the light brightness to 50 percent.",
                "set|Light|brightness|50|Percent|*|*",
            ),
            ("Turn the light off.", "turnOff|Light|*|*|*|*|*"),
            ("First, turn the light off.", "turnOff|Light|*|*|*|*|*"),
        )
        for utterance, raw_output in cases:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    raw_output,
                    collision_registry,
                )
                self.assertTrue(grounded.clarification.required)

        explicit = ground_domux_request(
            "Set the device named Brightness in the room named The to 50 percent brightness.",
            "set|Brightness|brightness|50|Percent|The|*",
            collision_registry,
        )
        self.assertTrue(explicit.clarification.required)
        self.assertEqual(
            tuple(entity.entity_id for entity in explicit.candidates),
            ("light.brightness",),
        )

        affirmative_aliases = EntityRegistry((
            EntitySpec(
                "light.living",
                "light",
                "Light",
                "Living Room",
                "Ground Floor",
                ("Yes", "Proceed"),
            ),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        ambiguous = ground_domux_request(
            "Turn off the light.",
            "turnOff|Light|*|*|*|*|*",
            affirmative_aliases,
        )
        self.assertEqual(len(ambiguous.candidates), 2)
        for answer in ("Yes.", "Proceed."):
            with self.subTest(answer=answer), self.assertRaisesRegex(
                GroundingError, "does not select"
            ):
                resolve_clarification_submission(
                    ambiguous,
                    answer=answer,
                    confirmed_instruction=DomuxInstruction(
                        "turnOff", "Light", "*", "*", "*", "Living Room", "Ground Floor"
                    ),
                    registry=affirmative_aliases,
                )

        ordinal_registry = EntityRegistry((
            EntitySpec("light.first", "light", "Light", "First", "Ground Floor"),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        for utterance in (
            "First, turn the light off.",
            "First: turn the light off.",
            "Turn the first light off.",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|*|*",
                    ordinal_registry,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("room", grounded.clarification.unresolved_slots)

    def test_questions_wait_prefixes_and_action_alternatives_never_execute(self) -> None:
        raw = "turnOff|Light|*|*|*|Study|Ground Floor"
        for utterance in (
            "Do you want to turn off the Study light?",
            "Do I turn off the Study light?",
            "Did you turn off the Study light?",
            "Do we turn off the Study light?",
            "Did we turn off the Study light?",
            "Can we turn off the Study light?",
            "Could we turn off the Study light?",
            "Would we turn off the Study light?",
            "Should we turn off the Study light?",
            "Do you need to turn off the Study light?",
            "Do you have to turn off the Study light?",
            "Would you want to turn off the Study light?",
            "Can you mean to turn off the Study light?",
            "We did turn off the Study light.",
            "I did turn off the Study light.",
            "You did turn off the Study light.",
            "We do turn off the Study light.",
            "I do turn off the Study light.",
            "You do turn off the Study light.",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertIn("informational_request", grounded.clarification.reasons)

        # These conventional second-person modal forms remain supported as
        # polite imperatives; adding a meta verb above changes them to a query.
        for utterance in (
            "Can you turn off the Study light?",
            "Could you turn off the Study light?",
            "Would you turn off the Study light?",
        ):
            with self.subTest(polite_imperative=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertNotIn("informational_request", grounded.clarification.reasons)

        waited = ground_domux_request(
            "Wait, then turn off the Study light.",
            raw,
            self.registry,
        )
        self.assertIn("unsupported_condition_or_time", waited.clarification.reasons)

        for utterance in (
            "From now on, turn the Study light off.",
            "Now and then, turn the Study light off.",
        ):
            with self.subTest(persistent_or_periodic=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertIn(
                    "unsupported_condition_or_time",
                    grounded.clarification.reasons,
                )

        alternative = ground_domux_request(
            "Turn the Study light on or off; confirm.",
            raw,
            self.registry,
        )
        self.assertIn("action", alternative.clarification.unresolved_slots)
        with self.assertRaises(GroundingError):
            resolve_clarification_submission(
                alternative,
                answer="Yes.",
                confirmed_instruction=DomuxInstruction(
                    "turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"
                ),
                registry=self.registry,
            )

    def test_model_selector_slots_cannot_launder_conditions_into_the_grammar(self) -> None:
        modifiers = (
            "subject to nobody being home",
            "depending on whether anyone is home",
            "so long as the room is empty",
            "during dinner",
            "upon arrival",
            "at dusk",
            "momentarily",
            "as needed",
        )
        for modifier in modifiers:
            utterance = f"Turn the Study light off {modifier}."
            for slot in ("device", "room", "floor"):
                fields = ["turnOff", "Light", "*", "*", "*", "Study", "Ground Floor"]
                fields[{"device": 1, "room": 5, "floor": 6}[slot]] = modifier
                with self.subTest(modifier=modifier, slot=slot):
                    grounded = ground_domux_request(utterance, "|".join(fields), self.registry)
                    self.assertTrue(grounded.clarification.required)
                    self.assertTrue({
                        "unsupported_condition_or_time", "unsupported_request_grammar",
                    }.intersection(grounded.clarification.reasons))
                    with self.assertRaises(GroundingError):
                        resolve_clarification_submission(
                            grounded,
                            answer="The Study light on the Ground Floor.",
                            confirmed_instruction=DomuxInstruction(
                                "turnOff", "Ceiling Light", "*", "*", "*",
                                "Study", "Ground Floor",
                            ),
                            registry=self.registry,
                        )

    def test_selector_slot_text_does_not_change_grammar_support(self) -> None:
        utterance = "Turn the Study light off subject to nobody being home."
        raw_outputs = (
            "turnOff|Light|*|*|*|Study|Ground Floor",
            "turnOff|subject to nobody being home|*|*|*|Study|Ground Floor",
            "turnOff|Light|*|*|*|subject to nobody being home|Ground Floor",
            "turnOff|Light|*|*|*|Study|subject to nobody being home",
        )
        reasons = []
        for raw_output in raw_outputs:
            grounded = ground_domux_request(utterance, raw_output, self.registry)
            reasons.append("unsupported_request_grammar" in grounded.clarification.reasons)
        self.assertEqual(reasons, [True, True, True, True])

    def test_unique_resolution_rechecks_non_wildcard_room_and_floor(self) -> None:
        grounded = ground_domux_request(
            "Turn off the Study ceiling light on the Ground Floor.",
            "turnOff|Ceiling Light|*|*|*|Study|Ground Floor",
            self.registry,
        )
        self.assertFalse(grounded.clarification.required)
        for slot, wrong in (("room", "Kitchen"), ("floor", "First Floor")):
            source = replace(grounded.source_instructions[0], **{slot: wrong})
            tampered = replace(grounded, source_instructions=(source,))
            with self.subTest(slot=slot), self.assertRaisesRegex(
                GroundingError, f"unique request {slot}"
            ):
                resolve_unique_request(tampered, self.registry)

    def test_target_only_answer_cannot_repair_a_model_missed_operation(self) -> None:
        context = SessionContext(("cover.study_curtain",))
        grounded = ground_domux_request(
            "Open that one to 60 percent.",
            "set|Light|brightness|60|Percent|*|*",
            self.registry,
            context,
        )
        self.assertEqual(
            tuple(entity.entity_id for entity in grounded.candidates),
            ("cover.study_curtain",),
        )
        with self.assertRaisesRegex(GroundingError, "answer-supported patch"):
            resolve_clarification_submission(
                grounded,
                answer="The Study curtain on the Ground Floor.",
                confirmed_instruction=DomuxInstruction(
                    "set", "Curtain", "position", "60", "Percent", "Study", "Ground Floor"
                ),
                registry=self.registry,
            )

    def test_zero_and_ambiguous_answers_fail(self) -> None:
        candidates = self.registry.candidates(parse_domux_output("turnOff|Ceiling Light|*|*|*|*|*")[0])
        for answer in ("", "Kitchen", "Ceiling Light", "9"):
            with self.subTest(answer=answer), self.assertRaises(GroundingError):
                resolve_clarification(answer, candidates)

    def test_plan_mappings_cover_all_three_domains(self) -> None:
        adapter = InMemoryHAAdapter(self.states)
        cases = (
            ("turnOff|Ceiling Light|*|*|*|Study|Ground Floor", "light.study_ceiling", "turn_off"),
            ("set|Curtain|position|30|Percent|Study|Ground Floor", "cover.study_curtain", "set_cover_position"),
            ("set|AC|temperature|23|Celsius|Study|Ground Floor", "climate.study_ac", "set_temperature"),
        )
        for raw, entity_id, service in cases:
            entity = self.registry.get(entity_id)
            plan = build_plan(parse_domux_output(raw)[0], entity, adapter.get_state(entity_id))
            self.assertEqual(plan.service, service)

    def test_light_brightness_zero_uses_home_assistant_turn_off_semantics(self) -> None:
        entity = self.registry.get("light.study_ceiling")
        adapter = InMemoryHAAdapter(self.states)
        cases = (
            "set|Light|brightness|0|Percent|Study|Ground Floor",
            "adjustDown|Light|brightness|60|Percent|Study|Ground Floor",
        )
        for raw in cases:
            with self.subTest(raw=raw):
                before = adapter.get_state(entity.entity_id)
                before["attributes"]["brightness"] = 153
                before["state"] = "on"
                adapter.set_state_for_setup(entity.entity_id, before)
                plan = build_plan(parse_domux_output(raw)[0], entity, before)
                self.assertEqual(plan.service, "turn_on")
                self.assertEqual(plan.service_data["brightness_pct"], 0)
                self.assertEqual(plan.expected_projection["state"], "off")
                self.assertIsNone(plan.expected_projection["brightness"])
                result = adapter.call_service(plan.domain, plan.service, plan.service_data)
                self.assertEqual(controlled_projection(result.after, "light")["state"], "off")
                self.assertTrue(projection_matches(
                    controlled_projection(result.after, "light"),
                    plan.expected_projection,
                ))

        positive = build_plan(
            parse_domux_output(
                "set|Light|brightness|1|Percent|Study|Ground Floor"
            )[0],
            entity,
            adapter.get_state(entity.entity_id),
        )
        self.assertEqual(positive.expected_projection["state"], "on")
        self.assertEqual(positive.expected_projection["brightness"], 3)

    def test_home_assistant_integer_coercions_and_optional_cover_position_are_bound(self) -> None:
        cover = self.registry.get("cover.study_curtain")
        cover_state = json.loads(json.dumps(self.states[cover.entity_id]))
        integral = build_plan(
            parse_domux_output(
                "set|Curtain|position|21|Percent|Study|Ground Floor"
            )[0],
            cover,
            cover_state,
        )
        self.assertEqual(integral.service_data["position"], 21)
        self.assertIsInstance(integral.service_data["position"], int)
        for raw in (
            "set|Curtain|position|20.9|Percent|Study|Ground Floor",
            "adjustUp|Curtain|position|10.5|Percent|Study|Ground Floor",
        ):
            with self.subTest(raw=raw), self.assertRaisesRegex(GroundingError, "integer"):
                build_plan(parse_domux_output(raw)[0], cover, cover_state)

        without_position = json.loads(json.dumps(cover_state))
        without_position["attributes"].pop("current_position")
        for raw, expected_state in (
            ("turnOn|Curtain|*|*|*|Study|Ground Floor", "open"),
            ("turnOff|Curtain|*|*|*|Study|Ground Floor", "closed"),
        ):
            with self.subTest(raw=raw):
                plan = build_plan(parse_domux_output(raw)[0], cover, without_position)
                self.assertEqual(plan.expected_projection, {
                    "entity_id": cover.entity_id,
                    "state": expected_state,
                })
        with self.assertRaisesRegex(GroundingError, "observed current_position"):
            build_plan(
                parse_domux_output(
                    "adjustUp|Curtain|position|10|Percent|Study|Ground Floor"
                )[0],
                cover,
                without_position,
            )

        light = self.registry.get("light.study_ceiling")
        light_state = json.loads(json.dumps(self.states[light.entity_id]))
        light_state["attributes"]["supported_color_modes"] = ["white"]
        for raw in (
            "set|Light|brightness|25|Percent|Study|Ground Floor",
            "adjustUp|Light|brightness|5|Percent|Study|Ground Floor",
        ):
            with self.subTest(raw=raw):
                plan = build_plan(parse_domux_output(raw)[0], light, light_state)
                self.assertEqual(plan.service, "turn_on")

        light_state["attributes"]["supported_color_modes"] = ["color_temp"]
        with self.assertRaisesRegex(GroundingError, "integer Kelvin"):
            build_plan(
                parse_domux_output(
                    "set|Light|colorTemperature|3000.9|Kelvin|Study|Ground Floor"
                )[0],
                light,
                light_state,
            )

    def test_climate_turn_off_prefers_advertised_off_mode_over_feature_gated_service(self) -> None:
        entity = self.registry.get("climate.study_ac")
        instruction = parse_domux_output(
            "turnOff|AC|*|*|*|Study|Ground Floor"
        )[0]
        state = json.loads(json.dumps(self.states[entity.entity_id]))
        self.assertEqual(state["attributes"]["supported_features"], 9)
        plan = build_plan(instruction, entity, state)
        self.assertEqual(plan.service, "set_hvac_mode")
        self.assertEqual(plan.service_data["hvac_mode"], "off")
        adapter = InMemoryHAAdapter({entity.entity_id: state})
        result = adapter.call_service(plan.domain, plan.service, plan.service_data)
        self.assertTrue(projection_matches(
            controlled_projection(result.after, "climate"),
            plan.expected_projection,
        ))

        state["attributes"]["hvac_modes"] = ["cool", "heat"]
        state["attributes"]["supported_features"] = 128
        feature_plan = build_plan(instruction, entity, state)
        self.assertEqual(feature_plan.service, "turn_off")
        self.assertNotIn("hvac_mode", feature_plan.service_data)

        state["attributes"]["supported_features"] = 0
        with self.assertRaisesRegex(GroundingError, "turn-off support"):
            build_plan(instruction, entity, state)

    def test_plan_rejects_noncanonical_units_and_unused_slots(self) -> None:
        adapter = InMemoryHAAdapter(self.states)
        entity = self.registry.get("light.study_ceiling")
        invalid = (
            "set|Light|brightness|30|Kelvin|Study|Ground Floor",
            "turnOff|Light|brightness|30|Percent|Study|Ground Floor",
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(GroundingError):
                build_plan(parse_domux_output(raw)[0], entity, adapter.get_state(entity.entity_id))

    def test_climate_turn_on_requires_one_unambiguous_active_mode(self) -> None:
        adapter = InMemoryHAAdapter(self.states)
        off = adapter.get_state("climate.study_ac")
        off["state"] = "off"
        adapter.set_state_for_setup("climate.study_ac", off)
        instruction = parse_domux_output("turnOn|AC|*|*|*|Study|Ground Floor")[0]
        with self.assertRaisesRegex(GroundingError, "confirm a mode explicitly"):
            build_plan(instruction, self.registry.get("climate.study_ac"), off)
        off["attributes"]["hvac_modes"] = ["off", "heat"]
        plan = build_plan(instruction, self.registry.get("climate.study_ac"), off)
        self.assertEqual(plan.expected_projection["state"], "heat")
        receipt = adapter.call_service(plan.domain, plan.service, plan.service_data)
        self.assertEqual(receipt.after["state"], "heat")

    def test_climate_preserves_advertised_underscore_enums_and_temperature_step(self) -> None:
        state = json.loads(json.dumps(self.states["climate.study_ac"]))
        state["attributes"]["hvac_modes"] = ["off", "fan_only", "heat_cool"]
        entity = self.registry.get("climate.study_ac")
        fan_only = build_plan(
            parse_domux_output("set|AC|mode|Fan|*|Study|Ground Floor")[0], entity, state,
        )
        self.assertEqual(fan_only.service_data["hvac_mode"], "fan_only")
        fan_speed = build_plan(
            parse_domux_output("set|AC|fan speed|medium_high|Level|Study|Ground Floor")[0],
            entity,
            state,
        )
        self.assertEqual(fan_speed.service_data["fan_mode"], "medium_high")
        with self.assertRaisesRegex(GroundingError, "does not align"):
            build_plan(
                parse_domux_output("set|AC|temperature|23.3|Celsius|Study|Ground Floor")[0],
                entity,
                state,
            )
        accepted = build_plan(
            parse_domux_output("set|AC|temperature|23.5|Celsius|Study|Ground Floor")[0],
            entity,
            state,
        )
        self.assertEqual(accepted.service_data["temperature"], 23.5)
        with self.assertRaisesRegex(GroundingError, "does not align"):
            build_plan(
                parse_domux_output("adjustUp|AC|temperature|0.3|Celsius|Study|Ground Floor")[0],
                entity,
                state,
            )
        adjusted = build_plan(
            parse_domux_output("adjustUp|AC|temperature|0.5|Celsius|Study|Ground Floor")[0],
            entity,
            state,
        )
        self.assertEqual(adjusted.service_data["temperature"], 24.5)

    def test_projection_ignores_volatile_home_assistant_fields(self) -> None:
        raw = {
            "entity_id": "light.study_ceiling",
            "state": "on",
            "attributes": {"brightness": 153, "friendly_name": "private"},
            "last_changed": "volatile",
            "context": {"id": "volatile"},
        }
        self.assertEqual(
            controlled_projection(raw, "light"),
            {"entity_id": "light.study_ceiling", "state": "on", "brightness": 153},
        )
        planned = planning_projection(self.states["light.study_ceiling"], "light")
        self.assertIn("supported_color_modes", planned)

    def test_user_words_cannot_be_dropped_or_reversed_by_the_model(self) -> None:
        cases = (
            (
                "Turn off the Living Room light on the Ground Floor.",
                "turnOn|Light|*|*|*|Living Room|Ground Floor",
                "action",
            ),
            (
                "Open the Study curtain to 20 percent.",
                "turnOn|Curtain|*|*|*|Study|Ground Floor",
                "value",
            ),
            (
                "Set the Study AC to 23 Celsius.",
                "turnOn|AC|*|*|*|Study|Ground Floor",
                "action",
            ),
        )
        for utterance, raw, missing in cases:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn(missing, grounded.clarification.unresolved_slots)

    def test_negated_target_and_cancelled_answers_fail_closed(self) -> None:
        grounded = ground_domux_request(
            "Turn off the Living Room light, not the Study light.",
            "turnOff|Light|*|*|*|Study|Ground Floor",
            self.registry,
        )
        confirmed = parse_domux_output(
            "turnOff|Ceiling Light|*|*|*|Study|Ground Floor"
        )[0]
        with self.assertRaisesRegex(GroundingError, "excluded"):
            resolve_clarification_submission(
                grounded,
                answer="The Study light.",
                confirmed_instruction=confirmed,
                registry=self.registry,
            )

        uncertain = ground_domux_request(
            "Maybe turn off the Study light.",
            "turnOff|Ceiling Light|*|*|*|Study|Ground Floor",
            self.registry,
        )
        for answer in (
            "no thanks", "actually no", "cancel that", "never mind please", "not Study",
            "I do not know", "still not sure", "whatever", "maybe", "please ask me later", "banana",
        ):
            with self.subTest(answer=answer), self.assertRaises(GroundingError):
                resolve_clarification_submission(
                    uncertain,
                    answer=answer,
                    confirmed_instruction=confirmed,
                    registry=self.registry,
                )

    def test_excluded_operation_values_require_a_positive_safe_replacement(self) -> None:
        cases = (
            (
                "Set Study light to anything but Blue.",
                "set|Light|color|Blue|*|Study|Ground Floor",
            ),
            (
                "Set Study light to something other than Blue.",
                "set|Light|color|Blue|*|Study|Ground Floor",
            ),
            (
                "Don't use Blue for Study light.",
                "set|Light|color|Blue|*|Study|Ground Floor",
            ),
            (
                "Set the Study AC to anything but Heat mode.",
                "set|AC|mode|Heat|*|Study|Ground Floor",
            ),
            (
                "Set brightness to any value other than 20% for the Study light.",
                "set|Light|brightness|20|Percent|Study|Ground Floor",
            ),
            (
            "Set color to avoid Blue for the Study light.",
                "set|Light|color|Blue|*|Study|Ground Floor",
            ),
            (
                "Make the Study light any color besides Blue.",
                "set|Light|color|Blue|*|Study|Ground Floor",
            ),
            (
                "Make the Study light any color apart from Blue.",
                "set|Light|color|Blue|*|Study|Ground Floor",
            ),
        )
        for utterance, raw in cases:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertIn("excluded_operation_value", grounded.clarification.reasons)
                self.assertIn("value", grounded.clarification.unresolved_slots)
                for answer in ("Yes.", "The Study light."):
                    with self.assertRaisesRegex(GroundingError, "excluded"):
                        resolve_clarification_submission(
                            grounded,
                            answer=answer,
                            confirmed_instruction=parse_domux_output(raw)[0],
                            registry=self.registry,
                        )

        grounded = ground_domux_request(cases[0][0], cases[0][1], self.registry)
        safe = parse_domux_output(
            "set|Light|color|Red|*|Study|Ground Floor"
        )[0]
        resolved = resolve_clarification_submission(
            grounded,
            answer="Use Red instead for the Study light.",
            confirmed_instruction=safe,
            registry=self.registry,
        )
        self.assertEqual(resolved.confirmed_instruction.value, "Red")

    def test_withdrawn_initial_requests_and_deferred_answers_cannot_execute(self) -> None:
        raw = "turnOff|Ceiling Light|*|*|*|Study|Ground Floor"
        for utterance in (
            "I don't want you to turn off the Study light.",
            "No need to turn off the Study light.",
            "Turn off the Study light, just kidding.",
            "Turn off the Study light—actually, never mind.",
            "Please refrain from turning off the Study light.",
            "Turn off the Study light, scratch that.",
            "Turn off the Study light, I changed my mind.",
            "Turn off the Study light, hold on.",
            "Dont turn off the Study light.",
            "Don’t turn off the Study light.",
            "Never ever turn off the Study light.",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertIn("negative_or_cancelled_intent", grounded.clarification.reasons)
                with self.assertRaisesRegex(GroundingError, "negated"):
                    resolve_clarification_submission(
                        grounded,
                        answer="Yes.",
                        confirmed_instruction=parse_domux_output(raw)[0],
                        registry=self.registry,
                    )

        ambiguous = ground_domux_request(
            "Turn off the ceiling light.",
            "turnOff|Ceiling Light|*|*|*|*|*",
            self.registry,
        )
        confirmed = parse_domux_output(raw)[0]
        for answer in (
            "Study, leave it on.",
            "Study, keep it on.",
            "Study, not now.",
            "Study, wait.",
            "Study, please refrain from turning it off.",
            "The Study one, but don't do it yet.",
            "Study, forget it.",
            "Study, I changed my mind.",
            "Study, not anymore.",
            "Study, skip it.",
            "Study, don't.",
            "Study, do not.",
            "Study, abort it.",
            "Study, disregard that.",
            "Study, ignore that.",
            "Study, leave it alone.",
            "Study, no longer.",
            "Study, on second thought no.",
            "Study, postpone it.",
            "Study, defer it.",
            "Study, pause.",
            "Study, not yet.",
            "Study, don't touch it.",
            "Study, I don't want that.",
            "Study, no thanks.",
            "Study, scratch that.",
            "Study, nix that.",
            "Study, banana.",
            "Study, don't go ahead.",
            "Study, rather not proceed.",
            "Study, do not go ahead.",
            "Study, I do not want to proceed.",
            "Study, I don't want you to turn it off.",
            "Study, not turn it off.",
            "Study, I don't need you to turn it off.",
            "Study, I withdraw permission to turn it off.",
            "Study, I revoke authorization to turn it off.",
            "Study, I refuse to let you turn it off.",
            "Study, you must not turn it off.",
            "Study, you may not turn it off.",
            "Study, don't you turn it off.",
            "Study, I forbid you to turn it off.",
            "Study, under no circumstances turn it off.",
            "Use Study. Turn off the light if nobody is home.",
            "Use Study. Turn off the light at nine.",
            "Use Study. Turn off the light provided nobody is home.",
            "Use Study. Turn off the light, scratch that.",
            "Use Study, do not confirm.",
            "Use Study, don’t execute it.",
            "Study, should I turn it off?",
            "Study, can I turn it off?",
            "Study, would it be safe to turn it off?",
            "Study, is it okay to turn it off?",
            "Study, do you recommend I turn it off?",
            "Study, tell me whether to turn it off.",
            "Study, why should I turn it off?",
            "Study, are you going to turn it off?",
            "Use any device except Study. Turn it off.",
            "Not in Study; turn the light off.",
        ):
            with self.subTest(answer=answer), self.assertRaises(GroundingError):
                resolve_clarification_submission(
                    ambiguous,
                    answer=answer,
                    confirmed_instruction=confirmed,
                    registry=self.registry,
                )
        for answer in ("Study.", "The one in the Study.", "Study, turn it off now."):
            with self.subTest(answer=answer):
                self.assertEqual(
                    resolve_clarification_submission(
                        ambiguous,
                        answer=answer,
                        confirmed_instruction=confirmed,
                        registry=self.registry,
                    ).chosen.entity_id,
                    "light.study_ceiling",
                )

    def test_clarification_answer_cannot_introduce_exclusions_or_alternatives(self) -> None:
        grounded = ground_domux_request(
            "Change the Study Ceiling Light color.",
            "set|Ceiling Light|color|*|*|Study|Ground Floor",
            self.registry,
        )
        blue = parse_domux_output(
            "set|Ceiling Light|color|Blue|*|Study|Ground Floor"
        )[0]
        for answer in (
            "Anything besides Blue.",
            "Anything apart from Blue.",
            "Use either Red or Blue.",
        ):
            with self.subTest(answer=answer), self.assertRaises(GroundingError):
                resolve_clarification_submission(
                    grounded,
                    answer=answer,
                    confirmed_instruction=blue,
                    registry=self.registry,
                )

    def test_clarification_cannot_add_an_operation_that_the_plan_drops(self) -> None:
        grounded = ground_domux_request(
            "Turn off the Ceiling Light.",
            "turnOff|Ceiling Light|*|*|*|*|*",
            self.registry,
        )
        confirmed = parse_domux_output(
            "turnOff|Ceiling Light|*|*|*|Study|Ground Floor"
        )[0]
        for answer in ("Study, make it Blue.", "Study, set brightness to 50 percent."):
            with self.subTest(answer=answer), self.assertRaises(GroundingError):
                resolve_clarification_submission(
                    grounded,
                    answer=answer,
                    confirmed_instruction=confirmed,
                    registry=self.registry,
                )

        uncertain = ground_domux_request(
            "Maybe turn off the Study light.",
            "turnOff|Ceiling Light|*|*|*|Study|Ground Floor",
            self.registry,
        )
        with self.assertRaises(GroundingError):
            resolve_clarification_submission(
                uncertain,
                answer="Make it Blue.",
                confirmed_instruction=confirmed,
                registry=self.registry,
            )
        for answer in (
            "Study, maybe", "I do not know, Study?", "Study perhaps",
            "Study, but I am not sure", "whatever, Study",
        ):
            with self.subTest(answer=answer), self.assertRaises(GroundingError):
                resolve_clarification_submission(
                    grounded,
                    answer=answer,
                    confirmed_instruction=confirmed,
                    registry=self.registry,
                )

    def test_named_room_is_not_misread_as_an_operational_color_or_mode(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.orange", "light", "Light", "Orange Room", "Ground Floor"),
            EntitySpec("climate.heat_room", "climate", "AC", "Heat Room", "Ground Floor"),
        ))
        grounded = ground_domux_request(
            "Turn off the Orange Room light.",
            "turnOff|Light|*|*|*|Orange Room|*",
            registry,
        )
        self.assertFalse(grounded.clarification.required)
        heat_room = ground_domux_request(
            "Turn off the Heat Room AC.",
            "turnOff|AC|*|*|*|Heat Room|*",
            registry,
        )
        self.assertFalse(heat_room.clarification.required)
        named_registry = EntityRegistry((
            EntitySpec("light.blue", "light", "Light", "Blue Room", "Ground Floor"),
            EntitySpec("light.study", "light", "Light", "Study", "Ground Floor"),
        ))
        named = ground_domux_request(
            "Turn off the light.", "turnOff|Light|*|*|*|*|*", named_registry,
        )
        resolved = resolve_clarification_submission(
            named,
            answer="Blue Room",
            confirmed_instruction=parse_domux_output(
                "turnOff|Light|*|*|*|Blue Room|Ground Floor"
            )[0],
            registry=named_registry,
        )
        self.assertEqual(resolved.chosen.entity_id, "light.blue")

    def test_opposing_action_clauses_cannot_collapse_to_one_model_action(self) -> None:
        for utterance in (
            "Turn on the Study light then switch that device off.",
            "Turn on the Study light, then turn the Study light off.",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOn|Ceiling Light|*|*|*|Study|Ground Floor",
                    self.registry,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("action", grounded.clarification.unresolved_slots)

    def test_source_values_ranges_and_multi_operation_text_fail_closed(self) -> None:
        cases = (
            (
                "Change the Study light brightness from 50 to 20 percent.",
                "set|Light|brightness|50|Percent|Study|*",
                "value",
            ),
            (
                "Set the Study light brightness between 20 and 50 percent.",
                "set|Light|brightness|50|Percent|Study|*",
                "value",
            ),
            (
                "Change the Study light color from Red to Blue.",
                "set|Light|color|Red|*|Study|*",
                "value",
            ),
            (
                "Use Heat then Cool mode on the Study AC.",
                "set|AC|mode|Heat|*|Study|*",
                "value",
            ),
            (
                "Open the Study curtain halfway, then close it.",
                "set|Curtain|position|50|Percent|Study|*",
                "action",
            ),
            (
                "Close the Study curtain, then open it to 20 percent.",
                "set|Curtain|position|20|Percent|Study|*",
                "action",
            ),
            (
                "Set the Study AC to Cool mode at 20.",
                "set|AC|mode|Cool|*|Study|*",
                "value",
            ),
        )
        for utterance, raw, slot in cases:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn(slot, grounded.clarification.unresolved_slots)

        target = ground_domux_request(
            "Change the Study light brightness from 50 to 20 percent.",
            "set|Light|brightness|20|Percent|Study|*",
            self.registry,
        )
        color_target = ground_domux_request(
            "Change the Study light color from Red to Blue.",
            "set|Light|color|Blue|*|Study|*",
            self.registry,
        )
        self.assertFalse(target.clarification.required)
        self.assertFalse(color_target.clarification.required)

    def test_explicit_temperature_unit_cannot_be_silently_changed(self) -> None:
        for unit in ("Fahrenheit", "°F", "Kelvin", "K"):
            with self.subTest(unit=unit):
                grounded = ground_domux_request(
                    f"Set the Study AC temperature to 20 {unit}.",
                    "set|AC|temperature|20|Celsius|Study|*",
                    self.registry,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("unit", grounded.clarification.unresolved_slots)

    def test_conflicted_action_and_unit_can_converge_after_explicit_clarification(self) -> None:
        action_grounded = ground_domux_request(
            "Open the Study curtain halfway, then close it.",
            "set|Curtain|position|50|Percent|Study|*",
            self.registry,
        )
        action_resolved = resolve_clarification_submission(
            action_grounded,
            answer="Close the Study curtain.",
            confirmed_instruction=parse_domux_output(
                "turnOff|Curtain|*|*|*|Study|Ground Floor"
            )[0],
            registry=self.registry,
        )
        self.assertEqual(action_resolved.confirmed_instruction.action, "turnOff")

        unit_grounded = ground_domux_request(
            "Set the Study AC temperature to 20 degrees Fahrenheit.",
            "set|AC|temperature|20|Celsius|Study|*",
            self.registry,
        )
        unit_resolved = resolve_clarification_submission(
            unit_grounded,
            answer="Use 20 Celsius for the Study AC instead.",
            confirmed_instruction=parse_domux_output(
                "set|AC|temperature|20|Celsius|Study|Ground Floor"
            )[0],
            registry=self.registry,
        )
        self.assertEqual(unit_resolved.confirmed_instruction.unit, "Celsius")

    def test_compound_effects_and_multiple_attributes_cannot_collapse(self) -> None:
        cases = (
            (
                "Turn off the Study light and make it Blue.",
                "set|Light|color|Blue|*|Study|*",
                "action",
            ),
            (
                "Turn off the Study light and set brightness to 20 percent.",
                "set|Light|brightness|20|Percent|Study|*",
                "action",
            ),
            (
                "Close the Study curtain and set position to 20 percent.",
                "set|Curtain|position|20|Percent|Study|*",
                "action",
            ),
            (
                "Turn off the Study AC and use Cool mode.",
                "set|AC|mode|Cool|*|Study|*",
                "action",
            ),
            (
                "Turn off the Study light and make it brighter.",
                "adjustUp|Light|brightness|10|Percent|Study|*",
                "action",
            ),
            (
                "Close the Study curtain and raise it.",
                "adjustUp|Curtain|position|10|Percent|Study|*",
                "action",
            ),
            (
                "Set the Study light brightness and color to Blue.",
                "set|Light|color|Blue|*|Study|*",
                "attribute",
            ),
            (
                "Make the Study AC warmer and use Cool mode.",
                "set|AC|mode|Cool|*|Study|*",
                "attribute",
            ),
            (
                "Set the Study AC wind speed High and use Cool mode.",
                "set|AC|mode|Cool|*|Study|*",
                "attribute",
            ),
        )
        for utterance, raw, slot in cases:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn(slot, grounded.clarification.unresolved_slots)

        compatible = ground_domux_request(
            "Turn on the Study light and make it Blue.",
            "set|Light|color|Blue|*|Study|*",
            self.registry,
        )
        self.assertFalse(compatible.clarification.required)

    def test_explicit_multi_targets_cannot_be_truncated_to_one_tuple(self) -> None:
        cases = (
            (
                "Turn off the Living Room light and the Study light.",
                "turnOff|Light|*|*|*|Living Room|*",
                {"light.living_ceiling", "light.study_ceiling"},
            ),
            (
                "Turn off the Study light and AC.",
                "turnOff|Light|*|*|*|Study|*",
                {"light.study_ceiling", "climate.study_ac"},
            ),
            (
                "Turn on the Study light and open the curtain.",
                "turnOn|Light|*|*|*|Study|*",
                {"light.study_ceiling", "cover.study_curtain"},
            ),
        )
        for utterance, raw, expected in cases:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertTrue(expected.issubset({item.entity_id for item in grounded.candidates}))

    def test_nested_room_names_select_only_the_longest_explicit_span(self) -> None:
        nested = EntityRegistry((
            EntitySpec("light.guest", "light", "Light", "Guest Bedroom", "Ground Floor"),
            EntitySpec("light.bedroom", "light", "Light", "Bedroom", "Ground Floor"),
            EntitySpec("light.east_hall", "light", "Light", "East Hall", "First Floor"),
            EntitySpec("light.hall", "light", "Light", "Hall", "First Floor"),
        ))
        for room, entity_id in (
            ("Guest Bedroom", "light.guest"),
            ("East Hall", "light.east_hall"),
        ):
            with self.subTest(room=room):
                grounded = ground_domux_request(
                    f"Turn off the {room} light.",
                    f"turnOff|Light|*|*|*|{room}|*",
                    nested,
                )
                self.assertFalse(grounded.clarification.required)
                self.assertEqual(grounded.candidates[0].entity_id, entity_id)

        both = ground_domux_request(
            "Turn off the Guest Bedroom light and the Bedroom light.",
            "turnOff|Light|*|*|*|Guest Bedroom|*",
            nested,
        )
        self.assertTrue(both.clarification.required)
        self.assertTrue({"light.guest", "light.bedroom"}.issubset(
            {entity.entity_id for entity in both.candidates}
        ))

    def test_numeric_specific_device_labels_are_not_operation_values(self) -> None:
        numeric = EntityRegistry((
            EntitySpec("light.lamp_2", "light", "Lamp 2", "Study", "Ground Floor"),
            EntitySpec("cover.curtain_2", "cover", "Curtain 2", "Study", "Ground Floor"),
            EntitySpec("climate.ac_2", "climate", "AC 2", "Study", "Ground Floor"),
        ))
        for utterance, raw in (
            ("Turn off the Study Lamp 2.", "turnOff|Lamp 2|*|*|*|Study|*"),
            ("Close the Study Curtain 2.", "turnOff|Curtain 2|*|*|*|Study|*"),
            ("Turn off the Study AC 2.", "turnOff|AC 2|*|*|*|Study|*"),
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, numeric)
                self.assertFalse(grounded.clarification.required)

    def test_explicit_alternatives_require_value_clarification(self) -> None:
        cases = (
            (
                "Set the Study light brightness to 20 or 50 percent.",
                "set|Light|brightness|20|Percent|Study|*",
            ),
            (
                "Set the Study light color to Blue or Red.",
                "set|Light|color|Blue|*|Study|*",
            ),
            (
                "Set the Study AC mode to Heat or Cool.",
                "set|AC|mode|Heat|*|Study|*",
            ),
            (
                "Set the Study light brightness to 20 and 50 percent.",
                "set|Light|brightness|20|Percent|Study|*",
            ),
            (
                "Set the Study light brightness to 20, 50 percent.",
                "set|Light|brightness|20|Percent|Study|*",
            ),
            (
                "Set the Study light color to Blue with a Red accent.",
                "set|Light|color|Blue|*|Study|*",
            ),
            (
                "Set the Study AC mode to Cool with Fan Only.",
                "set|AC|mode|Cool|*|Study|*",
            ),
            (
                "Set the Study light brightness below 20 percent.",
                "set|Light|brightness|20|Percent|Study|*",
            ),
            (
                "Set the Study AC temperature above 20 Celsius.",
                "set|AC|temperature|20|Celsius|Study|*",
            ),
        )
        for utterance, raw in cases:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn("value", grounded.clarification.unresolved_slots)

    def test_fan_speed_is_not_misread_as_an_hvac_mode(self) -> None:
        for attribute in ("fan speed", "wind speed"):
            with self.subTest(attribute=attribute):
                grounded = ground_domux_request(
                    f"Set the Study AC {attribute} to High.",
                    f"set|AC|{attribute}|High|Level|Study|*",
                    self.registry,
                )
                self.assertFalse(grounded.clarification.required)

    def test_absolute_and_relative_numeric_actions_cannot_be_interchanged(self) -> None:
        rejected = (
            ("Raise the Study light brightness to 20 percent.", "adjustUp|Light|brightness|20|Percent|Study|*"),
            ("Lower the Study light brightness to 20 percent.", "adjustDown|Light|brightness|20|Percent|Study|*"),
            ("Make the Study AC warmer to 25 Celsius.", "adjustUp|AC|temperature|25|Celsius|Study|*"),
            ("Raise the Study curtain to 30 percent.", "adjustUp|Curtain|position|30|Percent|Study|*"),
            ("Set the Study light brightness by 20 percent.", "set|Light|brightness|20|Percent|Study|*"),
            ("Open the Study curtain by 20 percent.", "set|Curtain|position|20|Percent|Study|*"),
        )
        for utterance, raw in rejected:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn("value", grounded.clarification.unresolved_slots)

        accepted = (
            ("Increase the Study light brightness by 20 percent.", "adjustUp|Light|brightness|20|Percent|Study|*"),
            ("Make the Study AC 2 degrees warmer.", "adjustUp|AC|temperature|2|Celsius|Study|*"),
        )
        for utterance, raw in accepted:
            with self.subTest(utterance=utterance):
                self.assertFalse(ground_domux_request(utterance, raw, self.registry).clarification.required)

    def test_climate_turn_on_requires_a_resolvable_mode_clarification(self) -> None:
        grounded = ground_domux_request(
            "Turn on the Study AC.",
            "turnOn|AC|*|*|*|Study|*",
            self.registry,
        )
        self.assertTrue(grounded.clarification.required)
        self.assertIn("climate_mode_confirmation_required", grounded.clarification.reasons)
        resolved = resolve_clarification_submission(
            grounded,
            answer="Use Cool mode on the Study AC.",
            confirmed_instruction=parse_domux_output(
                "set|AC|mode|Cool|*|Study|Ground Floor"
            )[0],
            registry=self.registry,
        )
        plan = build_plan(
            resolved.confirmed_instruction,
            resolved.chosen,
            self.states[resolved.chosen.entity_id],
        )
        self.assertEqual((plan.service, plan.service_data["hvac_mode"]), ("set_hvac_mode", "cool"))

    def test_compound_turn_on_must_match_the_service_side_effect(self) -> None:
        rejected = (
            (
                "Open the Study curtain to 0 percent.",
                "set|Curtain|position|0|Percent|Study|*",
            ),
            (
                "Turn on the Study AC and set temperature to 20 Celsius.",
                "set|AC|temperature|20|Celsius|Study|*",
            ),
            (
                "Turn on the Study AC and set fan speed to High.",
                "set|AC|fan speed|High|Level|Study|*",
            ),
        )
        for utterance, raw in rejected:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertTrue(grounded.clarification.required)
                self.assertIn("action", grounded.clarification.unresolved_slots)

        accepted = (
            (
                "Open the Study curtain to 20 percent.",
                "set|Curtain|position|20|Percent|Study|*",
            ),
            (
                "Turn on the Study AC and use Cool mode.",
                "set|AC|mode|Cool|*|Study|*",
            ),
        )
        for utterance, raw in accepted:
            with self.subTest(utterance=utterance):
                self.assertFalse(ground_domux_request(utterance, raw, self.registry).clarification.required)

    def test_conditions_schedules_durations_and_meta_questions_fail_closed(self) -> None:
        conditionals = (
            "Turn off the Study light if the room is empty.",
            "Turn on the Study light when it gets dark.",
            "Turn off the Study light after dinner.",
            "Turn off the Study light tomorrow.",
            "Turn on the Study light for an hour.",
            "Turn off the Study light at noon.",
            "Turn off the Study light in five minutes.",
            "Turn off the Study light once I leave.",
            "Turn off the Study light every night.",
            "Turn off the Study light at 6:30.",
            "Turn off the Study light provided nobody is home.",
            "Turn off the Study light as long as nobody is home.",
            "Turn off the Study light in half an hour.",
            "Turn off the Study light on Monday.",
            "Turn off the Study light at nine.",
        )
        for utterance in conditionals:
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance, "turnOff|Light|*|*|*|Study|*", self.registry,
                )
                self.assertTrue(grounded.clarification.required)
                self.assertIn("unsupported_condition_or_time", grounded.clarification.reasons)
                with self.assertRaisesRegex(GroundingError, "immediate command"):
                    resolve_clarification_submission(
                        grounded,
                        answer="Yes, the Study light.",
                        confirmed_instruction=parse_domux_output(
                            "turnOff|Light|*|*|*|Study|Ground Floor"
                        )[0],
                        registry=self.registry,
                    )

        for utterance in (
            "Should I turn off the Study light?",
            "Tell me how to turn off the Study light.",
            "What happens if I turn off the Study light?",
            "Can I turn off the Study light?",
            "Is it okay to turn off the Study light?",
            "Do you recommend I turn off the Study light?",
            "Why should I turn off the Study light?",
            "Please explain why I should turn off the Study light?",
            "Do I need to turn off the Study light?",
            "Would it be safe to turn off the Study light?",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance, "turnOff|Light|*|*|*|Study|*", self.registry,
                )
                self.assertIn("informational_request", grounded.clarification.reasons)
                with self.assertRaisesRegex(GroundingError, "informational"):
                    resolve_clarification_submission(
                        grounded,
                        answer="Yes, do it now.",
                        confirmed_instruction=parse_domux_output(
                            "turnOff|Light|*|*|*|Study|Ground Floor"
                        )[0],
                        registry=self.registry,
                    )

        for utterance in (
            "Can you turn off the Study light?",
            "Could you turn off the Study light?",
        ):
            with self.subTest(utterance=utterance):
                polite = ground_domux_request(
                    utterance,
                    "turnOff|Light|*|*|*|Study|*",
                    self.registry,
                )
                self.assertFalse(polite.clarification.required)

    def test_unconsumed_initial_language_cannot_be_recovered_by_clarification(self) -> None:
        modifiers = (
            "subject to nobody being home",
            "depending on whether anyone is home",
            "so long as the room is empty",
            "during dinner",
            "upon my arrival",
            "at dusk",
            "at dawn",
            "this Friday",
            "next Friday",
            "by nine",
            "for the next hour",
            "momentarily",
            "as needed",
            "forget it",
            "I withdraw that",
            "I revoke that",
            "I take that back",
            "don't bother",
            "actually don't",
            "use all but Blue",
            "use Blue excluding Green",
            "use non-Blue",
        )
        confirmed = parse_domux_output(
            "turnOff|Ceiling Light|*|*|*|Study|Ground Floor"
        )[0]
        for modifier in modifiers:
            utterance = f"Turn off the Study Ceiling Light, {modifier}."
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Ceiling Light|*|*|*|Study|Ground Floor",
                    self.registry,
                )
                self.assertTrue(grounded.clarification.required)
                with self.assertRaises(GroundingError):
                    resolve_clarification_submission(
                        grounded,
                        answer="The Study Ceiling Light.",
                        confirmed_instruction=confirmed,
                        registry=self.registry,
                    )

        for utterance in (
            "Turn off the Study Ceiling Light; wait.",
            "Turn off the Study Ceiling Light, stop.",
            "Turn off the Study Ceiling Light, do not.",
            "Turn off the Study Ceiling Light, no.",
            "Turn off the Study Ceiling Light, not now.",
            "Turn off the Study Ceiling Light; I don't want it.",
            "Turn off the Study Ceiling Light; I dont want it.",
            "Turn off the Study Ceiling Light; I don't need it.",
            "Turn off the Study Ceiling Light; I dont need it.",
            "Turn off the Study Ceiling Light; please do not.",
            "Turn off the Study Ceiling Light; please no.",
            "Turn off the Study Ceiling Light; no please.",
            "Turn off the Study Ceiling Light; I mean no.",
            "Turn off the Study Ceiling Light; I want no.",
        ):
            with self.subTest(utterance=utterance):
                grounded = ground_domux_request(
                    utterance,
                    "turnOff|Ceiling Light|*|*|*|Study|Ground Floor",
                    self.registry,
                )
                self.assertIn("negative_or_cancelled_intent", grounded.clarification.reasons)

        for utterance in (
            "Turn off the Study Ceiling Light, use the other one.",
            "Turn off the Study Ceiling Light, use the other light.",
            "Turn off the Study Ceiling Light, not this one.",
            "Turn off the Study Ceiling Light, not that one.",
        ):
            with self.subTest(utterance=utterance):
                generic = ground_domux_request(
                    utterance,
                    "turnOff|Ceiling Light|*|*|*|Study|Ground Floor",
                    self.registry,
                )
                self.assertIn("unsupported_request_grammar", generic.clarification.reasons)

    def test_model_slots_cannot_launder_unconsumed_request_language(self) -> None:
        utterance = (
            "Set the Study Ceiling Light brightness to 50 percent "
            "subject to nobody being home."
        )
        raws = (
            "set|Ceiling Light|brightness|50|subject to nobody being home|Study|*",
            "set|Ceiling Light|subject to nobody being home|50|Percent|Study|*",
        )
        confirmed = parse_domux_output(
            "set|Ceiling Light|brightness|50|Percent|Study|Ground Floor"
        )[0]
        for raw in raws:
            with self.subTest(raw=raw):
                grounded = ground_domux_request(utterance, raw, self.registry)
                self.assertIn("unsupported_request_grammar", grounded.clarification.reasons)
                with self.assertRaisesRegex(GroundingError, "new immediate command"):
                    resolve_clarification_submission(
                        grounded,
                        answer="Study, confirm 50 percent brightness.",
                        confirmed_instruction=confirmed,
                        registry=self.registry,
                    )

    def test_displayed_candidate_indices_are_selectors_not_operation_values(self) -> None:
        registry = EntityRegistry((
            EntitySpec("light.alpha", "light", "Light", "Alpha", "Ground Floor"),
            EntitySpec("light.beta", "light", "Light", "Beta", "Ground Floor"),
            EntitySpec("light.gamma", "light", "Light", "Gamma", "Ground Floor"),
        ))
        grounded = ground_domux_request(
            "Turn off the light.",
            "turnOff|Light|*|*|*|*|*",
            registry,
        )
        self.assertEqual(len(grounded.clarification.candidates), 3)
        self.assertFalse(
            {"action", "attribute", "value", "unit"}
            .intersection(grounded.clarification.unresolved_slots)
        )
        for index, chosen in enumerate(grounded.candidates, start=1):
            with self.subTest(index=index, entity_id=chosen.entity_id):
                confirmed = DomuxInstruction(
                    "turnOff", "Light", "*", "*", "*", chosen.room, chosen.floor,
                )
                resolved = resolve_clarification_submission(
                    grounded,
                    answer=str(index),
                    confirmed_instruction=confirmed,
                    registry=registry,
                )
                self.assertEqual(resolved.chosen.entity_id, chosen.entity_id)

    def test_generic_selector_reversals_fail_closed(self) -> None:
        grounded = ground_domux_request(
            "Turn off the ceiling light.",
            "turnOff|Ceiling Light|*|*|*|*|*",
            self.registry,
        )
        confirmed = parse_domux_output(
            "turnOff|Ceiling Light|*|*|*|Study|Ground Floor"
        )[0]
        for answer in (
            "Study, not this one.",
            "Study, not that one.",
            "Study, not this device.",
            "Study, not the one I mean.",
            "The other one.",
            "Use the other one.",
            "Leave this one unchanged.",
            "Study, not this.",
            "I mean the other one.",
        ):
            with self.subTest(answer=answer), self.assertRaises(GroundingError):
                resolve_clarification_submission(
                    grounded,
                    answer=answer,
                    confirmed_instruction=confirmed,
                    registry=self.registry,
                )

    def test_candidate_selector_text_cannot_double_as_operation_authorization(self) -> None:
        grounded = ground_domux_request(
            "Set the light brightness between 1 and 20 percent.",
            "set|Light|brightness|1|Percent|*|*",
            self.registry,
        )
        with self.assertRaises(GroundingError):
            resolve_clarification_submission(
                grounded,
                answer="2",
                confirmed_instruction=parse_domux_output(
                    "set|Light|brightness|2|Percent|Study|Ground Floor"
                )[0],
                registry=self.registry,
            )

        numbered = EntityRegistry((
            EntitySpec("light.bedroom_50", "light", "Light", "Bedroom", "Ground Floor"),
            EntitySpec("light.study_numbered", "light", "Light", "Study", "Ground Floor"),
        ))
        numbered_grounded = ground_domux_request(
            "Set the light brightness between 20 and 80 percent.",
            "set|Light|brightness|20|Percent|*|*",
            numbered,
        )
        with self.assertRaises(GroundingError):
            resolve_clarification_submission(
                numbered_grounded,
                answer="light.bedroom_50",
                confirmed_instruction=parse_domux_output(
                    "set|Light|brightness|50|Percent|Bedroom|Ground Floor"
                )[0],
                registry=numbered,
            )

    def test_selector_words_and_numbers_cannot_masquerade_as_operation_values(self) -> None:
        numbered = EntityRegistry((
            EntitySpec("light.bedroom_50", "light", "Light", "Bedroom 50", "Ground Floor"),
        ))
        utterance = "Set the Bedroom 50 light brightness to 20 percent."
        wrong = ground_domux_request(
            utterance, "set|Light|brightness|50|Percent|Bedroom 50|*", numbered,
        )
        right = ground_domux_request(
            utterance, "set|Light|brightness|20|Percent|Bedroom 50|*", numbered,
        )
        self.assertTrue(wrong.clarification.required)
        self.assertIn("value", wrong.clarification.unresolved_slots)
        self.assertFalse(right.clarification.required)

        collision_registry = EntityRegistry((
            EntitySpec("light.orange_room", "light", "Light", "Orange Room", "Ground Floor"),
            EntitySpec("climate.heat_room", "climate", "AC", "Heat Room", "Ground Floor"),
        ))
        color_wrong = ground_domux_request(
            "Make the Orange Room light brighter.",
            "set|Light|color|Orange|*|Orange Room|*",
            collision_registry,
        )
        mode_wrong = ground_domux_request(
            "Make the Heat Room AC warmer.",
            "set|AC|mode|Heat|*|Heat Room|*",
            collision_registry,
        )
        self.assertTrue(color_wrong.clarification.required)
        self.assertIn("attribute", color_wrong.clarification.unresolved_slots)
        self.assertTrue(mode_wrong.clarification.required)
        self.assertIn("attribute", mode_wrong.clarification.unresolved_slots)


class FailOnceReadAdapter(InMemoryHAAdapter):
    fail_next_read = False

    def get_state(self, entity_id: str) -> dict[str, object]:
        if self.fail_next_read:
            self.fail_next_read = False
            raise AdapterError("injected predispatch read failure")
        return super().get_state(entity_id)


class ClockAdvancingAdapter(InMemoryHAAdapter):
    def __init__(self, states: dict[str, dict[str, object]], clock: MutableClock):
        super().__init__(states)
        self.clock = clock
        self.advance_reads = False

    def get_state(self, entity_id: str) -> dict[str, object]:
        value = super().get_state(entity_id)
        if self.advance_reads:
            self.clock.value += 8
        return value


class UnknownOutcomeAdapter(InMemoryHAAdapter):
    def call_service(self, domain: str, service: str, data: dict[str, object]) -> ServiceCallResult:
        self.sut_calls.append({"kind": "sut", "outcome": "request_error_outcome_unknown"})
        raise ServiceCallError(
            "injected transport loss", attempted=True, acknowledged=False, outcome_unknown=True,
        )


class WaitFailureAdapter(InMemoryHAAdapter):
    def wait_for_projection(
        self, entity_id: str, domain: str, expected: dict[str, object],
    ) -> dict[str, object]:
        del entity_id, domain, expected
        raise AdapterError("injected post-ack observation failure")


class BlockingAdapter(InMemoryHAAdapter):
    def __init__(self, states: dict[str, dict[str, object]]):
        super().__init__(states)
        self.utility_started = threading.Event()
        self.release_utility = threading.Event()

    def call_service(self, domain: str, service: str, data: dict[str, object]) -> ServiceCallResult:
        if data.get("entity_id") == "light.utility":
            self.utility_started.set()
            if not self.release_utility.wait(timeout=2):
                raise AssertionError("test did not release the blocked utility call")
        return super().call_service(domain, service, data)


class PreparedActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry, self.states = fixture()
        self.grounded = ground_domux_request(
            "Turn off the Ceiling Light.",
            "turnOff|Ceiling Light|*|*|*|*|*",
            self.registry,
        )
        self.confirmed = parse_domux_output(
            "turnOff|Ceiling Light|*|*|*|Study|Ground Floor"
        )[0]
        self.clock = MutableClock()
        self.nonce_index = 0

    def nonce(self) -> str:
        self.nonce_index += 1
        return f"test-nonce-{self.nonce_index}"

    def prepare(
        self,
        store_type: type[PreparedActionStore] = PreparedActionStore,
        *,
        adapter: InMemoryHAAdapter | None = None,
        grounded=None,
        answer: str | None = "Study",
        confirmed: DomuxInstruction | None = None,
        state_dependencies: tuple[str, ...] = (),
    ):
        adapter = adapter or InMemoryHAAdapter(self.states)
        store = store_type(ttl_seconds=30, clock=self.clock, nonce_factory=self.nonce)
        grounded = grounded or self.grounded
        action = store.prepare(
            actor_id="actor-a",
            session_id="session-a",
            grounded=grounded,
            registry=self.registry,
            adapter=adapter,
            clarification_answer=answer,
            confirmed_instruction=confirmed or self.confirmed,
            state_dependencies=state_dependencies,
        )
        return adapter, store, action

    def test_clean_commit_changes_only_the_selected_entity(self) -> None:
        adapter, store, action = self.prepare()
        untouched = adapter.get_state("light.living_ceiling")
        result = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertTrue(result.accepted and result.dispatched and result.acknowledged)
        self.assertEqual((result.status, result.after["state"]), ("COMMITTED", "off"))
        self.assertEqual(adapter.get_state("light.living_ceiling"), untouched)
        self.assertEqual(len(adapter.sut_calls), 1)

    def test_public_handle_confirmation_and_plan_copies_are_immutable(self) -> None:
        adapter, store, action = self.prepare()
        with self.assertRaises(FrozenInstanceError):
            action.entity_id = "light.living_ceiling"  # type: ignore[misc]
        snapshot = store.snapshot(action.nonce)
        snapshot["plan"]["service_data"]["entity_id"] = "light.living_ceiling"
        result = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual((result.status, result.after["entity_id"]), ("COMMITTED", "light.study_ceiling"))
        tombstone = store.snapshot(action.nonce)
        self.assertTrue(tombstone["redacted"])
        retained = json.dumps(tombstone)
        self.assertNotIn("Turn off the Ceiling Light", retained)
        self.assertNotIn("light.study_ceiling", retained)
        self.assertNotIn("actor-a", retained)

    def test_replay_and_two_prepared_nonces_dispatch_at_most_once_each_state(self) -> None:
        adapter, store, first = self.prepare()
        second = store.prepare(
            actor_id="actor-a", session_id="session-a", grounded=self.grounded,
            registry=self.registry, adapter=adapter, clarification_answer="Study",
            confirmed_instruction=self.confirmed,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda item: store.commit(item.confirmation(), registry=self.registry, adapter=adapter),
                (first, second),
            ))
        self.assertEqual(sum(result.dispatched for result in results), 1)
        self.assertEqual({result.reason for result in results}, {"committed", "state_changed"})
        replay = store.commit(first.confirmation(), registry=self.registry, adapter=adapter)
        self.assertIn(replay.reason, {"replayed_nonce", "action_not_prepared"})
        self.assertEqual(len(adapter.sut_calls), 1)

    def test_expiry_state_capability_and_candidate_drift_are_zero_dispatch(self) -> None:
        adapter, store, action = self.prepare()
        self.clock.value = action.expires_at + 1
        self.assertEqual(
            store.commit(action.confirmation(), registry=self.registry, adapter=adapter).reason,
            "expired",
        )

        self.clock.value = 1000
        adapter, store, action = self.prepare()
        adapter.mutate_state_for_setup("light.study_ceiling")
        self.assertEqual(store.commit(action.confirmation(), registry=self.registry, adapter=adapter).reason, "state_changed")

        adapter, store, action = self.prepare()
        changed_state = adapter.get_state("light.study_ceiling")
        changed_state["attributes"]["supported_color_modes"] = ["onoff"]
        adapter.set_state_for_setup("light.study_ceiling", changed_state)
        self.assertEqual(store.commit(action.confirmation(), registry=self.registry, adapter=adapter).reason, "state_changed")

        adapter, store, action = self.prepare()
        changed = self.registry.with_replacement(
            EntitySpec("light.study_ceiling", "light", "Ceiling Light", "Library", "Ground Floor")
        )
        self.assertEqual(store.commit(action.confirmation(), registry=changed, adapter=adapter).reason, "candidate_set_changed")

        adapter, store, action = self.prepare()
        expanded = EntityRegistry((*self.registry.entities, EntitySpec(
            "light.bedroom_ceiling", "light", "Ceiling Light", "Bedroom", "Ground Floor",
        )))
        self.assertEqual(store.commit(action.confirmation(), registry=expanded, adapter=adapter).reason, "candidate_set_changed")
        self.assertEqual(len(adapter.sut_calls), 0)

    def test_confirmation_binds_all_authorization_digests(self) -> None:
        mutations = (
            ("actor_id", "actor-b", "actor_mismatch"),
            ("session_id", "session-b", "session_mismatch"),
            ("request_digest", "0" * 64, "request_mismatch"),
            ("clarification_digest", "0" * 64, "clarification_mismatch"),
            ("plan_digest", "0" * 64, "plan_mismatch"),
            ("candidate_digest", "0" * 64, "confirmation_candidate_mismatch"),
        )
        for field, value, reason in mutations:
            adapter, store, action = self.prepare()
            result = store.commit(
                altered_confirmation(action.confirmation(), **{field: value}),
                registry=self.registry,
                adapter=adapter,
            )
            self.assertEqual((result.reason, len(adapter.sut_calls)), (reason, 0))

    def test_only_declared_or_context_state_is_bound(self) -> None:
        adapter, store, action = self.prepare()
        adapter.mutate_state_for_setup("light.utility")
        self.assertTrue(store.commit(action.confirmation(), registry=self.registry, adapter=adapter).accepted)

        context_grounded = ground_domux_request(
            "Turn off that device.",
            "turnOff|*|*|*|*|*|*",
            self.registry,
            SessionContext(("light.study_ceiling", "light.utility")),
        )
        adapter, store, action = self.prepare(grounded=context_grounded)
        adapter.mutate_state_for_setup("light.utility")
        result = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual((result.reason, len(adapter.sut_calls)), ("state_changed", 0))

        adapter, store, action = self.prepare(state_dependencies=("light.utility",))
        adapter.mutate_state_for_setup("light.utility")
        result = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual((result.reason, len(adapter.sut_calls)), ("state_changed", 0))

    def test_slow_predispatch_reads_cannot_cross_ttl(self) -> None:
        adapter = ClockAdvancingAdapter(self.states, self.clock)
        adapter, store, action = self.prepare(adapter=adapter)
        adapter.advance_reads = True
        result = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual((result.reason, len(adapter.sut_calls)), ("expired", 0))

    def test_abandoned_expired_action_is_redacted_on_purge(self) -> None:
        _adapter, store, action = self.prepare()
        self.clock.value = action.expires_at + 1
        self.assertEqual(store.purge_expired(), 1)
        snapshot = store.snapshot(action.nonce)
        self.assertTrue(snapshot["redacted"])
        retained = json.dumps(snapshot)
        self.assertNotIn("Ceiling Light", retained)
        self.assertNotIn("light.study_ceiling", retained)

    def test_predispatch_read_failure_does_not_consume_nonce(self) -> None:
        adapter = FailOnceReadAdapter(self.states)
        adapter, store, action = self.prepare(adapter=adapter)
        adapter.fail_next_read = True
        first = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual((first.reason, first.dispatched), ("predispatch_state_read_failed", False))
        second = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual((second.status, len(adapter.sut_calls)), ("COMMITTED", 1))

    def test_dispatch_and_post_ack_unknown_outcomes_are_action_local(self) -> None:
        adapter, store, action = self.prepare(adapter=UnknownOutcomeAdapter(self.states))
        result = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual(
            (result.status, result.dispatched, result.acknowledged, result.outcome_unknown),
            ("FAILED_DISPATCH", True, False, True),
        )

        adapter, store, action = self.prepare(adapter=WaitFailureAdapter(self.states))
        result = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual(
            (result.status, result.dispatched, result.acknowledged, result.outcome_unknown),
            ("FAILED_POSTCONDITION", True, True, True),
        )

    def test_postcondition_failure_is_visible_and_nonce_stays_consumed(self) -> None:
        adapter, store, action = self.prepare()
        adapter.force_postcondition_mismatch = True
        result = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual((result.status, result.reason), ("FAILED_POSTCONDITION", "postcondition_mismatch"))
        replay = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertEqual((replay.reason, len(adapter.sut_calls)), ("replayed_nonce", 1))

    def test_dependency_target_race_is_serialized_before_dispatch(self) -> None:
        adapter = BlockingAdapter(self.states)
        store = PreparedActionStore(ttl_seconds=30, clock=self.clock, nonce_factory=self.nonce)
        context_grounded = ground_domux_request(
            "Turn off that device.", "turnOff|*|*|*|*|*|*", self.registry,
            SessionContext(("light.study_ceiling", "light.utility")),
        )
        dependent = store.prepare(
            actor_id="actor-a", session_id="session-a", grounded=context_grounded,
            registry=self.registry, adapter=adapter, clarification_answer="Study",
            confirmed_instruction=self.confirmed,
        )
        utility_grounded = ground_domux_request(
            "Turn on the Utility Light in the Utility Room on the Ground Floor.",
            "turnOn|Utility Light|*|*|*|Utility Room|Ground Floor",
            self.registry,
        )
        utility = store.prepare(
            actor_id="actor-a", session_id="session-a", grounded=utility_grounded,
            registry=self.registry, adapter=adapter,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            utility_future = pool.submit(
                store.commit, utility.confirmation(), registry=self.registry, adapter=adapter,
            )
            self.assertTrue(adapter.utility_started.wait(timeout=1))
            dependent_future = pool.submit(
                store.commit, dependent.confirmation(), registry=self.registry, adapter=adapter,
            )
            adapter.release_utility.set()
            utility_result = utility_future.result(timeout=2)
            dependent_result = dependent_future.result(timeout=2)
        self.assertEqual(utility_result.status, "COMMITTED")
        self.assertEqual((dependent_result.reason, len(adapter.sut_calls)), ("state_changed", 1))

    def test_b1_binds_plan_and_session_but_deliberately_omits_temporal_guards(self) -> None:
        adapter, store, action = self.prepare(ClarifyPrepareStore)
        rejected = store.commit(
            altered_confirmation(action.confirmation(), session_id="session-b"),
            registry=self.registry,
            adapter=adapter,
        )
        self.assertEqual((rejected.reason, len(adapter.sut_calls)), ("session_mismatch", 0))

        adapter, store, action = self.prepare(ClarifyPrepareStore)
        first = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        second = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertTrue(first.accepted and second.accepted)
        self.assertEqual(len(adapter.sut_calls), 2)

        adapter, store, action = self.prepare(ClarifyPrepareStore)
        adapter.mutate_state_for_setup("light.study_ceiling")
        drift = store.commit(action.confirmation(), registry=self.registry, adapter=adapter)
        self.assertTrue(drift.accepted)
        self.assertEqual(len(adapter.sut_calls), 1)


class _HAHandler(BaseHTTPRequestHandler):
    token = "test-token-not-a-real-secret"
    state = {"entity_id": "light.demo", "state": "off", "attributes": {"brightness": 0}}
    climate_state = {
        "entity_id": "climate.demo",
        "state": "cool",
        "attributes": {
            "temperature": 24.0,
            "hvac_modes": ["off", "cool"],
            "supported_features": 1,
        },
    }
    config: object = {"unit_system": {"temperature": "°C"}}
    get_paths: list[str] = []
    calls: list[dict[str, object]] = []
    post_status: int | None = None

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self._json(401, {})
            return
        type(self).get_paths.append(self.path)
        if self.path == "/api/states/light.demo":
            self._json(200, self.state)
        elif self.path == "/api/states/climate.demo":
            self._json(200, self.climate_state)
        elif self.path == "/api/config":
            self._json(200, self.config)
        else:
            self._json(404, {})

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self._json(401, {})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).calls.append(payload)
        if type(self).post_status is not None:
            self._json(type(self).post_status, {})
            return
        if self.path == "/api/services/light/turn_on":
            type(self).state = {
                "entity_id": "light.demo", "state": "on", "attributes": {"brightness": 0},
            }
            self._json(200, [type(self).state])
        else:
            self._json(404, {})


class RestAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        _HAHandler.state = {"entity_id": "light.demo", "state": "off", "attributes": {"brightness": 0}}
        _HAHandler.climate_state = {
            "entity_id": "climate.demo",
            "state": "cool",
            "attributes": {
                "temperature": 24.0,
                "hvac_modes": ["off", "cool"],
                "supported_features": 1,
            },
        }
        _HAHandler.config = {"unit_system": {"temperature": "°C"}}
        _HAHandler.get_paths = []
        _HAHandler.calls = []
        _HAHandler.post_status = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HAHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_official_rest_shape_and_loopback_guard(self) -> None:
        port = self.server.server_address[1]
        adapter = HomeAssistantRESTAdapter(
            f"http://127.0.0.1:{port}", _HAHandler.token, poll_seconds=0.2,
        )
        before = adapter.get_state("light.demo")
        after = adapter.call_service("light", "turn_on", {"entity_id": "light.demo"})
        self.assertEqual((before["state"], after.after["state"]), ("off", "on"))
        self.assertEqual(len(adapter.sut_calls), 1)
        with self.assertRaises(ValueError):
            HomeAssistantRESTAdapter("https://example.com", "token")

    def test_http_4xx_is_rejected_but_5xx_has_unknown_dispatch_outcome(self) -> None:
        port = self.server.server_address[1]
        adapter = HomeAssistantRESTAdapter(
            f"http://127.0.0.1:{port}", _HAHandler.token, poll_seconds=0.2,
        )
        for status, unknown, outcome in (
            (400, False, "request_rejected"),
            (500, True, "request_error_outcome_unknown"),
        ):
            with self.subTest(status=status):
                _HAHandler.post_status = status
                with self.assertRaises(ServiceCallError) as caught:
                    adapter.call_service("light", "turn_on", {"entity_id": "light.demo"})
                self.assertTrue(caught.exception.attempted)
                self.assertFalse(caught.exception.acknowledged)
                self.assertEqual(caught.exception.outcome_unknown, unknown)
                self.assertEqual(adapter.sut_calls[-1]["outcome"], outcome)

    def test_climate_temperature_unit_comes_from_official_config(self) -> None:
        port = self.server.server_address[1]
        adapter = HomeAssistantRESTAdapter(
            f"http://127.0.0.1:{port}", _HAHandler.token, poll_seconds=0.2,
        )
        state = adapter.get_state("climate.demo")
        self.assertEqual(state["attributes"]["temperature_unit"], "°C")
        self.assertNotIn("temperature_unit", _HAHandler.climate_state["attributes"])
        self.assertEqual(
            _HAHandler.get_paths,
            ["/api/states/climate.demo", "/api/config"],
        )

        malformed = (
            [],
            {},
            {"unit_system": {}},
            {"unit_system": {"temperature": 123}},
        )
        for config in malformed:
            with self.subTest(config=config):
                _HAHandler.config = config
                with self.assertRaises(AdapterError):
                    adapter.get_state("climate.demo")


if __name__ == "__main__":
    unittest.main()
