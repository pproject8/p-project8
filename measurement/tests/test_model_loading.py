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

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import model_loading as loading
import worker
import summary
import yaml


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.model=self.root/'model.gguf';self.model.write_bytes(b'x'*100)
        self.settings=dict(bench_path=str(self.root/'bench'),work_dir=str(self.root))
        p=patch('model_loading.prepare_helper',return_value=(Path('/helper'),Path('/libs'),{'llama_revision':'test'}));p.start();self.addCleanup(p.stop)

    def test_options_and_parse_reject_invalid_values(self):
        for req in ({'loading_repeat':True},{'loading_repeat':0},{'load_mode':[]},{'load_mode':['bad']},{'cache':'unknown'}):
            with self.subTest(req=req),self.assertRaises(ValueError):
                loading.validate_loading(req)
        for value in (0,-1,True,float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):
                loading.parse_measurement(json.dumps({'model_load_s':value}))

    def test_busy_notebook_prevents_cache_drop(self):
        with patch('model_loading.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'123',b'')) as run:
            with self.assertRaisesRegex(RuntimeError,'캐시'):
                loading.clear_cache({'drop_caches_path':str(self.model)})
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0][0],'pgrep')

    def test_unsupported_abi_is_rejected_before_build_or_network(self):
        # Bypass setUp's helper mock to exercise the real version guard.
        import importlib
        spec=importlib.util.spec_from_file_location('loading_abi_guard',Path(loading.__file__))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module.sys,'platform','linux'),patch.object(module.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'version other-build','')),patch.object(module.urllib.request,'urlopen') as network:
            with self.assertRaisesRegex(RuntimeError,'ABI'):
                module.prepare_helper(self.settings,io.StringIO())
            network.assert_not_called()

    def test_each_repeat_clears_cache_then_starts_a_new_process(self):
        events=[]
        def clear(settings):events.append('clear')
        def execute(cmd,*args,**kwargs):
            self.assertEqual(events[-1],'clear')
            events.append('process')
            return subprocess.CompletedProcess(cmd,0,'{"model_load_s":0.25}',''),0.5
        with patch('model_loading.clear_cache',side_effect=clear),patch('model_loading.run_timed_process',side_effect=execute) as run:
            info=loading.run_loading(dict(repeat=3,load_mode=['mmap','none']),self.settings,self.model,self.root,'test')
        self.assertEqual(run.call_count,6)
        self.assertEqual(events,['clear','process']*6)
        self.assertEqual(info['ok'],6)
        self.assertEqual(info['conditions'][0]['mean_s'],0.5)
        self.assertEqual(info['conditions'][0]['stddev_s'],0)
        with (self.root/'model_loading.csv').open(encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),6)
            self.assertEqual(rows[0]['model_load_api_s'],'0.25')
            self.assertEqual(rows[0]['measurement_method'],loading.METHOD)

    def test_cold_cache_failure_never_falls_back_to_warm(self):
        with patch('model_loading.clear_cache',side_effect=RuntimeError('not registered')),patch('model_loading.subprocess.run') as execute,patch('model_loading.warm_model') as warm:
            info=loading.run_loading(dict(repeat=2),self.settings,self.model,self.root,'test')
        execute.assert_not_called();warm.assert_not_called()
        self.assertEqual((info['ok'],info['failed']),(0,2))

    def test_warm_reads_model_before_every_process(self):
        with patch('model_loading.warm_model') as warm,patch('model_loading.clear_cache') as clear,patch('model_loading.run_timed_process',return_value=(subprocess.CompletedProcess([],0,'{"model_load_s":1}',''),1.25)):
            info=loading.run_loading(dict(repeat=10,loading_repeat=2,cache='warm'),self.settings,self.model,self.root,'test')
        self.assertEqual(warm.call_count,2);clear.assert_not_called()
        self.assertEqual(info['ok'],2)

    def test_timeout_and_failure_do_not_become_zero_or_stop_later_repeat(self):
        failures=[subprocess.TimeoutExpired('helper',1),(subprocess.CompletedProcess([],1,'',''),None),(subprocess.CompletedProcess([],0,'{"model_load_s":1}',''),1.25)]
        with patch('model_loading.clear_cache'),patch('model_loading.run_timed_process',side_effect=failures):
            info=loading.run_loading(dict(repeat=3),self.settings,self.model,self.root,'test')
        self.assertEqual((info['ok'],info['failed']),(1,2))
        with (self.root/'model_loading.csv').open(encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(rows[0]['model_load_s'],'')
        self.assertIsNone(info['conditions'][0]['stddev_s'])

    def test_missing_helper_preserves_failure_reason(self):
        with patch('model_loading.prepare_helper',side_effect=RuntimeError('wrong ABI')):
            info=loading.run_loading(dict(repeat=1),self.settings,self.model,self.root,'test')
        self.assertEqual(info['status'],'unavailable')
        self.assertIn('wrong ABI',(self.root/'model_loading.csv').read_text(encoding='utf-8'))

    def test_memory_limited_request_never_launches_unlimited_helper(self):
        with patch('model_loading.subprocess.run') as run:
            info=loading.run_loading(dict(repeat=1,memory_max=[.8]),self.settings,self.model,self.root,'test')
        run.assert_not_called();self.assertEqual(info['status'],'disabled')

    def test_summary_keeps_preparation_and_model_load_separate(self):
        meta={'finished':'yes','ok':1,'failed':0,
              'cpu_monitor':{'conditions':[dict(order=1,test='pp32',loading={'startup_to_warmup_s':1.2})]},
              'model_loading':{'conditions':[dict(load_mode='mmap',cache='cold',ok=2,failed=0,mean_s=.3,stddev_s=.01,cv_percent=3.33)]}}
        (self.root/'meta.json').write_text(json.dumps(meta),encoding='utf-8')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):summary.show(self.root)
        self.assertIn('1.200초',output.getvalue());self.assertIn('0.300초',output.getvalue())
        self.assertIn('cold',output.getvalue());self.assertIn('준비 시간',output.getvalue())

    def test_worker_passes_modes_to_bench_and_preserves_old_header_by_default(self):
        (self.root/'bench').touch()
        request=self.root/'request.yaml'
        request.write_text(yaml.safe_dump(dict(model=self.model.name,threads=[1],prompt_tokens=[8],gen_tokens=[4],repeat=1,load_mode=['mmap','none'],measure_loading=False)))
        calls=[]
        def runner(cmd,*args,**kwargs):
            calls.append(cmd)
            p,n=cmd[cmd.index('-p')+1],cmd[cmd.index('-n')+1]
            return subprocess.CompletedProcess(cmd,0,f'n_prompt,n_gen,test_time,cpu_info,build_commit,model_filename,n_threads,avg_ts,stddev_ts\n{p},{n},now,cpu,build,model.gguf,1,10,1\n','')
        with patch('worker.run_monitored',side_effect=runner),patch('worker.code_version',return_value={}),patch('worker.system_info',return_value={}),contextlib.redirect_stdout(io.StringIO()):
            worker.run_request(request,{**self.settings,'models_dir':str(self.root)},'test')
        self.assertEqual([cmd[cmd.index('-lm')+1] for cmd in calls],['mmap','none','mmap','none'])
        with (self.root/'results/test/results.csv').open(encoding='utf-8') as f:
            self.assertEqual([r['load_mode'] for r in csv.DictReader(f)],['mmap','none','mmap','none'])


    def test_explicit_cold_without_drop_helper_fails_before_benchmark(self):
        (self.root/'bench').touch()
        request=self.root/'request.yaml'
        request.write_text(yaml.safe_dump(dict(model=self.model.name,threads=[1],prompt_tokens=[8],gen_tokens=[4],repeat=1,cache='cold')))
        settings={**self.settings,'models_dir':str(self.root),'drop_caches_path':str(self.root/'missing')}
        with patch('worker.run_monitored') as bench,self.assertRaisesRegex(ValueError,'캐시 비우기'):
            worker.run_request(request,settings,'test')
        bench.assert_not_called()
        self.assertFalse((self.root/'results/test').exists())

    def test_cold_helper_check_skips_default_warm_and_disabled_loading(self):
        settings={'drop_caches_path':str(self.root/'missing')}
        for req in ({},{'cache':'warm'},{'cache':'cold','measure_loading':False},{'cache':'cold','memory_max':'1G'}):
            with self.subTest(req=req),patch('model_loading.subprocess.run') as run:
                loading.check_cold_cache_ready(req,settings)
                run.assert_not_called()

    def test_explicit_cold_requires_absolute_helper_allowed_by_sudo(self):
        req={'cache':'cold'}
        with self.assertRaisesRegex(ValueError,'절대 경로'):
            loading.check_cold_cache_ready(req,{'drop_caches_path':'bin/pp8-drop-caches'})
        with patch('model_loading.subprocess.run',return_value=subprocess.CompletedProcess([],1)),self.assertRaisesRegex(ValueError,'sudo'):
            loading.check_cold_cache_ready(req,{'drop_caches_path':str(self.model)})
        with patch('model_loading.subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run:
            loading.check_cold_cache_ready(req,{'drop_caches_path':str(self.model)})
        self.assertEqual(run.call_args.args[0],['sudo','-n','-l',str(self.model)])

if __name__=='__main__':unittest.main()
