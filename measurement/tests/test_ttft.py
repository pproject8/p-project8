import contextlib
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ttft
import summary


def timing(prompt=32, **changes):
    data = dict(ttft_ms=101.25, prefill_ms=100, first_token_ms=1.25,
                prompt_eval_ms=100, eval_ms=0, first_token_wall_ms=105,
                first_token_id=42, evaluated_tokens=prompt, sampled_tokens=1)
    data.update(changes)
    return data


def engine_log(data):
    return ('llama_perf_context_print: prompt eval time = 100 ms\n'
            'llama_perf_sampler_print: samplers time = 1.25 ms / 1 runs\n'
            'PP8_TTFT ' + json.dumps(data) + '\n')


class TTFTTests(unittest.TestCase):
    def test_engine_timing_sum_not_wall_or_load(self):
        data = timing(first_token_wall_ms=9000)
        result = ttft.parse_timing('load time = 9999 ms\n' + engine_log(data), 32)
        self.assertEqual(result['ttft_ms'], 101.25)
        self.assertEqual(result['first_token_id'], 42)

    def test_second_token_decode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'decode'):
            ttft.parse_timing(engine_log(timing(eval_ms=10, prefill_ms=110, ttft_ms=111.25)), 32)

    def test_one_token_prompt_engine_eval_classification(self):
        result = ttft.parse_timing(engine_log(timing(1, prompt_eval_ms=0, eval_ms=100)), 1)
        self.assertEqual(result['prefill_ms'], 100)

    def test_wrong_counts_missing_logs_and_bad_values_rejected(self):
        cases = [timing(evaluated_tokens=1), timing(evaluated_tokens=True), timing(sampled_tokens=2),
                 timing(first_token_id=-1), timing(ttft_ms=float('nan')), timing(prefill_ms=-1),
                 timing(ttft_ms=102), timing(first_token_ms=True)]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                ttft.parse_timing(engine_log(data), 32)
        for text in ['{}', 'PP8_TTFT {}', engine_log(timing()) * 2]:
            with self.assertRaises(ValueError):
                ttft.parse_timing(text, 32)

    def run_fixture(self, root, **req):
        request = dict(threads=[4], prompt_tokens=[32], repeat=2)
        request.update(req)
        return ttft.run_ttft(request, dict(bench_path=str(root/'bench'), work_dir=str(root)), root/'model', root, 'test')

    def test_fresh_measurement_per_repeat_and_warmup_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('ttft.prepare_helper', return_value=(root/'helper', root, {})), \
                 patch('ttft.measure', side_effect=[timing(ttft_ms=999), timing(), timing(ttft_ms=120)]) as measure:
                result = self.run_fixture(root, gen_tokens=[8, 128])
            self.assertEqual(measure.call_count, 3)
            self.assertIn('warmup', measure.call_args_list[0].args[-1].name)
            self.assertEqual(result['conditions'][0]['mean_ms'], (101.25+120)/2)
            rows = list(csv.DictReader(io.StringIO((root/'ttft.csv').read_text())))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['measurement_method'], ttft.METHOD)
            self.assertEqual(result['cache'], 'uncontrolled')

    def test_timeout_has_empty_value_and_next_repeat_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('ttft.prepare_helper', return_value=(root/'helper', root, {})), \
                 patch('ttft.measure', side_effect=[timing(), subprocess.TimeoutExpired('helper', 1), timing()]):
                result = self.run_fixture(root)
            rows = list(csv.DictReader(io.StringIO((root/'ttft.csv').read_text())))
            self.assertEqual([r['status'] for r in rows], ['timeout', 'ok'])
            self.assertEqual(rows[0]['ttft_ms'], '')
            self.assertEqual((result['ok'], result['failed']), (1, 1))

    def test_missing_tool_is_explicit_and_disabled_skips_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('ttft.prepare_helper', side_effect=RuntimeError('missing tool')):
                result = self.run_fixture(root)
            self.assertEqual(result['failed'], 2)
            with patch('ttft.prepare_helper') as build:
                result = self.run_fixture(root, ttft=False)
                build.assert_not_called()
            self.assertEqual(result['status'], 'disabled')
            self.assertEqual(len((root/'ttft.csv').read_text().splitlines()), 1)

    def test_warmup_failure_does_not_duplicate_or_measure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('ttft.prepare_helper', return_value=(root/'helper', root, {})), \
                 patch('ttft.measure', side_effect=ValueError('bad warmup')) as measure:
                result = self.run_fixture(root)
            self.assertEqual(measure.call_count, 1)
            self.assertEqual(result['failed'], 2)
            self.assertEqual(len(list(csv.DictReader(io.StringIO((root/'ttft.csv').read_text())))), 2)

    def test_measure_cleanup_on_interrupt_and_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for exc in [KeyboardInterrupt(), subprocess.TimeoutExpired('helper', 1)]:
                proc = Mock(); proc.wait.side_effect = exc
                with patch('ttft.subprocess.Popen', return_value=proc), patch('ttft.stop_process') as stop:
                    with self.assertRaises(type(exc)):
                        ttft.measure(root/'helper', root, root/'model', 4, 32, 1, root/'log')
                    stop.assert_called_once_with(proc)

    def test_failed_process_never_parsed_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); proc = Mock(returncode=-9)
            with patch('ttft.subprocess.Popen', return_value=proc), patch('ttft.stop_process'), patch('ttft.parse_timing') as parse:
                with self.assertRaises(RuntimeError):
                    ttft.measure(root/'helper', root, root/'model', 4, 32, 1, root/'log')
                parse.assert_not_called()

    def test_abi_guard_before_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('ttft.sys.platform', 'linux'), patch('ttft.subprocess.run', return_value=Mock(returncode=0, stdout='other-build', stderr='')):
                with self.assertRaisesRegex(RuntimeError, 'ABI'):
                    ttft.prepare_helper(dict(bench_path=tmp+'/bench'), io.StringIO())

    def test_summary_single_sample_and_failed(self):
        result = ttft.summarize([dict(threads=4, prompt_tokens=32, status='ok', ttft_ms=10),
                                 dict(threads=4, prompt_tokens=32, status='failed')])[0]
        self.assertEqual((result['ok'], result['failed']), (1, 1))
        self.assertIsNone(result['stddev_ms'])
        self.assertIsNone(result['cv_percent'])

    def test_summary_new_and_legacy_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for method, label in [(ttft.METHOD, 'engine timing'), (None, '로컬 HTTP')]:
                info = dict(conditions=[dict(threads=4, prompt_tokens=32, ok=2, failed=0, mean_ms=120, stddev_ms=12, cv_percent=10)])
                if method: info['measurement_method'] = method
                (root/'meta.json').write_text(json.dumps(dict(finished='yes', ttft_measurement=info)))
                output = io.StringIO()
                with contextlib.redirect_stdout(output): summary.show(root)
                self.assertIn(label, output.getvalue())
                self.assertIn('120.00 ms', output.getvalue())

    def test_invalid_settings_rejected(self):
        for req, settings in [({'ttft': 'yes'}, {}), ({}, {'ttft_request_timeout_seconds': float('nan')})]:
            with self.assertRaises(ValueError): ttft.validate_options(req, settings)


if __name__ == '__main__':
    unittest.main()
