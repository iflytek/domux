"""Dataset contracts and freeze failures; no model-derived expected labels."""
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from generate_challenge import build_challenge
from generate_dataset import build_records
from challenge_report import extra_metrics, inventory
from validate_data import load_spec, validate, verify_freeze, validate_run_contract

ROOT = Path(__file__).resolve().parents[1]


class ChallengeTests(unittest.TestCase):
    def test_default_remains_strict_and_new_profile_accepts_challenge(self):
        self.assertEqual([], validate(build_records()))
        self.assertTrue(validate(build_records()[:2]))
        spec = load_spec(ROOT/'data/challenge-v1.spec.json')
        self.assertEqual([], validate(build_challenge(), spec))
        self.assertTrue(validate(build_challenge()))

    def test_bad_rows_and_pair_integrity(self):
        spec = load_spec(ROOT/'data/challenge-v1.spec.json')
        for bad in ({}, [], {'group': []}):
            self.assertTrue(validate([bad], spec))
        for key, value in [('group', 'unknown'), ('source', 'human-authored-synthetic'),
                           ('gold', ''), ('gold', 'bad'), ('evaluate_parse', 'false')]:
            rows = build_challenge()
            rows[0][key] = value
            self.assertTrue(validate(rows, spec), key)
        rows = build_challenge()
        rows[1]['text'] = rows[0]['text'].upper()
        self.assertTrue(validate(rows, spec))
        rows = build_challenge()
        rows[1]['gold'] = rows[2]['gold']
        self.assertTrue(validate(rows, spec))

    def test_no_exact_cross_split_text_or_family_overlap(self):
        old, new = build_records(), build_challenge()
        canonical = lambda s: ' '.join(s.casefold().split())
        self.assertFalse({canonical(r['text']) for r in old} & {canonical(r['text']) for r in new})
        self.assertFalse({r['base_id'] for r in old} & {r['base_id'] for r in new})
        self.assertEqual(136, sum(r['evaluate_parse'] for r in new))
        self.assertEqual(16, sum('\n' in r['gold'] for r in new))
        self.assertEqual(160, inventory(old, new)['unique_texts'])

    def test_metric_denominators_separate_false_allow_and_overblock(self):
        from run_support import finish_record
        source = build_challenge()[0]
        cases = [('clarify', 'execute', 'candidate'), ('execute', 'clarify', 'clarify'),
                 ('execute', 'execute', 'candidate')]
        rows = []
        for i, (expected, actual, output) in enumerate(cases):
            row = {**source, 'id': str(i), 'expected_decision': expected}
            result = finish_record(row, source['gold'], 1., None, actual)
            rows.append({**row, **result, 'safety_decision': actual, 'output_decision': output})
        rows[2]['raw_output'] = 'turnOff|Light|*|*|*|Bedroom|*'
        metrics = extra_metrics(rows)
        self.assertEqual((1, 1), (metrics['false_allow']['count'], metrics['false_allow']['denominator']))
        self.assertEqual((1, 2), (metrics['overblock']['count'], metrics['overblock']['denominator']))
        self.assertEqual((1, 2), (metrics['incorrect_scored_candidate']['count'], metrics['incorrect_scored_candidate']['denominator']))
        self.assertEqual(0, metrics['execution_performed_count'])

    def test_freeze_rejects_tampering_omissions_and_outside_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root/'data.txt'
            data.write_text('original')
            freeze = root/'freeze.json'
            files = {'data.txt': hashlib.sha256(data.read_bytes()).hexdigest()}
            freeze.write_text(json.dumps({'files': files}))
            self.assertEqual(64, len(verify_freeze(freeze, root, [data])))
            with self.assertRaises(ValueError):
                verify_freeze(freeze, root, [root/'missing'])
            data.write_text('changed')
            with self.assertRaises(ValueError):
                verify_freeze(freeze, root)
            freeze.write_text(json.dumps({'files': {'../outside': '0'*64}}))
            with self.assertRaises(ValueError):
                verify_freeze(freeze, root)

    def test_spec_rejects_inconsistent_counts_and_non_boolean_options(self):
        spec = load_spec(ROOT/'data/challenge-v1.spec.json')
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'spec.json'
            for key, value in [('records', 80), ('pairs', True), ('unique_text', 'yes'), ('sources', [])]:
                bad = deepcopy(spec)
                bad[key] = value
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    load_spec(path)

    def test_frozen_run_rejects_different_revision_or_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'freeze.json'
            path.write_text(json.dumps({'run': {'revision': 'a'*40, 'settings': {'threads': 16}}}))
            validate_run_contract(path, {'threads': 16}, 'a'*40)
            with self.assertRaises(ValueError):
                validate_run_contract(path, {'threads': 8}, 'a'*40)
            with self.assertRaises(ValueError):
                validate_run_contract(path, {'threads': 16}, 'b'*40)


if __name__ == '__main__':
    unittest.main()
