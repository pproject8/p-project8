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
from cpu_monitor import MONITOR_FIELDS, parse_cpu_ticks, read_cpu_frequencies, run_monitored, parse_memory_status, read_process_memory


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




@unittest.skipUnless(sys.platform.startswith("linux"), "Linux process monitoring")
class LoadingBoundaryTests(unittest.TestCase):
    def test_marker_bounds_exclude_later_computation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "monitor.csv"
            code = "import sys,time; time.sleep(.15); print('llama-bench: benchmark 1/1: warmup prompt run',file=sys.stderr,flush=True); time.sleep(.3)"
            result = run_monitored([sys.executable, "-c", code], output, {}, 5)
            loading = result.monitor_summary["loading"]
            self.assertEqual(loading["status"], "ok")
            self.assertGreaterEqual(loading["upper_bound_s"], .15)
            self.assertLess(loading["lower_bound_s"], loading["upper_bound_s"])
            self.assertLess(loading["upper_bound_s"], result.monitor_summary["elapsed_s"])
            self.assertIn("warmup prompt run", result.stderr)

    def test_missing_marker_is_null_even_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_monitored([sys.executable, "-c", "raise SystemExit(2)"], Path(tmp)/"monitor.csv", {}, 5)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.monitor_summary["loading"]["status"], "marker_missing")
            self.assertIsNone(result.monitor_summary["loading"]["startup_to_warmup_s"])

    def test_timeout_preserves_observed_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = "import sys,time; print('llama-bench: benchmark 1/1: warmup generation run',file=sys.stderr,flush=True); time.sleep(5)"
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                run_monitored([sys.executable, "-c", code], Path(tmp)/"monitor.csv", {}, .25)
            self.assertEqual(caught.exception.monitor_summary["loading"]["status"], "ok")


class MemoryParsingTests(unittest.TestCase):
    def test_units_and_real_zero_swap(self):
        result = parse_memory_status("VmRSS: 1024 kB\nVmHWM: 2048 kB\nVmSize: 4096 kB\nVmSwap: 0 kB\n")
        self.assertEqual(result["memory_rss_bytes"], 1048576)
        self.assertEqual(result["memory_hwm_bytes"], 2097152)
        self.assertEqual(result["memory_vms_bytes"], 4194304)
        self.assertEqual(result["memory_swap_bytes"], 0)
        self.assertEqual(result["memory_status"], "ok")

    def test_bad_and_missing_fields_are_not_zero(self):
        result = parse_memory_status("VmRSS: 10 kB\nVmHWM: -1 kB\nVmSize: bad kB\nVmSwap: 7 MB\n")
        self.assertEqual(result["memory_status"], "partial")
        for key in ("memory_hwm_bytes", "memory_vms_bytes", "memory_swap_bytes"):
            self.assertIsNone(result[key])
        self.assertEqual(parse_memory_status("State: Z\n")["memory_status"], "unavailable")

    def test_missing_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = read_process_memory(123, Path(tmp))
        self.assertEqual(result["memory_status"], "unavailable")
        self.assertIsNone(result["memory_rss_bytes"])


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux memory monitoring")
class MemoryIntegrationTests(unittest.TestCase):
    setUp = CpuMonitorTests.setUp
    run_child = CpuMonitorTests.run_child
    rows = CpuMonitorTests.rows
    def test_allocated_memory_and_final_null(self):
        result = self.run_child("import time; x=bytearray(64*1024*1024); time.sleep(.4)")
        rows = self.rows()
        valid = [r for r in rows if r["memory_status"] == "ok"]
        self.assertTrue(valid)
        self.assertTrue(any(int(r["memory_rss_bytes"]) >= 64*1024*1024 for r in valid))
        summary = result.monitor_summary["memory"]
        rss = [int(r["memory_rss_bytes"]) for r in valid]
        self.assertEqual(summary["rss_samples"], len(valid))
        self.assertEqual(summary["max_observed_rss_bytes"], max(rss))
        self.assertAlmostEqual(summary["mean_sample_rss_bytes"], sum(rss)/len(rss), delta=.001)
        self.assertEqual(rows[-1]["memory_status"], "process_exited")
        self.assertEqual(rows[-1]["memory_rss_bytes"], "")

    def test_unreadable_memory_keeps_cpu_and_success(self):
        with patch("cpu_monitor.read_process_memory", return_value=__import__('cpu_monitor').empty_memory("unavailable")):
            result = self.run_child("import time; time.sleep(.25)")
        self.assertEqual(result.returncode, 0)
        self.assertGreater(result.monitor_summary["samples"], 0)
        self.assertIsNone(result.monitor_summary["memory"]["mean_sample_rss_bytes"])
        self.assertTrue(all(r["memory_rss_bytes"] == "" for r in self.rows()))

    def test_timeout_preserves_memory(self):
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.run_child("import time; x=bytearray(16*1024*1024); time.sleep(5)", timeout=.35)
        self.assertGreater(caught.exception.monitor_summary["memory"]["rss_samples"], 0)
        self.assertIsNotNone(caught.exception.monitor_summary["memory"]["max_observed_rss_bytes"])


if __name__ == "__main__":
    unittest.main()
