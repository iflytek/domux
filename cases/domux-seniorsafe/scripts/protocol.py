"""Shared wire-format validation. Format validity is not permission to execute."""
from __future__ import annotations

VALID_ACTIONS = {'turnOn', 'turnOff', 'set', 'adjustUp', 'adjustDown',
                 'activate', 'deactivate', 'pause'}
SCORER_VERSION = '2-ordered-exact-lcs-f1'


def parse_output(output: str) -> tuple[list[list[str]], bool]:
    if not isinstance(output, str):
        return [], False
    parsed = [[field.strip() for field in line.split('|')]
              for line in output.splitlines() if line.strip()]
    valid = bool(parsed) and all(len(row) == 7 and all(row)
                                 and row[0] in VALID_ACTIONS and row[1] != '*'
                                 for row in parsed)
    return parsed, valid


def exact_match(output: str, gold: str) -> bool:
    pred, pred_valid = parse_output(output)
    target, target_valid = parse_output(gold)
    return pred_valid and target_valid and pred == target
