import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from io_monitor import CounterMonitor, parse_io, parse_faults, read_io
from cpu_monitor import MONITOR_FIELDS, run_monitored

class CounterTests(unittest.TestCase):
    def test_io_units_partial_and_zero(self):
        self.assertEqual(parse_io('read_bytes: 0\nrchar: 123\n'), {'io_read_bytes': 0, 'io_rchar_bytes': 123})
        self.assertIsNone(parse_io('read_bytes: -4\nrchar: bad\n')['io_read_bytes'])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_io(123, Path(tmp))['io_read_bytes'])

    def test_fault_fields_exclude_children_and_parentheses(self):
        fields=['S']+['0']*20
        fields[7],fields[8],fields[9],fields[10]='23','999','7','888'
        self.assertEqual(parse_faults('12 (test (worker)) '+' '.join(fields)), {'minor_faults':23,'major_faults':7})
        self.assertIsNone(parse_faults('broken')['minor_faults'])

    def test_gap_uses_actual_interval_and_final_status(self):
        c=CounterMonitor()
        first=c.sample({'io_read_bytes':100},1)
        self.assertEqual(first['io_read_bytes_delta'],100)
        self.assertEqual(first['io_read_bytes_per_s'],100)
        absent=c.sample({},2)
        self.assertIsNone(absent['io_read_bytes'])
        final=c.sample({'io_read_bytes':300},3,final=True)
        self.assertEqual(final['io_read_bytes_delta'],200)
        self.assertEqual(final['io_read_bytes_interval_s'],2)
        self.assertEqual(final['io_read_bytes_per_s'],100)
        self.assertTrue(c.summary()['io_read_bytes']['final_observed'])
        self.assertIsNone(c.summary()['major_faults']['last_observed_total'])

    def test_rollback_is_not_negative_rate_or_fake_zero(self):
        c=CounterMonitor();c.sample({'minor_faults':100},1)
        row=c.sample({'minor_faults':50},2,final=True)
        self.assertEqual(row['minor_faults_status'],'counter_decreased')
        self.assertIsNone(row['minor_faults_delta'])
        self.assertEqual(c.summary()['minor_faults']['last_observed_total'],100)
        self.assertFalse(c.summary()['minor_faults']['final_observed'])

@unittest.skipUnless(sys.platform.startswith('linux'),'Linux /proc counters')
class IntegrationTests(unittest.TestCase):
    def run_child(self, code, timeout=5):
        return run_monitored([sys.executable,'-c',code], self.path, {},timeout,interval=.1)
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'monitor.csv'
        with self.path.open('w',newline='') as f:csv.DictWriter(f,fieldnames=MONITOR_FIELDS).writeheader()
    def test_read_and_fault_totals_survive_exit(self):
        data=Path(self.tmp.name)/'data';data.write_bytes(b'x'*1048576)
        code=f"import time; x=bytearray(16*1024*1024); data=open({str(data)!r},'rb').read(); time.sleep(.25)"
        result=self.run_child(code)
        self.assertEqual(result.returncode,0)
        with self.path.open() as f: rows=list(csv.DictReader(f))
        counts=result.monitor_summary['io_and_faults']
        for key in ('io_read_bytes','io_rchar_bytes','minor_faults','major_faults'):
            self.assertGreater(counts[key]['samples'],0)
            if key in ('minor_faults','major_faults'):
                self.assertTrue(counts[key]['final_observed'],(key,counts))
            self.assertEqual(sum(int(r[key+'_delta']) for r in rows if r[key+'_delta']),counts[key]['last_observed_total'])
            self.assertTrue(all(float(r[key+'_per_s'])>=0 for r in rows if r[key+'_per_s']))
        self.assertGreater(counts['io_rchar_bytes']['last_observed_total'],1048576)
        self.assertGreater(counts['minor_faults']['last_observed_total'],1000)
        self.assertEqual(rows[-1]['memory_status'],'process_exited')
        self.assertIn(rows[-1]['io_read_bytes_status'],('ok','unavailable'))
        if rows[-1]['io_read_bytes_status']=='unavailable':
            self.assertFalse(counts['io_read_bytes']['final_observed'])
            self.assertEqual(rows[-1]['io_read_bytes'],'')
    def test_io_denial_preserves_faults_and_cpu(self):
        with patch('cpu_monitor.read_io',return_value=parse_io('')):
            result=self.run_child('import time; time.sleep(.2)')
        self.assertEqual(result.returncode,0)
        counts=result.monitor_summary['io_and_faults']
        self.assertIsNone(counts['io_read_bytes']['last_observed_total'])
        self.assertGreater(counts['minor_faults']['last_observed_total'],0)
        self.assertGreater(result.monitor_summary['samples'],0)
    def test_timeout_keeps_final_counters(self):
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.run_child('import time; x=bytearray(4*1024*1024); time.sleep(10)',timeout=.3)
        self.assertTrue(caught.exception.monitor_summary['io_and_faults']['minor_faults']['final_observed'])
    def test_nonzero_and_fast_exit(self):
        result=self.run_child('raise SystemExit(7)')
        self.assertEqual(result.returncode,7)
        self.assertTrue(result.monitor_summary['io_and_faults']['minor_faults']['final_observed'])

if __name__=='__main__':unittest.main()
