import contextlib
import csv
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import model_loading as loading
import summary


class TimeLoadingTests(unittest.TestCase):
    def test_elapsed_log_not_api_or_cpu_time(self):
        self.assertEqual(loading.parse_elapsed('user=9\nsystem=8\nPP8_MODEL_LOAD_ELAPSED_S=1.25\n'),1.25)
        for text in ['', 'PP8_MODEL_LOAD_ELAPSED_S=nan', 'PP8_MODEL_LOAD_ELAPSED_S=inf',
                     'PP8_MODEL_LOAD_ELAPSED_S=0', 'PP8_MODEL_LOAD_ELAPSED_S=-1',
                     'PP8_MODEL_LOAD_ELAPSED_S=1\nPP8_MODEL_LOAD_ELAPSED_S=2']:
            with self.subTest(text=text),self.assertRaises(ValueError):loading.parse_elapsed(text)

    def test_ttft_engine_blocks_cache_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper=Path(tmp)/'drop';helper.touch()
            def run(cmd,**kwargs):
                self.assertEqual(cmd[0],'pgrep','sudo must never be reached')
                return subprocess.CompletedProcess(cmd,0 if cmd[-1]=='ttft-engine' else 1,b'',b'')
            with patch('model_loading.subprocess.run',side_effect=run) as execute:
                with self.assertRaisesRegex(RuntimeError,'캐시'):
                    loading.clear_cache(dict(drop_caches_path=str(helper)))
            self.assertEqual(execute.call_count,4)

    def test_process_query_failure_blocks_cache_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper=Path(tmp)/'drop';helper.touch()
            with patch('model_loading.subprocess.run',return_value=subprocess.CompletedProcess([],2,b'',b'')) as execute:
                with self.assertRaises(RuntimeError):loading.clear_cache(dict(drop_caches_path=str(helper)))
            self.assertEqual(execute.call_count,1)

    @unittest.skipUnless(sys.platform=='linux' and shutil.which('pgrep'),'Linux pgrep required')
    def test_actual_ttft_named_process_blocks_cache_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper=Path(tmp)/'must-not-run';helper.touch()
            code="import ctypes,time; ctypes.CDLL(None).prctl(15,b'ttft-engine',0,0,0); print('ready',flush=True); time.sleep(30)"
            child=subprocess.Popen([sys.executable,'-u','-c',code],stdout=subprocess.PIPE,text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(),'ready')
                with self.assertRaisesRegex(RuntimeError,'다른 측정이 실행 중'):
                    loading.clear_cache(dict(drop_caches_path=str(helper)))
            finally:
                child.terminate();child.wait();child.stdout.close()

    def test_idle_notebook_clears_cache_after_all_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper=Path(tmp)/'drop';helper.touch()
            def run(cmd,**kwargs):return subprocess.CompletedProcess(cmd,1 if cmd[0]=='pgrep' else 0,b'',b'')
            with patch('model_loading.subprocess.run',side_effect=run) as execute:
                loading.clear_cache(dict(drop_caches_path=str(helper)))
            self.assertEqual(execute.call_count,5)
            self.assertEqual(execute.call_args.args[0],['sudo','-n',str(helper)])

    def test_summary_distinguishes_legacy_and_process_elapsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for method,label in [(None,'이전 방식'),(loading.METHOD,'time 로딩 전용 프로세스')]:
                info=dict(conditions=[dict(load_mode='auto',cache='cold',ok=2,failed=0,mean_s=1.25,stddev_s=.01,cv_percent=.8)])
                if method:info['measurement_method']=method
                (root/'meta.json').write_text(json.dumps(dict(finished='yes',model_loading=info)))
                output=io.StringIO()
                with contextlib.redirect_stdout(output):summary.show(root)
                self.assertIn(label,output.getvalue());self.assertIn('1.250초',output.getvalue())

    def test_interrupt_and_timeout_stop_wrapper_and_child(self):
        for exc in [KeyboardInterrupt(),subprocess.TimeoutExpired('time',1)]:
            proc=Mock();proc.wait.side_effect=exc;proc.poll.return_value=None
            with patch('model_loading.subprocess.Popen',return_value=proc),patch('model_loading.stop_timed_process') as stop:
                with self.assertRaises(type(exc)):
                    loading.run_timed_process(['helper'],io.StringIO(),1)
                stop.assert_called_once_with(proc)

    @unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/time').is_file(),'GNU time on Linux required')
    def test_actual_gnu_time_and_separate_api_result(self):
        log=io.StringIO()
        command=[sys.executable,'-c','import time; time.sleep(.05); print(\'{"model_load_s":0.001}\')']
        result,elapsed=loading.run_timed_process(command,log,3)
        self.assertEqual(result.returncode,0)
        self.assertGreaterEqual(elapsed,.04)
        self.assertEqual(loading.parse_measurement(result.stdout),.001)
        self.assertIn('PP8_MODEL_LOAD_ELAPSED_S=',log.getvalue())

    @unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/time').is_file(),'GNU time on Linux required')
    def test_actual_timeout_reaps_helper_before_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile=Path(tmp)/'pid'
            code='import os,time,pathlib; pathlib.Path('+repr(str(pidfile))+').write_text(str(os.getpid())); time.sleep(30)'
            with self.assertRaises(subprocess.TimeoutExpired):
                loading.run_timed_process([sys.executable,'-c',code],io.StringIO(),.3)
            self.assertTrue(pidfile.exists())
            pid=int(pidfile.read_text())
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                stat=Path(f'/proc/{pid}/stat')
                if not stat.exists() or stat.read_text().split(')')[1].strip().startswith('Z'):break
                time.sleep(.01)
            else:self.fail('timed-out loading helper is still running')


if __name__=='__main__':unittest.main()
