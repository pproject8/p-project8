import csv
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cpu_monitor import MONITOR_FIELDS, parse_cpu_ticks, run_monitored


class StatParsingTests(unittest.TestCase):
    def test_command_name_with_spaces_and_parentheses(self):
        fields = ["S"] + ["0"] * 10 + ["123", "45"]
        self.assertEqual(parse_cpu_ticks("123 (worker name (x)) " + " ".join(fields)), 168)


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
