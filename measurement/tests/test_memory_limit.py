import contextlib
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import memory_limit as ml
import memory_gate as gate
import worker
import summary
import yaml


class MemoryLimitTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'linux', 'Linux cgroup paths')
    def test_gate_asks_user_manager_to_attach_and_fails_before_exec(self):
        with patch('memory_gate.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'not delegated')) as attach, patch('memory_gate.os.execv') as execute:
            with self.assertRaisesRegex(RuntimeError, 'attachment failed'):
                gate.enter('/sys/fs/cgroup/pp8-test.scope', 8192, ['/bin/true'])
            self.assertIn('AttachProcessesToUnit', attach.call_args.args[0])
            self.assertIn('ssau', attach.call_args.args[0])
            execute.assert_not_called()
    def test_validation_rejects_bad_limits_and_timeouts(self):
        for value in ([], 1, [True], [0], [-1], [float('nan')], [float('inf')], ['1']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ml.validate_request({'memory_max': value})
        for value in (True, 0, -1, float('inf'), '30'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ml.validate_request({'condition_timeout_seconds': value})
        ml.validate_request({'memory_max': [.8, 1, 1.25, 2], 'condition_timeout_seconds': 1800})
        with self.assertRaises(ValueError):
            ml.validate_request({'memory_max': [1], 'ttft': True})
        with self.assertRaisesRegex(ValueError, '#42'):
            ml.validate_request({'memory_max': [1], 'load_mode': ['none']})

    def test_page_aligned_limit(self):
        self.assertEqual(ml.limit_bytes(10000, .8), 8192)
        self.assertEqual(ml.limit_bytes(8192, 1), 8192)
        self.assertEqual(ml.limit_bytes(10000, 1.25), 16384)

    def test_event_reading_missing_data_is_not_fake_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp)
            self.assertIsNone(ml.read_events(group))
            (group/'memory.events').write_text('oom 2\noom_kill 1\nmax 9\n')
            self.assertEqual(ml.read_events(group)['oom_kill'], 1)
            (group/'memory.events').write_text('invalid data here')
            self.assertIsNone(ml.read_events(group))

    def test_scope_start_failure_stops_only_its_generated_unit(self):
        process = unittest.mock.Mock()
        process.poll.return_value = 1
        with patch('memory_limit.sys.platform', 'linux'), patch('memory_limit.shutil.which', return_value='/bin/systemd-run'), patch('memory_limit.subprocess.Popen', return_value=process) as launch, patch('memory_limit.subprocess.run') as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'scope'):
                with ml.memory_scope(8192, io.StringIO()):
                    self.fail('failed scope must never yield')
            command = launch.call_args.args[0]
            self.assertIn('MemoryMax=8192', command)
            self.assertIn('MemorySwapMax=0', command)
            self.assertIn('Delegate=yes', command)
            unit = command[command.index('--unit') + 1]
            self.assertEqual(cleanup.call_args.args[0], ['systemctl', '--user', 'stop', unit])

    def test_oom_requires_counter_evidence_not_sigkill_alone(self):
        self.assertEqual(ml.classify(-9, {'oom_kill': 0}, {'oom_kill': 1}), 'oom')
        self.assertEqual(ml.classify(-9, {'oom_kill': 3}, {'oom_kill': 3}), 'failed')
        self.assertEqual(ml.classify(-9, None, None), 'failed_unknown_oom')
        self.assertEqual(ml.classify(125, None, None), 'setup_error')
        self.assertEqual(ml.classify(-9, {'oom_kill': 0}, {'oom_kill': 1}, True), 'timeout')

    def test_scope_setup_failure_never_runs_unlimited(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('memory_limit.memory_scope', side_effect=RuntimeError('permission denied')):
                runner = unittest.mock.Mock()
                result = ml.run_limited(['bench'], Path(tmp)/'monitor', {'order': 1}, 30, 1, 10000, tmp, runner)
                runner.assert_not_called()
                self.assertEqual(result.memory_limit['status'], 'setup_error')
                self.assertNotEqual(result.returncode, 0)

    def test_limited_runner_uses_exec_gate_and_reads_events_before_cleanup(self):
        state = []
        @contextlib.contextmanager
        def fake_scope(limit, log):
            state.append('open')
            yield {'cgroup_path': '/sys/fs/cgroup/pp8-test.scope', 'events_before': {'oom_kill': 0}}
            state.append('closed')
        def events(group):
            self.assertEqual(state, ['open'])
            return {'oom_kill': 1}
        def runner(cmd, monitor, context, timeout):
            self.assertIn('enter', cmd)
            self.assertEqual(cmd[-1], 'bench')
            return subprocess.CompletedProcess(cmd, -9, '', '')
        with tempfile.TemporaryDirectory() as tmp, patch('memory_limit.memory_scope', fake_scope), patch('memory_limit.read_events', events):
            result = ml.run_limited(['bench'], 'monitor', {'order': 1}, 2, .8, 10000, tmp, runner)
            self.assertEqual(result.memory_limit['status'], 'oom')
            self.assertEqual(state, ['open', 'closed'])

    def test_timeout_preserves_evidence_and_scope_cleanup(self):
        cleaned = []
        @contextlib.contextmanager
        def fake_scope(limit, log):
            try:
                yield {'cgroup_path': '/fake', 'events_before': {'oom_kill': 0}}
            finally:
                cleaned.append(True)
        with tempfile.TemporaryDirectory() as tmp, patch('memory_limit.memory_scope', fake_scope), patch('memory_limit.read_events', return_value={'oom_kill': 0}):
            runner = unittest.mock.Mock(side_effect=subprocess.TimeoutExpired('bench', 1))
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                ml.run_limited(['bench'], 'monitor', {'order': 1}, 1, 1, 10000, tmp, runner)
            self.assertEqual(raised.exception.memory_limit['status'], 'timeout')
            self.assertEqual(cleaned, [True])


class WorkerMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'model.gguf').write_bytes(b'fake')
        (self.root/'bench').touch()
        self.settings = dict(bench_path=str(self.root/'bench'), models_dir=str(self.root), work_dir=str(self.root/'work'))
        self.req = dict(model='model.gguf', threads=[1], prompt_tokens=[8], gen_tokens=[4], repeat=1)
        for name, value in [('system_info', {}), ('code_version', {}), ('platform.platform', 'test')]:
            p = patch('worker.'+name, return_value=value); p.start(); self.addCleanup(p.stop)

    def execute(self):
        req = self.root/'request.yaml'
        req.write_text(yaml.safe_dump(self.req), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.run_request(req, self.settings, 'test')
        folder = self.root/'work/results/test'
        return folder, json.loads((folder/'meta.json').read_text(encoding='utf-8'))

    def success(self, cmd, *args, **kwargs):
        p, n = cmd[cmd.index('-p')+1], cmd[cmd.index('-n')+1]
        return subprocess.CompletedProcess(cmd, 0,
            f'n_prompt,n_gen,test_time,cpu_info,build_commit,model_filename,n_threads,avg_ts,stddev_ts\n{p},{n},now,cpu,build,model.gguf,1,10,1\n', '')

    def test_unlimited_header_and_request_timeout_remain_compatible(self):
        self.req['condition_timeout_seconds'] = 7
        with patch('worker.run_monitored', side_effect=self.success) as runner:
            folder, meta = self.execute()
        self.assertEqual(runner.call_args.kwargs['timeout'], 7)
        with (folder/'results.csv').open(encoding='utf-8') as f:
            self.assertEqual(csv.DictReader(f).fieldnames, worker.FIELDS)
        self.assertNotIn('memory_limit', meta)

    def test_oom_row_is_saved_and_later_conditions_continue(self):
        self.req['memory_max'] = [.8, 2]
        calls = []
        def limited(cmd, monitor, context, timeout, multiplier, model_size, out_dir, runner):
            calls.append(multiplier)
            result = self.success(cmd)
            result.memory_limit = dict(memory_max=multiplier, memory_max_bytes=4096,
                cgroup_path='/fake', status='ok', events_before={'oom_kill': 0}, events_after={'oom_kill': 0})
            if len(calls) == 1:
                result.returncode = -9
                result.memory_limit.update(status='oom', events_after={'oom_kill': 1})
            return result
        with patch('worker.run_limited', side_effect=limited):
            folder, meta = self.execute()
        self.assertEqual(len(calls), 4)
        self.assertEqual((meta['ok'], meta['failed']), (3, 1))
        with (folder/'results.csv').open(encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]['status'], 'oom')
        self.assertEqual(rows[0]['avg_ts'], '')
        self.assertEqual(rows[0]['oom_kill_delta'], '1')
        self.assertEqual(len(rows), 4)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            summary.show(folder)
        self.assertIn('oom', out.getvalue())
        self.assertIn('0.8', out.getvalue())

    def test_timeout_records_row_and_runs_next_condition(self):
        self.req['memory_max'] = [1]
        count = []
        def limited(cmd, monitor, context, timeout, multiplier, model_size, out_dir, runner):
            count.append(True)
            if len(count) == 1:
                exc = subprocess.TimeoutExpired(cmd, timeout)
                exc.returncode = -9
                exc.memory_limit = dict(memory_max=1, memory_max_bytes=4096, status='timeout')
                raise exc
            result = self.success(cmd)
            result.memory_limit = dict(memory_max=1, memory_max_bytes=4096, status='ok')
            return result
        with patch('worker.run_limited', side_effect=limited):
            folder, meta = self.execute()
        self.assertEqual((meta['ok'], meta['failed']), (1, 1))
        with (folder/'results.csv').open(encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]['status'], 'timeout')
        self.assertEqual(rows[0]['returncode'], '-9')


if __name__ == '__main__':
    unittest.main()
