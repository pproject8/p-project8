import contextlib
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ttft
import worker
import summary


class StreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        try:
            self.wfile.write(b': ping\n\ndata: {"tokens":[],"content":""}\n\n')
            self.wfile.flush()
            time.sleep(self.server.token_delay)
            self.wfile.write(b'data: {"tokens":[42],"content":"","stop":false}\n\n')
            self.wfile.flush()
            time.sleep(self.server.final_delay)
            final = {"stop": True, "tokens": [], "tokens_cached": 32,
                     "timings": {"prompt_n": self.server.evaluated}}
            self.wfile.write(('data: ' + json.dumps(final) + '\n\n').encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class TTFTTests(unittest.TestCase):
    def server(self, evaluated=32, token_delay=.02, final_delay=.2):
        server = ThreadingHTTPServer(('127.0.0.1', 0), StreamHandler)
        server.evaluated, server.token_delay, server.final_delay = evaluated, token_delay, final_delay
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def test_first_generated_id_not_ping_content_or_final_response(self):
        server = self.server()
        started = time.perf_counter()
        result = ttft.measure(server.server_port, [1] * 32, 2)
        self.assertEqual(result['first_token_id'], 42)
        self.assertEqual(result['evaluated_tokens'], 32)
        self.assertLess(result['ttft_ms'], (time.perf_counter() - started) * 1000 - 100)
        self.assertFalse(server.payload['cache_prompt'])
        self.assertEqual(server.payload['n_predict'], 1)
        self.assertTrue(server.payload['return_tokens'])

    def test_reused_or_wrong_input_count_is_rejected(self):
        server = self.server(evaluated=1, final_delay=0)
        with self.assertRaisesRegex(ValueError, 'prompt_n'):
            ttft.measure(server.server_port, [1] * 32, 2)

    def test_request_deadline(self):
        server = self.server(token_delay=.3, final_delay=0)
        with self.assertRaises((TimeoutError, OSError)):
            ttft.measure(server.server_port, [1] * 32, .05)

    def test_prompt_length_and_summary_single_sample(self):
        self.assertEqual(ttft.prompt_for_length([99, 1, 2], 5), [99, 1, 2, 1, 2])
        rows = [dict(threads=4, prompt_tokens=32, status='ok', ttft_ms=10),
                dict(threads=4, prompt_tokens=32, status='failed')]
        result = ttft.summarize(rows)[0]
        self.assertEqual((result['ok'], result['failed']), (1, 1))
        self.assertIsNone(result['stddev_ms'])
        self.assertIsNone(result['cv_percent'])

    def test_missing_server_is_explicit_and_disabled_is_header_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            req = dict(threads=[4], prompt_tokens=[32], repeat=2)
            result = ttft.run_ttft(req, dict(bench_path=str(root/'missing')), root/'model', root, 'test')
            self.assertEqual(result['failed'], 2)
            with (root/'ttft.csv').open(encoding='utf-8') as f:
                self.assertEqual([r['status'] for r in csv.DictReader(f)], ['unavailable'] * 2)
            req['ttft'] = False
            result = ttft.run_ttft(req, dict(bench_path=str(root/'missing')), root/'model', root, 'test')
            self.assertEqual(result['status'], 'disabled')
            self.assertEqual(len((root/'ttft.csv').read_text(encoding='utf-8').splitlines()), 1)

    def test_server_cleanup_on_interrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); server = root/'server'; server.touch()
            proc = unittest.mock.Mock()
            proc.poll.return_value = None
            with patch('ttft.subprocess.Popen', return_value=proc), patch('ttft.request', side_effect=KeyboardInterrupt), patch('ttft.stop_server') as stop:
                with self.assertRaises(KeyboardInterrupt):
                    ttft.run_ttft(dict(threads=[4], prompt_tokens=[32], repeat=1),
                                  dict(bench_path=str(server), server_path=str(server)), root/'model', root, 'test')
                stop.assert_called_once_with(proc)

    def test_failure_does_not_duplicate_rows_or_continue_busy_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); server = root/'server'; server.touch()
            proc = unittest.mock.Mock(); proc.poll.return_value = None
            def respond(port, endpoint, *args, **kwargs):
                return {'tokens': [99, 1]} if endpoint == '/tokenize' else {}
            with patch('ttft.subprocess.Popen', return_value=proc), patch('ttft.request', side_effect=respond), patch('ttft.measure', side_effect=TimeoutError), patch('ttft.stop_server') as stop:
                result = ttft.run_ttft(dict(threads=[4], prompt_tokens=[32, 64], repeat=2),
                                      dict(bench_path=str(server), server_path=str(server)), root/'model', root, 'test')
                self.assertEqual(result['failed'], 4)
                with (root/'ttft.csv').open(encoding='utf-8') as f:
                    self.assertEqual(len(list(csv.DictReader(f))), 4)
                stop.assert_called_once_with(proc)

    def test_summary_displays_ttft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'meta.json').write_text(json.dumps({'finished': 'yes', 'ttft_measurement': {
                'conditions': [dict(threads=4, prompt_tokens=32, ok=2, failed=0,
                                    mean_ms=120, stddev_ms=12, cv_percent=10)]}}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                summary.show(root)
            self.assertIn('120.00 ms', out.getvalue())
            self.assertIn('10.00%', out.getvalue())

    def test_invalid_ttft_settings_rejected(self):
        with self.assertRaises(ValueError):
            ttft.validate_options({'ttft': 'yes'}, {})
        with self.assertRaises(ValueError):
            ttft.validate_options({}, {'ttft_request_timeout_seconds': float('nan')})


if __name__ == '__main__':
    unittest.main()
