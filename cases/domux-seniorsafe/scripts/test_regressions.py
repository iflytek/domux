"""Offline counterexamples; no model downloads or external requests."""
import copy
import unittest
from unittest.mock import patch

from generate_dataset import build_records
from normalize import normalize_text, safety_decision
from run_eval import evaluate_record, parse_output
from score import aggregate, comparison, matching_counts
from validate_data import validate


def result(row, output=None):
    return {**row, 'raw_output': row['gold'] if output is None else output,
            'format_valid': True, 'result_correct': True, 'latency_ms': 1.,
            'error': None, 'safety_decision': row['expected_decision'], 'revision': '0' * 40}


class RegressionTests(unittest.TestCase):
    def test_risk_aliases_and_missing_target(self):
        for text in ('打开客厅的 gas valve', 'turn on the door lock',
                     '把客厅那个打开', 'turn on it in the bedroom', ''):
            with self.subTest(text=text):
                self.assertNotEqual('execute', safety_decision(text)[0])

    def test_correction_retains_action_and_context(self):
        text = '把客厅窗帘关上，不对，是卧室的窗帘。'
        normalized, _ = normalize_text(text)
        self.assertTrue('关上' in normalized or 'turn off' in normalized)
        self.assertIn('不对', normalized)
        self.assertTrue('卧室' in normalized or 'Bedroom' in normalized)

    def test_english_alias_has_word_boundaries(self):
        self.assertIn('satellite', normalize_text('Set satellite light to blue')[0])
        self.assertIn('light', normalize_text('turn on LITE')[0])

    def test_invalid_protocol(self):
        for output in ('||||||', 'BOGUS|Light|*|*|*|Living Room|*', ''):
            self.assertFalse(parse_output(output)[1])

    def test_empty_gold_is_invalid(self):
        self.assertTrue(validate([{**row, 'gold': ''} for row in build_records()]))

    def test_order_and_duplicates_affect_exact_match(self):
        a = 'turnOn|Light|*|*|*|Living Room|*'
        b = 'turnOff|Light|*|*|*|Living Room|*'
        row = {**build_records()[0], 'gold': a + '\n' + b}
        with patch('run_eval.call_model', return_value=(b + '\n' + a, 1.)):
            r = evaluate_record(row, 'raw', '', '', 'test', '0'*40, 'test', 1, 32)
            self.assertFalse(r['result_correct'])
        self.assertFalse(aggregate([result(build_records()[0], a+'\n'+a)])['result_accuracy'])

    def test_exact_intent_not_consumed_by_partial_match(self):
        a = 'turnOn|Light|*|*|*|Living Room|*'
        b = 'turnOn|Light|*|*|*|Bedroom|*'
        wrong = 'turnOff|Light|*|*|*|Living Room|*'
        self.assertEqual(1, matching_counts(wrong+'\n'+a, a+'\n'+b)[3])

    def test_comparison_rejects_incomplete_duplicate_or_changed_data(self):
        rows = [result(row) for row in build_records()]
        for bad in (rows[:1], rows + [rows[0]]):
            with self.assertRaises(ValueError):
                comparison(rows, bad)
        bad = copy.deepcopy(rows)
        bad[0]['text'] = 'changed input'
        with self.assertRaises(ValueError):
            comparison(rows, bad)

    def test_empty_gold_cannot_score_as_correct(self):
        row = {**build_records()[0], 'gold': ''}
        with patch('run_eval.call_model', return_value=('', 1.)):
            self.assertFalse(evaluate_record(row, 'raw', '', '', 'test', '0'*40, 'test', 1, 32)['result_correct'])

    def test_malformed_api_response_is_recorded(self):
        with patch('run_eval.call_model', side_effect=IndexError('empty choices')):
            r = evaluate_record(build_records()[0], 'raw', '', '', 'test', '0'*40, 'test', 1, 32)
        self.assertIsNotNone(r['error'])
        self.assertFalse(r['result_correct'])

    def test_scorer_recomputes_flags_and_penalizes_extra_lines(self):
        row = build_records()[0]
        self.assertEqual(0., aggregate([result(row, '||||||')])['result_accuracy'])
        output = row['gold'] + '\n' + row['gold']
        counts = matching_counts(output, row['gold'])
        self.assertEqual((1, 2, 1), counts[3:])
        errored = {**result(row), 'error': 'TimeoutError'}
        self.assertEqual(0., aggregate([errored])['slot_f1'])


if __name__ == '__main__':
    unittest.main()
