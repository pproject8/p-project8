"""Linux process CPU sampling without additional packages or privileges."""
import csv
import datetime
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

MONITOR_FIELDS = [
    "request_id", "order", "test", "threads", "repeat", "pid", "timestamp",
    "elapsed_s", "interval_s", "cpu_percent", "cpu_time_s",
]


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

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        with subprocess.Popen(cmd, stdout=stdout, stderr=stderr) as proc:
            # Start elapsed time before launch so short processes include launch latency.
            next_sample = interval
            with Path(monitor_path).open("a", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=MONITOR_FIELDS)

                def sample():
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
                    writer.writerow({
                        **context, "pid": proc.pid,
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
                            sample()
                            break
                        if timeout is not None and elapsed >= timeout:
                            proc.kill()
                            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
                            sample()
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
