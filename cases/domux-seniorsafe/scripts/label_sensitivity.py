"""Explicit label-only sensitivity analysis; never rewrite primary evidence."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from challenge_report import extra_metrics, write_new
from run_support import load_jsonl, provenance, fingerprint
from score import aggregate, comparison
from validate_data import input_contract, REQUIRED, validate_run_contract

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT/'artifacts/generalization-v1'
    review_path = folder/'label_review.json'
    review = json.loads(review_path.read_text(encoding='utf-8'))
    freeze = folder/'freeze.json'
    input_contract(ROOT/'data/challenge-v1.jsonl', ROOT/'data/challenge-v1.spec.json', freeze)
    dataset = load_jsonl(ROOT/'data/challenge-v1.jsonl')
    changes = {item['id']: item for item in review['changes']}
    if len(changes) != len(review['changes']) or not set(changes).issubset({r['id'] for r in dataset}):
        raise ValueError('invalid reviewed ID set')
    primary = json.loads((folder/'metrics.json').read_text(encoding='utf-8'))
    original, revised = {}, {}
    for pipeline in ('raw', 'normalized'):
        path = folder/f'{pipeline}_outputs.jsonl'
        env = json.loads((folder/f'{pipeline}_environment.json').read_text(encoding='utf-8'))
        rows = load_jsonl(path)
        if (env.get('status') != 'complete' or env.get('sample_count') != len(dataset)
                or env.get('outputs_sha256') != hashlib.sha256(path.read_bytes()).hexdigest()
                or [{k:r[k] for k in REQUIRED} for r in rows] != dataset):
            raise ValueError('primary output incomplete or changed')
        validate_run_contract(freeze, env['settings'], env['revision'])
        expected = provenance(dataset, env['settings'])
        if (any(env.get(k) != expected[k] for k in expected)
                or env['settings'].get('freeze_sha256') != hashlib.sha256(freeze.read_bytes()).hexdigest()
                or any(any(r.get(k) != env.get(k) for k in ('revision','run_id','pipeline','code_sha256','settings_sha256','dataset_sha256')) for r in rows)):
            raise ValueError('primary provenance mismatch')
        if aggregate(rows) != primary[pipeline]:
            raise ValueError('primary metric file changed')
        original[pipeline] = rows
        revised[pipeline] = deepcopy(rows)
        for r in revised[pipeline]:
            if r['id'] in changes:
                item = changes[r['id']]
                if r['gold'] != item['frozen_gold']:
                    raise ValueError('review does not refer to frozen gold')
                r['gold'] = item['protocol_gold']
    if comparison(original['raw'], original['normalized']) != primary['comparison']:
        raise ValueError('primary paired metrics changed')
    result = {
        'kind': 'label-only sensitivity, not a new model run or replacement primary score',
        'review_sha256': hashlib.sha256(review_path.read_bytes()).hexdigest(),
        'changed_ids': sorted(changes),
        'frozen_dataset_sha256': fingerprint(dataset),
        'overlay_dataset_sha256': fingerprint([{k:r[k] for k in REQUIRED} for r in revised['raw']]),
        'primary': {p: aggregate(r) for p,r in original.items()},
        'protocol_label_overlay': {p: aggregate(r) for p,r in revised.items()},
        'disputed_labels_excluded': {p: aggregate([row for row in r if row['id'] not in changes]) for p,r in original.items()},
        'overlay_extended_metrics': {p: extra_metrics(r) for p,r in revised.items()},
        'overlay_comparison': comparison(revised['raw'], revised['normalized']),
        'predictions_and_policy_unchanged': True,
    }
    write_new(folder/'label_sensitivity.json', result)
    print(json.dumps({p: result['protocol_label_overlay'][p]['result_accuracy'] for p in revised}))


if __name__ == '__main__':
    main()
