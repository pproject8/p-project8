import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import summary

class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.results=self.root/'results';self.results.mkdir()
        self.settings=self.root/'settings.yaml';self.settings.write_text('work_dir: '+self.root.as_posix())
    def result(self,name='20261102-000000-test',meta=None):
        p=self.results/name;p.mkdir()
        data={'finished':'2026-11-02','ok':1,'failed':0,'model_file':'model.gguf'} if meta is None else meta
        (p/'meta.json').write_text(json.dumps(data))
        (p/'results.csv').write_text('order,test,threads,repeat,avg_ts,stddev_ts\n1,pp512,12,10,80,1\n')
        return p
    def run_summary(self,*args):
        output=io.StringIO();errors=io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(errors):
            rc=summary.main(['--settings',str(self.settings),*args])
        return rc,output.getvalue(),errors.getvalue()
    def test_latest_and_explicit_id_use_personal_settings(self):
        older=self.result();newer=self.result('20261102-000001-new')
        rc,out,err=self.run_summary();self.assertEqual(rc,0);self.assertIn(newer.name,out);self.assertNotIn(older.name,out)
        rc,out,err=self.run_summary(older.name);self.assertEqual(rc,0);self.assertIn(older.name,out)
    def test_old_results_show_missing_not_fake_zero(self):
        self.result();rc,out,err=self.run_summary()
        self.assertEqual(rc,0);self.assertIn('미기록',out);self.assertIn('80.00 ± 1.00',out)
        self.assertNotIn('0 bytes',out)
    def test_zero_and_unconfirmed_io_remain_distinct(self):
        c={'order':1,'test':'pp512','io_and_faults':{'io_read_bytes':{'last_observed_total':0,'final_observed':False,'last_observed_elapsed_s':3},'major_faults':{'last_observed_total':0,'final_observed':True}}}
        self.result(meta={'finished':'yes','ok':1,'failed':0,'cpu_monitor':{'conditions':[c]}})
        rc,out,err=self.run_summary();self.assertEqual(rc,0)
        self.assertIn('0 bytes (마지막 관측값',out);self.assertIn('major 페이지 폴트: 0회',out)
    def test_incomplete_latest_does_not_silently_show_old_result(self):
        self.result();self.result('20261102-000002-running',{'finished':None})
        rc,out,err=self.run_summary();self.assertEqual(rc,1);self.assertIn('미완료',err);self.assertNotIn('TPS',out)
    def test_failure_without_tps_is_visible(self):
        self.result(meta={'finished':'yes','ok':0,'failed':1,'cpu_monitor':{'conditions':[{'order':2,'test':'tg128'}]}})
        rc,out,err=self.run_summary();self.assertEqual(rc,0);self.assertIn('실패 1',out);self.assertIn('결과 없음',out)
    def test_empty_or_bad_id_returns_readable_error(self):
        rc,out,err=self.run_summary();self.assertEqual(rc,1);self.assertIn('결과가 없습니다',err)
        rc,out,err=self.run_summary('../elsewhere');self.assertEqual(rc,1);self.assertIn('요청 ID',err)

if __name__=='__main__':unittest.main()
