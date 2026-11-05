import argparse
import contextlib
import csv
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import worker,model_loading,summary,team


class LoadingPreservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'model.gguf').write_bytes(b'fake model');(self.root/'bench').touch()
        self.settings=dict(bench_path=str(self.root/'bench'),models_dir=str(self.root),work_dir=str(self.root/'work'))
        self.req=dict(name='preservation',model='model.gguf',repeat=2,threads=[1],prompt_tokens=[8],gen_tokens=[4])
        self.request=self.root/'request.yaml';self.request.write_text(yaml.safe_dump(self.req))
        self.ident='preservation';self.folder=self.root/'work/results'/self.ident
        for target,value in [('worker.system_info',{}),('worker.code_version',{}),('worker.platform.platform','test')]:
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)

    def bench(self,cmd,*args,**kwargs):
        p=cmd[cmd.index('-p')+1];n=cmd[cmd.index('-n')+1]
        output='n_prompt,n_gen,test_time,cpu_info,build_commit,model_filename,n_threads,avg_ts,stddev_ts\n'
        output+=f'{p},{n},now,cpu,build,model.gguf,1,10,1\n'
        return subprocess.CompletedProcess(cmd,0,output,'')

    def run_request(self):
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.run_monitored',side_effect=self.bench):
            worker.run_request(self.request,self.settings,req_id=self.ident)

    def assert_tps_readable(self,error):
        meta=json.loads((self.folder/'meta.json').read_text())
        self.assertTrue(meta['finished']);self.assertEqual((meta['ok'],meta['failed']),(2,0))
        rows=list(csv.DictReader(io.StringIO((self.folder/'results.csv').read_text())))
        self.assertEqual([r['test'] for r in rows],['pp8','tg4'])
        output=io.StringIO()
        with contextlib.redirect_stdout(output):summary.show(self.folder)
        self.assertIn('10.00',output.getvalue());self.assertIn(error,output.getvalue())
        return meta

    def test_incomplete_header_download_keeps_finished_tps(self):
        with patch('model_loading.prepare_helper',side_effect=http.client.IncompleteRead(b'partial')):
            self.run_request()
        meta=self.assert_tps_readable('IncompleteRead')
        self.assertEqual(meta['model_loading']['status'],'failed')
        self.assertEqual(meta['model_loading']['completed_samples'],0)

    def test_error_before_progress_callback_still_finalizes(self):
        with patch('worker.run_loading',side_effect=TypeError('unexpected loader bug')):
            self.run_request()
        self.assert_tps_readable('TypeError')

    def test_interrupt_preserves_completed_loading_samples_and_stops(self):
        success=(subprocess.CompletedProcess([],0,'{"model_load_s":0.25}',''),.5)
        with patch('model_loading.prepare_helper',return_value=(Path('/helper'),Path('/libs'),{})), \
             patch('model_loading.clear_cache'),patch('model_loading.run_timed_process',side_effect=[success,KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):self.run_request()
        meta=self.assert_tps_readable('KeyboardInterrupt')
        info=meta['model_loading']
        self.assertEqual(info['status'],'interrupted')
        self.assertEqual((info['completed_samples'],info['expected_samples']),(1,2))
        self.assertEqual(info['conditions'][0]['mean_s'],.5)
        self.assertEqual(info['conditions'][0]['ok'],1)
        rows=list(csv.DictReader(io.StringIO((self.folder/'model_loading.csv').read_text())))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'ok')

    def test_system_exit_is_saved_but_not_swallowed(self):
        with patch('worker.run_loading',side_effect=SystemExit(2)):
            with self.assertRaises(SystemExit):self.run_request()
        self.assert_tps_readable('SystemExit')

    def queued_request(self,base,ident):
        path=base/'queue'/(ident+'.yaml');path.write_text(yaml.safe_dump(self.req));return path

    def test_queue_download_failure_is_done_with_loading_failure(self):
        base=team.prepare(self.settings);path=self.queued_request(base,self.ident)
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.run_monitored',side_effect=self.bench), \
             patch('model_loading.prepare_helper',side_effect=http.client.IncompleteRead(b'partial')):
            team.process_one(path,self.settings,base)
        self.assertTrue((base/'done'/path.name).is_file())
        self.assertFalse((base/'failed'/path.name).exists())
        self.assert_tps_readable('IncompleteRead')

    def test_queue_interrupt_preserves_done_and_leaves_next_request(self):
        base=team.prepare(self.settings);path=self.queued_request(base,self.ident)
        other=self.queued_request(base,'next-request')
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.run_monitored',side_effect=self.bench), \
             patch('worker.run_loading',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):team.process_one(path,self.settings,base)
        self.assertTrue((base/'done'/path.name).is_file());self.assertTrue(other.exists())
        self.assertFalse((base/'running'/path.name).exists())
        self.assertEqual(team.outcome(base,self.ident,'done'),'done')
        self.assert_tps_readable('KeyboardInterrupt')

    def test_partial_tps_remains_partial_after_loading_interrupt(self):
        base=team.prepare(self.settings);path=self.queued_request(base,self.ident)
        calls=iter([False,True])
        def bench(cmd,*args,**kwargs):return self.bench(cmd) if next(calls) else subprocess.CompletedProcess(cmd,1,'','bad')
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.run_monitored',side_effect=bench), \
             patch('worker.run_loading',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):team.process_one(path,self.settings,base)
        self.assertTrue((base/'done'/path.name).is_file())
        self.assertEqual(team.outcome(base,self.ident,'done'),'partial')

    def test_interrupt_during_tps_is_not_falsely_finalized(self):
        base=team.prepare(self.settings);path=self.queued_request(base,self.ident)
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.run_monitored',side_effect=KeyboardInterrupt()), \
             patch('worker.run_loading') as loading:
            with self.assertRaises(KeyboardInterrupt):team.process_one(path,self.settings,base)
            loading.assert_not_called()
        self.assertTrue((base/'running'/path.name).is_file())
        meta=json.loads((self.folder/'meta.json').read_text());self.assertIsNone(meta['finished'])
        with self.assertRaisesRegex(ValueError,'미완료'):summary.show(self.folder)

    @unittest.skipUnless(sys.platform=='linux' and Path('/usr/bin/time').is_file(),'Linux GNU time required')
    def test_real_sigint_during_timed_load_keeps_tps_and_cleans_child(self):
        pidfile=self.root/'loading-pid'
        helper=self.root/'loading-helper'
        helper.write_text('#!'+sys.executable+'\nimport os,time\nfrom pathlib import Path\n'
                          +f'Path({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n')
        helper.chmod(0o700)
        source=str(Path(worker.__file__).parent)
        driver=self.root/'driver.py'
        driver.write_text(f'''import sys,subprocess
from pathlib import Path
sys.path.insert(0,{source!r})
import worker,model_loading
def bench(cmd,*args,**kwargs):
 p=cmd[cmd.index('-p')+1];n=cmd[cmd.index('-n')+1]
 text='n_prompt,n_gen,test_time,cpu_info,build_commit,model_filename,n_threads,avg_ts,stddev_ts\\n'
 return subprocess.CompletedProcess(cmd,0,text+f'{{p}},{{n}},now,cpu,build,model.gguf,1,10,1\\n','')
worker.run_monitored=bench
model_loading.prepare_helper=lambda *args:(Path({str(helper)!r}),Path({str(self.root)!r}),{{}})
model_loading.clear_cache=lambda settings:None
worker.run_request(Path({str(self.request)!r}),{self.settings!r},req_id={self.ident!r})
''')
        with (self.root/'driver.log').open('w') as log:
            proc=subprocess.Popen([sys.executable,str(driver)],stdout=log,stderr=log,start_new_session=True)
            try:
                deadline=time.monotonic()+8
                while not pidfile.exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(pidfile.exists(),'loading helper did not start')
                pid=int(pidfile.read_text())
                proc.send_signal(signal.SIGINT);proc.wait(timeout=8)
                self.assertNotEqual(proc.returncode,0)
                self.assert_tps_readable('KeyboardInterrupt')
                stat=Path(f'/proc/{pid}/stat')
                deadline=time.monotonic()+2
                while stat.exists() and not stat.read_text().split(')')[1].strip().startswith('Z') and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(not stat.exists() or stat.read_text().split(')')[1].strip().startswith('Z'),'loading child still running')
            finally:
                if proc.poll() is None:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                if pidfile.exists():
                    try:
                        group=os.getpgid(int(pidfile.read_text()))
                        if group!=os.getpgrp():os.killpg(group,signal.SIGKILL)
                    except ProcessLookupError:pass


if __name__=='__main__':unittest.main()
