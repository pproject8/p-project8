import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cpu_monitor import MONITOR_FIELDS, parse_cpu_ticks, read_cpu_frequencies, run_monitored


class StatParsingTests(unittest.TestCase):
    def test_command_name_with_spaces_and_parentheses(self):
        fields = ["S"] + ["0"] * 10 + ["123", "45"]
        self.assertEqual(parse_cpu_ticks("123 (worker name (x)) " + " ".join(fields)), 168)


class FrequencyReadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def set_frequency(self, cpu, value):
        p = self.root / f"cpu{cpu}" / "cpufreq" / "scaling_cur_freq"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value)

    def test_affinity_scope_and_khz_to_mhz(self):
        self.set_frequency(0, "3200000")
        self.set_frequency(3, "1800000")
        self.set_frequency(8, "9999999")  # Not permitted by the sampled affinity.
        with patch("cpu_monitor.os.sched_getaffinity", return_value={0, 3}, create=True):
            result = read_cpu_frequencies(123, self.root)
        self.assertEqual(result["cpu_freq_avg_mhz"], 2500.0)
        self.assertEqual(result["cpu_freq_status"], "ok")
        self.assertEqual(json.loads(result["cpu_freq_mhz_by_cpu"]), {"0": 3200.0, "3": 1800.0})

    def test_missing_cpu_is_null_not_zero(self):
        self.set_frequency(0, "2000000")
        with patch("cpu_monitor.os.sched_getaffinity", return_value={0, 2}, create=True):
            result = read_cpu_frequencies(123, self.root)
        self.assertEqual(result["cpu_freq_avg_mhz"], 2000.0)
        self.assertEqual(result["cpu_freq_status"], "partial")
        self.assertEqual(result["cpu_freq_cpus_read"], 1)
        self.assertIsNone(json.loads(result["cpu_freq_mhz_by_cpu"])["2"])

    def test_invalid_frequency_does_not_create_false_values(self):
        for cpu, value in enumerate(("nan", "0", "-100", "invalid")):
            self.set_frequency(cpu, value)
        with patch("cpu_monitor.os.sched_getaffinity", return_value=set(range(4)), create=True):
            result = read_cpu_frequencies(123, self.root)
        self.assertEqual(result["cpu_freq_status"], "unavailable")
        self.assertIsNone(result["cpu_freq_avg_mhz"])

    def test_affinity_permission_failure(self):
        with patch("cpu_monitor.os.sched_getaffinity", side_effect=PermissionError(), create=True):
            result = read_cpu_frequencies(123, self.root)
        self.assertEqual(result["cpu_freq_status"], "unavailable")
        self.assertEqual(result["cpu_freq_cpus_read"], 0)


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires Linux /proc and waitid")
class CpuMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "monitor.csv"
        with self.path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=MONITOR_FIELDS).writeheader()
        self.context = dict(request_id="test", order=1, test="pp32", threads=1, repeat=1)

    def run_child(self, code, timeout=5):
        return run_monitored([sys.executable, "-c", code], self.path, self.context, timeout, interval=0.1)

    def rows(self):
        with self.path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_cpu_intervals_and_large_output_without_pipe_deadlock(self):
        result = self.run_child("import sys,time; sys.stdout.write('x'*150000); sys.stderr.write('y'*150000); end=time.monotonic()+0.35\nwhile time.monotonic()<end: pass")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), 150000)
        self.assertEqual(len(result.stderr), 150000)
        rows = self.rows()
        self.assertGreaterEqual(len(rows), 2)
        self.assertTrue(any(float(r["cpu_percent"]) > 0 for r in rows))
        self.assertTrue(all(r["request_id"] == "test" and r["test"] == "pp32" for r in rows))
        elapsed = [float(r["elapsed_s"]) for r in rows]
        self.assertTrue(all(a < b for a, b in zip(elapsed, elapsed[1:])))
        integrated = sum(float(r["cpu_percent"]) / 100 * float(r["interval_s"]) for r in rows)
        self.assertAlmostEqual(integrated, result.monitor_summary["cpu_time_s"], delta=0.002)
        self.assertEqual(result.monitor_summary["status"], "ok")
        self.assertEqual(result.monitor_summary["samples"], len(rows))

    def test_short_process_has_final_sample(self):
        result = self.run_child("print('fast')")
        self.assertEqual(result.stdout.strip(), "fast")
        self.assertGreaterEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[-1]["cpu_freq_status"], "process_exited")
        self.assertEqual(self.rows()[-1]["cpu_freq_avg_mhz"], "")

    def test_nonzero_exit_keeps_cpu_samples(self):
        result = self.run_child("import sys; sys.exit(7)")
        self.assertEqual(result.returncode, 7)
        self.assertGreaterEqual(len(self.rows()), 1)

    def test_timeout_records_cpu_and_reaps_child(self):
        with self.assertRaises(subprocess.TimeoutExpired) as ctx:
            self.run_child("import time; time.sleep(10)", timeout=0.2)
        self.assertGreaterEqual(len(self.rows()), 1)
        pid = ctx.exception.monitor_summary["pid"]
        with self.assertRaises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)

    def test_unavailable_cpu_data_does_not_invent_zero_usage(self):
        with patch("cpu_monitor.parse_cpu_ticks", side_effect=ValueError("unreadable stat")):
            result = self.run_child("print('still succeeds')")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.monitor_summary["status"], "unavailable")
        self.assertIsNone(result.monitor_summary["avg_cpu_percent"])
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
