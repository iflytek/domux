"""Prospective coverage inventory and extra metrics, with strict paired scoring."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from protocol import parse_output
from run_support import load_jsonl, provenance
from score import aggregate, correct
from validate_data import input_contract, validate, validate_run_contract

ROOT = Path(__file__).resolve().parents[1]


def inventory(old, new):
    canonical = lambda s: ' '.join(s.casefold().split())
    overlap = sorted({canonical(r['text']) for r in old} & {canonical(r['text']) for r in new})
    family_overlap = sorted({r['base_id'] for r in old} & {r['base_id'] for r in new})
    if overlap or family_overlap:
        raise ValueError('challenge leaks exact text or base_id from regression set')
    commands = [cmd for r in new if r['evaluate_parse'] for cmd in parse_output(r['gold'])[0]]
    old_gold = {r['gold'] for r in old}
    return dict(records=len(new), families=len({r['base_id'] for r in new}),
                unique_texts=len({canonical(r['text']) for r in new}), text_overlap=overlap,
                base_id_overlap=family_overlap, gold_overlap_records=sum(r['gold'] in old_gold for r in new),
                unique_scored_gold=len({r['gold'] for r in new if r['evaluate_parse']}),
                parse_evaluable=sum(r['evaluate_parse'] for r in new),
                multi_command_records=sum('\n' in r['gold'] for r in new),
                language_counts=dict(Counter(r['language'] for r in new)),
                group_counts=dict(Counter(r['group'] for r in new)),
                decision_counts=dict(Counter(r['expected_decision'] for r in new)),
                actions=dict(Counter(c[0] for c in commands)), rooms=sorted({c[5] for c in commands}),
                devices=sorted({c[1] for c in commands}), values=sorted({c[3] for c in commands}),
                limitation='AI-authored purposive synthetic pairs; shared protocol/templates; no population or device-safety inference')


def ratio(ids, denominator):
    return dict(count=len(ids), denominator=denominator, rate=len(ids)/denominator if denominator else None, ids=ids)


def extra_metrics(rows):
    blocked = [r for r in rows if r['expected_decision'] != 'execute']
    allowed = [r for r in rows if r['expected_decision'] == 'execute']
    candidates = [r for r in rows if r['output_decision'] == 'candidate']
    scored_candidates = [r for r in candidates if r['evaluate_parse']]
    decisions = ('execute', 'clarify', 'reject')
    matrix = {e: {a: 0 for a in decisions} for e in decisions}
    for r in rows:
        matrix[r['expected_decision']][r['safety_decision']] += 1
    return dict(
        clean=aggregate([r for r in rows if r['group'] == 'clean']),
        challenge=aggregate([r for r in rows if r['group'] != 'clean']),
        exact_correct=sum(correct(r) for r in rows), confusion_matrix_expected_then_actual=matrix,
        false_allow=ratio([r['id'] for r in blocked if r['safety_decision'] == 'execute'], len(blocked)),
        overblock=ratio([r['id'] for r in allowed if r['safety_decision'] != 'execute'], len(allowed)),
        blocked_request_candidate=ratio([r['id'] for r in blocked if r['output_decision'] == 'candidate'], len(blocked)),
        incorrect_scored_candidate=ratio([r['id'] for r in scored_candidates if not correct(r)], len(scored_candidates)),
        output_decisions=dict(Counter(r['output_decision'] for r in rows)),
        execution_performed_count=sum(r['execution_performed'] is not False for r in rows))


def write_new(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=ROOT/'artifacts/generalization-v1')
    parser.add_argument('--inventory-only', action='store_true')
    args = parser.parse_args()
    data = ROOT/'data/challenge-v1.jsonl'
    spec_path = ROOT/'data/challenge-v1.spec.json'
    frozen = None if args.inventory_only else args.directory/'freeze.json'
    spec, _ = input_contract(data, spec_path, frozen)
    new = load_jsonl(data)
    errors = validate(new, spec)
    if errors:
        raise ValueError('; '.join(errors))
    if args.inventory_only:
        write_new(args.directory/'coverage.json', inventory(load_jsonl(ROOT/'data/seniorsafe.jsonl'), new))
        return
    raw_path, norm_path = [args.directory/f'{p}_outputs.jsonl' for p in ('raw', 'normalized')]
    raw, norm = load_jsonl(raw_path), load_jsonl(norm_path)
    # Strict score checks the two runs; also bind both to the predeclared complete challenge and freeze.
    from validate_data import REQUIRED
    for rows in (raw, norm):
        if [{k: r[k] for k in REQUIRED} for r in rows] != new:
            raise ValueError('results do not match the frozen full challenge')
    freeze_digest = hashlib.sha256(frozen.read_bytes()).hexdigest()
    for pipeline in ('raw', 'normalized'):
        env = json.loads((args.directory/f'{pipeline}_environment.json').read_text(encoding='utf-8'))
        if env['settings'].get('freeze_sha256') != freeze_digest:
            raise ValueError('run was not bound to this freeze')
        validate_run_contract(frozen, env['settings'], env['revision'])
        expected = provenance(new, env['settings'])
        if any(env.get(k) != expected[k] for k in expected):
            raise ValueError('run provenance differs from the frozen data/code/settings')
    subprocess.run([sys.executable, '-B', str(ROOT/'scripts/score.py'), '--raw', str(raw_path),
                    '--normalized', str(norm_path), '--output', str(args.directory/'metrics.json')], check=True, stdout=subprocess.DEVNULL)
    extended = {p: extra_metrics(rows) for p, rows in (('raw', raw), ('normalized', norm))}
    write_new(args.directory/'extended_metrics.json', extended)
    failures = []
    for a, b in zip(raw, norm):
        if (a['evaluate_parse'] and (not correct(a) or not correct(b))
                or a['safety_decision'] != a['expected_decision']
                or b['safety_decision'] != b['expected_decision']):
            failures.append(dict(id=a['id'], text=a['text'], gold=a['gold'], evaluate_parse=a['evaluate_parse'],
                raw_output=a['raw_output'], normalized_request=b['request_text'], normalized_output=b['raw_output'],
                raw_correct=correct(a), normalized_correct=correct(b),
                expected_decision=a['expected_decision'], actual_input_decision=a['safety_decision'],
                raw_output_decision=a['output_decision'], normalized_output_decision=b['output_decision']))
    write_new(args.directory/'failures.json', failures)
    print(json.dumps(extended, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
