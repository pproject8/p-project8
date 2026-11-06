import collections
import contextlib
import csv
import http.client
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import ttft,worker,summary,team


def timing(prompt):
    return dict(ttft_ms=100.1,prefill_ms=100,first_token_ms=.1,prompt_eval_ms=100,
                eval_ms=0,first_token_wall_ms=101,first_token_id=42,evaluated_tokens=prompt,sampled_tokens=1)


class ScheduleTests(unittest.TestCase):
    def request(self,**changes):
        req=dict(threads=[1,2],prompt_tokens=[32,64],gen_tokens=[8,128],repeat=2)
        req.update(changes);return req

    def test_disabled_shuffle_keeps_yaml_and_repeat_order(self):
        seed,jobs=ttft.make_schedule(self.request())
        self.assertIsNone(seed)
        self.assertEqual(jobs,[(1,32,1),(1,32,2),(1,64,1),(1,64,2),
                               (2,32,1),(2,32,2),(2,64,1),(2,64,2)])

    def test_seed_replays_order_and_does_not_change_global_rng(self):
        before=random.getstate()
        a=ttft.make_schedule(self.request(shuffle=True,seed=17))
        b=ttft.make_schedule(self.request(shuffle=True,seed=17))
        c=ttft.make_schedule(self.request(shuffle=True,seed=18))
        self.assertEqual(a,b);self.assertNotEqual(a[1],c[1])
        self.assertNotEqual(a[1],ttft.make_schedule(self.request())[1])
        self.assertEqual(random.getstate(),before)
        self.assertEqual(collections.Counter(a[1]),collections.Counter(ttft.make_schedule(self.request())[1]))

    def test_missing_seed_is_generated_and_recorded_for_replay(self):
        with patch('ttft.random.randrange',return_value=1234):
            seed,jobs=ttft.make_schedule(self.request(shuffle=True))
        self.assertEqual(seed,1234)
        self.assertEqual(jobs,ttft.make_schedule(self.request(shuffle=True,seed=seed))[1])

    def test_actual_measure_calls_csv_and_metadata_follow_shuffled_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            def measure(helper,libdir,model,threads,prompt,timeout,log):
                calls.append((threads,prompt,log.name));return timing(prompt)
            with patch('ttft.prepare_helper',return_value=(root/'tool',root,{})),patch('ttft.measure',side_effect=measure):
                info=ttft.run_ttft(self.request(shuffle=True,seed=17),{},root/'model',root,'test')
            rows=list(csv.DictReader(io.StringIO((root/'ttft.csv').read_text())))
            self.assertEqual(info['seed'],17);self.assertEqual(info['ok'],8)
            measured=[c for c in calls if 'warmup' not in c[2]]
            self.assertEqual(len([c for c in calls if 'warmup' in c[2]]),4)
            self.assertEqual([(r['threads'],r['prompt_tokens']) for r in rows],[(str(t),str(p)) for t,p,_ in measured])
            self.assertEqual([int(r['order']) for r in rows],list(range(1,9)))
            for row,expected in zip(rows,info['execution_order']):
                for k in ('threads','prompt_tokens','repetition','order'):self.assertEqual(int(row[k]),expected[k])
            # Exactly one excluded warmup before a condition's first sample.
            seen=set()
            for t,p,name in calls:
                if (t,p) not in seen:self.assertIn('warmup',name);seen.add((t,p))
                else:self.assertNotIn('warmup',name)

    def test_warmup_failure_is_limited_to_its_condition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def measure(helper,libdir,model,threads,prompt,timeout,log):
                if prompt==64:raise ValueError('bad warmup')
                return timing(prompt)
            with patch('ttft.prepare_helper',return_value=(root/'tool',root,{})),patch('ttft.measure',side_effect=measure):
                info=ttft.run_ttft(self.request(shuffle=True,seed=17),{},root/'model',root,'test')
            self.assertEqual((info['ok'],info['failed']),(4,4))
            self.assertEqual(info['completed_samples'],8)

    def test_memory_limited_request_does_not_launch_unlimited_ttft(self):
        with tempfile.TemporaryDirectory() as tmp,patch('ttft.prepare_helper') as build:
            root=Path(tmp)
            info=ttft.run_ttft(self.request(memory_max=[.8]),{},root/'model',root,'test')
            build.assert_not_called();self.assertEqual(info['status'],'disabled')


class TTFTIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'model.gguf').write_bytes(b'model');(self.root/'bench').touch()
        self.settings=dict(bench_path=str(self.root/'bench'),models_dir=str(self.root),work_dir=str(self.root/'work'))
        self.req=dict(model='model.gguf',threads=[1],prompt_tokens=[8,16],gen_tokens=[4],repeat=2,
                      shuffle=True,measure_loading=False)
        self.request=self.root/'request.yaml';self.request.write_text(yaml.safe_dump(self.req))
        self.folder=self.root/'work/results/test'
        for target,value in [('worker.system_info',{}),('worker.code_version',{}),('worker.platform.platform','test')]:
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)

    def bench(self,cmd,*args,**kwargs):
        p=cmd[cmd.index('-p')+1];n=cmd[cmd.index('-n')+1]
        text='n_prompt,n_gen,test_time,cpu_info,build_commit,model_filename,n_threads,avg_ts,stddev_ts\n'
        return subprocess.CompletedProcess(cmd,0,text+f'{p},{n},now,cpu,build,model.gguf,1,10,1\n','')

    def test_phase_order_and_automatic_seed_shared_with_ttft(self):
        calls=[]
        def bench(*args,**kwargs):calls.append('TPS');return self.bench(*args,**kwargs)
        def run_ttft(req,*args,**kwargs):
            calls.append('TTFT');self.assertEqual(req['seed'],1234)
            return dict(status='ok',enabled=True,shuffle=True,seed=req['seed'],conditions=[])
        def loading(*args,**kwargs):calls.append('LOAD');return dict(status='disabled',conditions=[])
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.random.randrange',return_value=1234), \
             patch('worker.run_monitored',side_effect=bench),patch('worker.run_ttft',side_effect=run_ttft), \
             patch('worker.run_loading',side_effect=loading):
            worker.run_request(self.request,self.settings,req_id='test')
        self.assertEqual(calls,['TPS','TPS','TPS','TTFT','LOAD'])
        meta=json.loads((self.folder/'meta.json').read_text())
        self.assertEqual(meta['seed'],meta['ttft_measurement']['seed']);self.assertTrue(meta['finished'])

    def test_unexpected_ttft_error_preserves_tps_and_still_runs_loading(self):
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.run_monitored',side_effect=self.bench), \
             patch('worker.run_ttft',side_effect=http.client.IncompleteRead(b'partial')), \
             patch('worker.run_loading',return_value=dict(status='disabled',conditions=[])) as loading:
            worker.run_request(self.request,self.settings,req_id='test')
        loading.assert_called_once()
        meta=json.loads((self.folder/'meta.json').read_text())
        self.assertEqual(meta['ok'],3);self.assertTrue(meta['finished'])
        self.assertEqual(meta['ttft_measurement']['status'],'failed')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):summary.show(self.folder)
        self.assertIn('IncompleteRead',output.getvalue());self.assertIn('10.00',output.getvalue())

    def test_ttft_interrupt_preserves_tps_and_never_starts_loading(self):
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.run_monitored',side_effect=self.bench), \
             patch('worker.run_ttft',side_effect=KeyboardInterrupt()),patch('worker.run_loading') as loading:
            with self.assertRaises(KeyboardInterrupt):worker.run_request(self.request,self.settings,req_id='test')
        loading.assert_not_called()
        meta=json.loads((self.folder/'meta.json').read_text())
        self.assertTrue(meta['finished']);self.assertEqual(meta['ok'],3)
        self.assertEqual(meta['ttft_measurement']['status'],'interrupted')

    def test_queue_interrupt_keeps_done_and_sends_single_end_notice(self):
        base=team.prepare(self.settings);path=base/'queue/test.yaml';path.write_text(yaml.safe_dump(self.req))
        with contextlib.redirect_stdout(io.StringIO()),patch('worker.run_monitored',side_effect=self.bench), \
             patch('worker.run_ttft',side_effect=KeyboardInterrupt()),patch('worker.run_loading') as loading, \
             patch('team.notify.send') as notify:
            with self.assertRaises(KeyboardInterrupt):team.process_one(path,self.settings,base)
        loading.assert_not_called();self.assertTrue((base/'done/test.yaml').exists())
        messages=[c.args[1] for c in notify.call_args_list]
        self.assertEqual(sum('측정 종료:' in m for m in messages),1)


if __name__=='__main__':unittest.main()
