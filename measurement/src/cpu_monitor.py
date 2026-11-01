"""Linux process CPU sampling without additional packages or privileges."""
import csv
import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

MONITOR_FIELDS = [
    "request_id", "order", "test", "threads", "repeat", "pid", "timestamp",
    "elapsed_s", "interval_s", "cpu_percent", "cpu_time_s",
    "cpu_freq_avg_mhz", "cpu_freq_min_mhz", "cpu_freq_max_mhz",
    "cpu_freq_mhz_by_cpu", "cpu_freq_cpus_total", "cpu_freq_cpus_read",
    "cpu_freq_source", "cpu_freq_status",
]

FREQUENCY_SOURCE = "scaling_cur_freq"


def empty_frequencies(status):
    return {"cpu_freq_avg_mhz": None, "cpu_freq_min_mhz": None,
            "cpu_freq_max_mhz": None, "cpu_freq_mhz_by_cpu": "{}",
            "cpu_freq_cpus_total": None, "cpu_freq_cpus_read": 0,
            "cpu_freq_source": FREQUENCY_SOURCE, "cpu_freq_status": status}


def read_cpu_frequencies(pid, root=Path("/sys/devices/system/cpu")):
    """Snapshots for CPUs allowed by the main thread's affinity, not CPU load weighted."""
    fields = empty_frequencies("unavailable")
    try:
        cpus = sorted(os.sched_getaffinity(pid))
    except (OSError, AttributeError):
        return fields
    values = {}
    for cpu in cpus:
        try:
            khz = float((Path(root) / f"cpu{cpu}" / "cpufreq" / FREQUENCY_SOURCE).read_text().strip())
            values[str(cpu)] = round(khz / 1000, 3) if math.isfinite(khz) and khz > 0 else None
        except (OSError, ValueError):
            values[str(cpu)] = None
    available = [v for v in values.values() if v is not None]
    fields.update(cpu_freq_mhz_by_cpu=json.dumps(values, separators=(",", ":")),
                  cpu_freq_cpus_total=len(cpus), cpu_freq_cpus_read=len(available))
    if available:
        fields.update(cpu_freq_avg_mhz=round(sum(available) / len(available), 3),
                      cpu_freq_min_mhz=min(available), cpu_freq_max_mhz=max(available),
                      cpu_freq_status="ok" if len(available) == len(cpus) else "partial")
    return fields


def parse_cpu_ticks(stat):
    # comm (field 2) can itself contain spaces and parentheses.
    fields = stat.rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])  # utime + stime, all process threads


def run_monitored(cmd, monitor_path, context, timeout, interval=1.0):
    """Return CompletedProcess; attach monitor_summary also to timeouts.

    waitid(WNOWAIT) leaves the exited child readable in /proc until its final
    CPU sample is taken. Output goes to temporary files to avoid pipe blockage.
    No global lock or queue is added here.
    """
    if not sys.platform.startswith("linux"):
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
        result.monitor_summary = {**context, "status": "unsupported", "samples": 0}
        return result
    if interval <= 0:
        raise ValueError("CPU sampling interval must be positive")

    ticks_per_second = os.sysconf("SC_CLK_TCK")
    samples = 0
    max_percent = 0.0
    last_ticks = 0
    last_elapsed = 0.0
    error = None
    started = time.monotonic()
    timed_out = False
    frequency_means = []
    frequency_mins = []
    frequency_maxs = []
    frequency_status_counts = {}

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        with subprocess.Popen(cmd, stdout=stdout, stderr=stderr) as proc:
            # Start elapsed time before launch so short processes include launch latency.
            next_sample = interval
            with Path(monitor_path).open("a", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=MONITOR_FIELDS)

                def sample(process_exited=False):
                    nonlocal samples, last_ticks, last_elapsed, max_percent, error
                    try:
                        ticks = parse_cpu_ticks(Path(f"/proc/{proc.pid}/stat").read_text())
                    except (OSError, ValueError, IndexError) as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        return
                    elapsed = time.monotonic() - started
                    window = elapsed - last_elapsed
                    if window <= 0 or ticks < last_ticks:
                        return
                    percent = (ticks - last_ticks) / ticks_per_second / window * 100
                    # Once the process exits, CPU time for its final interval is still
                    # valid, but a new clock snapshot could describe an idle CPU instead.
                    freq = empty_frequencies("process_exited") if process_exited else read_cpu_frequencies(proc.pid)
                    status = freq["cpu_freq_status"]
                    frequency_status_counts[status] = frequency_status_counts.get(status, 0) + 1
                    if freq["cpu_freq_avg_mhz"] is not None:
                        frequency_means.append(freq["cpu_freq_avg_mhz"])
                        frequency_mins.append(freq["cpu_freq_min_mhz"])
                        frequency_maxs.append(freq["cpu_freq_max_mhz"])
                    writer.writerow({
                        **context, **freq, "pid": proc.pid,
                        "timestamp": datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                        "elapsed_s": f"{elapsed:.6f}", "interval_s": f"{window:.6f}",
                        "cpu_percent": f"{percent:.3f}",
                        "cpu_time_s": f"{ticks / ticks_per_second:.6f}",
                    })
                    output.flush()
                    samples += 1
                    max_percent = max(max_percent, percent)
                    last_ticks, last_elapsed = ticks, elapsed

                try:
                    while True:
                        exited = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                        elapsed = time.monotonic() - started
                        if exited is not None:
                            sample(process_exited=True)
                            break
                        if timeout is not None and elapsed >= timeout:
                            proc.kill()
                            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
                            sample(process_exited=True)
                            timed_out = True
                            break
                        if elapsed >= next_sample:
                            sample()
                            next_sample = elapsed + interval
                        pause = min(0.05, max(0.001, next_sample - elapsed))
                        if timeout is not None:
                            pause = min(pause, max(0.001, timeout - elapsed))
                        time.sleep(pause)
                except BaseException:
                    # Ctrl+C/error must not leave a benchmark running in the background.
                    proc.kill()
                    proc.wait()
                    raise
            returncode = proc.wait()
        stdout.seek(0)
        stderr.seek(0)
        stdout_text = stdout.read().decode("utf-8", errors="replace")
        stderr_text = stderr.read().decode("utf-8", errors="replace")

    summary = {
        **context, "pid": proc.pid,
        "status": "partial" if error and samples else "unavailable" if error else "ok",
        "samples": samples,
        "elapsed_s": round(last_elapsed, 6) if samples else None,
        "cpu_time_s": round(last_ticks / ticks_per_second, 6) if samples else None,
        "avg_cpu_percent": round(last_ticks / ticks_per_second / last_elapsed * 100, 3) if samples else None,
        "max_cpu_percent": round(max_percent, 3) if samples else None,
        "frequency": {
            "source": FREQUENCY_SOURCE, "unit": "MHz",
            "samples": len(frequency_means), "status_counts": frequency_status_counts,
            "mean_sample_avg_mhz": round(sum(frequency_means) / len(frequency_means), 3) if frequency_means else None,
            "min_observed_mhz": min(frequency_mins) if frequency_mins else None,
            "max_observed_mhz": max(frequency_maxs) if frequency_maxs else None,
        },
    }
    if error:
        summary["error"] = error
    if timed_out:
        exc = subprocess.TimeoutExpired(cmd, timeout, output=stdout_text, stderr=stderr_text)
        exc.monitor_summary = summary
        raise exc
    result = subprocess.CompletedProcess(cmd, returncode, stdout_text, stderr_text)
    result.monitor_summary = summary
    return result
