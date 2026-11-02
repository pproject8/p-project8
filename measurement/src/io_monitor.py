"""Per-process cumulative I/O and fault counters with gap-aware deltas."""
from pathlib import Path

COUNTERS = {
    "io_read_bytes": "/proc/PID/io:read_bytes",
    "io_rchar_bytes": "/proc/PID/io:rchar",
    "minor_faults": "/proc/PID/stat:minflt",
    "major_faults": "/proc/PID/stat:majflt",
}
COUNTER_FIELDS = [field for key in COUNTERS for field in
                  (key, key + "_delta", key + "_interval_s", key + "_per_s", key + "_status")]


def parse_io(text):
    values = {"io_read_bytes": None, "io_rchar_bytes": None}
    names = {"read_bytes": "io_read_bytes", "rchar": "io_rchar_bytes"}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep and name in names:
            try:
                number = int(value.strip())
                if number >= 0:
                    values[names[name]] = number
            except ValueError:
                pass
    return values


def read_io(pid, root=Path("/proc")):
    try:
        return parse_io((Path(root) / str(pid) / "io").read_text())
    except OSError:
        return parse_io("")


def parse_faults(stat):
    try:
        fields = stat.rsplit(")", 1)[1].split()
        minor, major = int(fields[7]), int(fields[9])
        return {"minor_faults": minor if minor >= 0 else None,
                "major_faults": major if major >= 0 else None}
    except (ValueError, IndexError):
        return {"minor_faults": None, "major_faults": None}


class CounterMonitor:
    def __init__(self):
        self.last = {key: (0, 0.0) for key in COUNTERS}
        self.samples = {key: 0 for key in COUNTERS}
        self.max_rates = {key: None for key in COUNTERS}
        self.final_observed = {key: False for key in COUNTERS}
        self.status_counts = {key: {} for key in COUNTERS}

    def sample(self, values, elapsed, final=False):
        row = {}
        for key in COUNTERS:
            value = values.get(key)
            previous, previous_time = self.last[key]
            duration = elapsed - previous_time
            status = "unavailable" if value is None else "counter_decreased" if value < previous else "ok"
            if duration <= 0:
                status = "invalid_interval"
            self.status_counts[key][status] = self.status_counts[key].get(status, 0) + 1
            valid = status == "ok"
            delta = value - previous if valid else None
            rate = delta / duration if valid else None
            row.update({key: value if valid else None,
                        key + "_delta": delta,
                        key + "_interval_s": round(duration, 6) if valid else None,
                        key + "_per_s": round(rate, 6) if valid else None,
                        key + "_status": status})
            if valid:
                self.last[key] = (value, elapsed)
                self.samples[key] += 1
                maximum = self.max_rates[key]
                self.max_rates[key] = rate if maximum is None else max(maximum, rate)
                if final:
                    self.final_observed[key] = True
        return row

    def summary(self):
        return {key: {
            "source": source, "samples": self.samples[key],
            "last_observed_total": self.last[key][0] if self.samples[key] else None,
            "last_observed_elapsed_s": round(self.last[key][1], 6) if self.samples[key] else None,
            "final_observed": self.final_observed[key],
            "mean_per_s_since_launch": round(self.last[key][0] / self.last[key][1], 6) if self.samples[key] else None,
            "max_observed_per_s": round(self.max_rates[key], 6) if self.samples[key] else None,
            "status_counts": self.status_counts[key],
        } for key, source in COUNTERS.items()}
