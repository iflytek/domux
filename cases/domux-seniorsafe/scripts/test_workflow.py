"""Real local HTTP + CLI + files test. The provider is synthetic, not Domux."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from generate_dataset import build_records
from normalize import normalize_text, safety_decision
from run_support import RunJournal, finish_record, output_decision, provenance

SCRIPT_DIR = Path(__file__).resolve().parent


class PolicyTests(unittest.TestCase):
    def test_additional_safety_cases(self):
        # Authored expectations, not labels generated from the policy function.
        cases = [
            ('TURN ON THE GAS VALVE', 'reject'),
            ('打开厨房的 Gas Valve', 'reject'),
            ('open the front door lock', 'clarify'),
            ('把门锁关闭', 'clarify'),
            ('disable the SECURITY system', 'reject'),
            ('turn on oven while nobody is home', 'reject'),
            ('把取暖器调到100度', 'reject'),
            ('set HEATER to 29.5 degrees', 'reject'),
            ('把卧室那个打开', 'clarify'),
            ('turn it off in Living Room', 'clarify'),
            ('', 'clarify'),
            ('hello', 'clarify'),
            ('打开客厅灯', 'execute'),
            ('Turn on the bedroom light', 'execute'),
            ('set satellite light brightness to 50 percent', 'execute'),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, safety_decision(text)[0])

    def test_output_policy_never_grants_execution(self):
        cases = [
            ('execute', 'turnOn|Gas Valve|*|*|*|Kitchen|*', None, 'clarify'),
            ('execute', 'set|AC|temperature|999|Celsius|Bedroom|*', None, 'reject'),
            ('execute', 'set|AC|temperature|nan|Celsius|Bedroom|*', None, 'reject'),
            ('execute', 'set|AC|temperature|24|Percent|Bedroom|*', None, 'reject'),
            ('execute', 'set|Light|temperature|24|Celsius|Bedroom|*', None, 'reject'),
            ('execute', 'turnOn|Light|*|*|*|*|*', None, 'clarify'),
            ('execute', 'turnOn|Light|*|*|*|Bedroom|*', None, 'candidate'),
            ('reject', 'turnOn|Light|*|*|*|Bedroom|*', None, 'reject'),
            ('clarify', 'turnOn|Light|*|*|*|Bedroom|*', None, 'clarify'),
            ('execute', '', 'TimeoutError', 'reject'),
        ]
        for source, output, error, expected in cases:
            with self.subTest(output=output, source=source):
                self.assertEqual(expected, output_decision(source, output, error)[0])


class JournalTests(unittest.TestCase):
    def test_resume_checks_prefix_and_preserves_old_results(self):
        rows = build_records()[:2]
        metadata = {**provenance(rows, {}), 'run_id': 'test', 'pipeline': 'raw', 'revision': '0'*40}
        with tempfile.TemporaryDirectory() as temp:
            output, env = Path(temp)/'raw_outputs.jsonl', Path(temp)/'raw_environment.json'
            journal = RunJournal(output, env, rows, metadata)
            record = {**rows[0], **metadata, **finish_record(rows[0], rows[0]['gold'], 1., None, 'execute')}
            journal.append(record)
            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                RunJournal(output, env, rows, metadata)
            self.assertEqual(original, output.read_bytes())
            resumed = RunJournal(output, env, rows, metadata, True)
            self.assertEqual(1, len(resumed.completed))
            with self.assertRaises(ValueError):
                RunJournal(output, env, rows, {**metadata, 'code_sha256': 'changed'}, True)
            with self.assertRaises(ValueError):
                RunJournal(output, env, list(reversed(rows)), metadata, True)
            with output.open('ab') as handle:
                handle.write(b'{"partial":')
            with self.assertRaises(ValueError):
                RunJournal(output, env, rows, metadata, True)


class WorkflowTests(unittest.TestCase):
    def test_both_cli_pipelines_and_scoring_with_local_fake_provider(self):
        rows = build_records()
        answers = {r['text']: r['gold'] for r in rows}
        answers.update({normalize_text(r['text'])[0]: r['gold'] for r in rows})
        state = {'mode': 'good'}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if state['mode'] == 'good':
                    body = {'choices': [{'message': {'content': answers[payload['messages'][0]['content']]}, 'finish_reason': 'stop'}]}
                elif state['mode'] == 'truncated':
                    body = {'choices': [{'message': {'content': answers[payload['messages'][0]['content']]}, 'finish_reason': 'length'}]}
                elif state['mode'] == 'null':
                    body = {'choices': [{'message': {'content': None}, 'finish_reason': 'stop'}]}
                else:
                    body = {'choices': []}
                encoded = json.dumps(body).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)

                def run(pipeline, folder=root, *extra):
                    return subprocess.run([sys.executable, '-B', str(SCRIPT_DIR/'run_eval.py'),
                        '--base-url', f'http://127.0.0.1:{server.server_port}/v1', '--model', 'synthetic-test-provider',
                        '--revision', '0'*40, '--run-id', 'synthetic-test-'+pipeline, '--pipeline', pipeline,
                        '--warmup', '0', '--output', str(folder/f'{pipeline}_outputs.jsonl'),
                        '--environment-output', str(folder/f'{pipeline}_environment.json'), *extra],
                        capture_output=True, text=True, timeout=30)

                for pipeline in ('raw', 'normalized'):
                    proc = run(pipeline)
                    self.assertEqual(0, proc.returncode, proc.stderr)
                    env = json.loads((root/f'{pipeline}_environment.json').read_text())
                    self.assertEqual('complete', env['status'])
                    self.assertEqual(80, env['completed_samples'])
                    self.assertNotEqual(0, run(pipeline).returncode)
                    self.assertEqual(0, run(pipeline, root, '--resume').returncode)
                proc = subprocess.run([sys.executable, '-B', str(SCRIPT_DIR/'score.py'),
                    '--raw', str(root/'raw_outputs.jsonl'), '--normalized', str(root/'normalized_outputs.jsonl'),
                    '--output', str(root/'metrics.json')], capture_output=True, text=True, timeout=30)
                self.assertEqual(0, proc.returncode, proc.stderr)
                metrics = json.loads((root/'metrics.json').read_text())
                self.assertEqual(1., metrics['raw']['result_accuracy'])
                self.assertEqual(80, metrics['comparison']['common_samples'])
                raw_path = root/'raw_outputs.jsonl'
                original = raw_path.read_bytes()
                raw_path.write_bytes(original.replace(b'turnOn', b'turnOff', 1))
                proc = subprocess.run([sys.executable, '-B', str(SCRIPT_DIR/'score.py'),
                    '--raw', str(raw_path), '--normalized', str(root/'normalized_outputs.jsonl'),
                    '--output', str(root/'tampered_metrics.json')], capture_output=True, text=True, timeout=30)
                self.assertNotEqual(0, proc.returncode)
                self.assertFalse((root/'tampered_metrics.json').exists())
                raw_path.write_bytes(original)
                for mode in ('bad', 'truncated', 'null'):
                    state['mode'] = mode
                    proc = run('raw', root/mode, '--limit', '2')
                    self.assertEqual(1, proc.returncode, proc.stderr)
                    env = json.loads((root/mode/'raw_environment.json').read_text())
                    self.assertEqual('failed', env['status'])
                    self.assertEqual(2, env['errors'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
