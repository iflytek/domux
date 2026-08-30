"""Freeze a predeclared local experiment once, before inference; never overwrite."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from challenge_report import inventory, write_new
from generate_challenge import build_challenge
from run_support import load_jsonl
from validate_data import load_spec, validate, verify_freeze

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT/'artifacts/generalization-v1'
    if any(folder.glob('*_outputs.jsonl')):
        raise ValueError('cannot freeze after model outputs exist')
    verify_freeze(folder/'baseline_lock.json', ROOT)
    data, spec = ROOT/'data/challenge-v1.jsonl', ROOT/'data/challenge-v1.spec.json'
    rows = load_jsonl(data)
    errors = validate(rows, load_spec(spec))
    if errors or rows != build_challenge():
        raise ValueError(f'dataset does not match valid prospective fixtures: {errors}')
    coverage = inventory(load_jsonl(ROOT/'data/seniorsafe.jsonl'), rows)
    if coverage != json.loads((folder/'coverage.json').read_text(encoding='utf-8')):
        raise ValueError('coverage inventory changed')
    files = [data, spec, ROOT/'data/seniorsafe.jsonl', folder/'baseline_lock.json',
             folder/'PROTOCOL.md', folder/'coverage.json', *sorted((ROOT/'scripts').glob('*.py'))]
    write_new(folder/'freeze.json', {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'meaning': 'local prospective freeze before first challenge model output; byte hashes, not external attestation',
        'run': {'revision': '6c71a32f4d624cadfd9fce9d10240d8068e53456',
                'settings': {'backend': 'transformers-cpu', 'repo_id': 'iFlytekOpenSource/Domux',
                             'dtype': 'bfloat16', 'threads': 16, 'max_new_tokens': 128, 'do_sample': False}},
        'files': {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
    print(f'Frozen {len(files)} files before inference')


if __name__ == '__main__':
    main()
