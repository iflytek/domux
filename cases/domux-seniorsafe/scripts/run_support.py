"""Versioned, exclusive benchmark output with conservative prefix resume."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from normalize import contains_term, safety_decision
from protocol import exact_match, parse_output


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def provenance(rows: list[dict], settings: dict) -> dict:
    root = Path(__file__).resolve().parent
    files = ('normalize.py', 'protocol.py', 'run_support.py', 'run_eval.py',
             'run_transformers_cpu.py', 'score.py', 'validate_data.py')
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
    return {'schema_version': 2, 'dataset_sha256': fingerprint(rows),
            'code_sha256': fingerprint(hashes), 'code_files': hashes,
            'settings_sha256': fingerprint(settings), 'settings': settings}


def load_jsonl(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('JSONL records must be objects')
    return rows


def select_rows(rows: list[dict], limit: int | None, sample_ids: str = '') -> list[dict]:
    if limit is not None and limit <= 0:
        raise ValueError('limit must be positive')
    ids = [row['id'] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate dataset ids')
    if sample_ids:
        if limit is not None:
            raise ValueError('use either sample-ids or limit')
        requested = [item.strip() for item in sample_ids.split(',') if item.strip()]
        if not requested or len(set(requested)) != len(requested):
            raise ValueError('sample-ids must be non-empty and unique')
        by_id = {row['id']: row for row in rows}
        if any(row_id not in by_id for row_id in requested):
            raise ValueError('unknown sample ids')
        return [by_id[row_id] for row_id in requested]
    selected = rows[:limit] if limit is not None else rows
    if not selected:
        raise ValueError('empty dataset selection')
    return selected


def output_decision(source_decision: str, output: str, error: str | None) -> tuple[str, list[str]]:
    """Offline policy only. Candidate is not execution permission or device proof."""
    if source_decision != 'execute':
        return source_decision, ['source_policy_blocks_candidate']
    parsed, valid = parse_output(output)
    if error or not valid:
        return 'reject', ['inference_error_or_invalid_protocol']
    for action, device, attribute, value, unit, room, floor in parsed:
        # Reject generated high-risk actions even if absent from the input.
        risk, reasons = safety_decision(f'{action} {device}')
        if risk != 'execute':
            return 'clarify', ['generated_risky_or_unknown_device', *reasons]
        if room == '*':
            return 'clarify', ['generated_target_missing_room']
        ranges = {'brightness': (0, 100, 'Percent'), 'position': (0, 100, 'Percent'),
                  'openness': (0, 100, 'Percent'), 'temperature': (16, 30, 'Celsius'),
                  'colorTemperature': (3000, 6500, 'Kelvin')}
        is_light = any(contains_term(device, t) for t in ('light', 'lamp'))
        is_ac = device.casefold() == 'ac'
        is_curtain = any(contains_term(device, t) for t in ('curtain', 'blind'))
        capabilities = ({'brightness', 'color', 'colorTemperature', 'mode'} if is_light else
                        {'temperature', 'mode', 'windSpeed'} if is_ac else
                        {'position', 'openness'} if is_curtain else set())
        if not capabilities:
            return 'clarify', ['unsupported_device_capabilities']
        if action in {'turnOn', 'turnOff', 'pause'}:
            if (attribute, value, unit) != ('*', '*', '*') or (action == 'pause' and not is_curtain):
                return 'reject', ['invalid_action_fields']
        elif action in {'set', 'adjustUp', 'adjustDown'}:
            if attribute not in capabilities:
                return 'reject', ['unsupported_attribute']
            if action != 'set' and value == '*' and unit == '*' and attribute in ranges:
                continue
            if attribute in ranges:
                low, high, expected_unit = ranges[attribute]
                try:
                    number = float(value)
                except ValueError:
                    return 'reject', ['invalid_numeric_value']
                # Relative deltas do not have a known resulting state.
                if action != 'set':
                    return 'clarify', ['relative_adjustment_requires_device_state']
                if not math.isfinite(number) or not low <= number <= high or unit != expected_unit:
                    return 'reject', ['value_or_unit_out_of_policy']
            elif value == '*' or unit != '*':
                return 'reject', ['invalid_named_value']
            else:
                return 'clarify', ['named_value_requires_device_capability_registry']
        else:
            return 'clarify', ['scene_requires_device_registry']
    return 'candidate', ['offline_only_requires_device_resolution_and_execution_ack']


def finish_record(row: dict, output: str, latency_ms: float, error: str | None,
                  source_decision: str) -> dict:
    parsed, valid = parse_output(output)
    decision, reasons = output_decision(source_decision, output, error)
    return {'raw_output': output, 'parsed': parsed, 'format_valid': valid and error is None,
            'result_correct': bool(row['evaluate_parse']) and error is None and exact_match(output, row['gold']),
            'latency_ms': round(latency_ms, 3), 'error': error,
            'output_decision': decision, 'output_reasons': reasons, 'execution_performed': False}


class RunJournal:
    """Never overwrite evidence. Resume only an intact, matching result prefix."""
    def __init__(self, output: Path, environment: Path, rows: list[dict], metadata: dict, resume: bool = False):
        self.output, self.environment, self.rows = output, environment, rows
        self.metadata = metadata
        self.previous_environment: dict = {}
        self.completed: list[dict] = []
        if output.resolve() == environment.resolve():
            raise ValueError('result and environment paths must differ')
        if resume:
            old = json.loads(environment.read_text(encoding='utf-8'))
            self.previous_environment = old
            for key, value in metadata.items():
                if old.get(key) != value:
                    raise ValueError(f'resume metadata mismatch: {key}')
            data = output.read_bytes()
            if data and not data.endswith(b'\n'):
                raise ValueError('partial last line; preserve file and inspect before resuming')
            self.completed = load_jsonl(output)
            if old.get('status') == 'complete' and old.get('outputs_sha256') != hashlib.sha256(data).hexdigest():
                raise ValueError('completed output digest mismatch')
            if len(self.completed) > len(rows):
                raise ValueError('resume has more records than the dataset')
            for saved, source in zip(self.completed, rows):
                if any(saved.get(key) != value for key, value in source.items()):
                    raise ValueError('resume records do not match dataset prefix')
                for key in ('run_id', 'pipeline', 'revision', 'dataset_sha256', 'code_sha256', 'settings_sha256'):
                    if saved.get(key) != metadata[key]:
                        raise ValueError(f'resume result metadata mismatch: {key}')
        else:
            if output.exists() or environment.exists():
                raise FileExistsError('output already exists; choose a new run directory or use --resume')
            output.parent.mkdir(parents=True, exist_ok=True)
            environment.parent.mkdir(parents=True, exist_ok=True)
            with environment.open('x', encoding='utf-8') as handle:
                json.dump({**metadata, 'status': 'running', 'completed_samples': 0}, handle, ensure_ascii=False, indent=2)
            with output.open('x', encoding='utf-8'):
                pass

    def append(self, record: dict) -> None:
        with self.output.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps({**record, **{key: self.metadata[key] for key in
                ('schema_version', 'dataset_sha256', 'code_sha256', 'settings_sha256')}}, ensure_ascii=False) + '\n')
            handle.flush()
        self.completed.append(record)

    def finish(self, extra: dict | None = None) -> int:
        errors = sum(row['error'] is not None for row in self.completed)
        complete = len(self.completed) == len(self.rows)
        status = 'complete' if complete and not errors else 'failed'
        payload = {**self.previous_environment, **self.metadata, **(extra or {}), 'status': status,
                   'completed_samples': len(self.completed), 'errors': errors,
                   'outputs_sha256': hashlib.sha256(self.output.read_bytes()).hexdigest()}
        temp = self.environment.with_suffix(self.environment.suffix + '.tmp')
        with temp.open('w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp.replace(self.environment)
        return 0 if status == 'complete' else 1
