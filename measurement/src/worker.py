import csv
import datetime
import hashlib
import io
import json
import os
import platform
import random
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from cpu_monitor import MONITOR_FIELDS, run_monitored
from model_loading import validate_loading, run_loading

LOADING_FIELDS = ["request_id", "order", "test", "threads", "repeat", "status",
                  "startup_to_warmup_s", "lower_bound_s", "upper_bound_s"]

FIELDS = ["request_id", "time", "cpu", "build", "model", "threads",
          "repeat", "test", "avg_ts", "stddev_ts", "order"]


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def as_list(x):
    return x if isinstance(x, list) else [x]


def make_combos(req):
    combos = []
    for t in as_list(req["threads"]):
        for p in as_list(req["prompt_tokens"]):
            combos.append((p, 0, t))
        for n in as_list(req["gen_tokens"]):
            combos.append((0, n, t))
    return combos


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_first_line(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def system_info():
    info = {
        "kernel": platform.release(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
    }
    governor = read_first_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if governor:
        info["cpu_governor"] = governor
    if hasattr(os, "getloadavg"):
        info["loadavg_at_start"] = [round(x, 2) for x in os.getloadavg()]
    return info


def code_version():
    """Record the checkout used for this request, independently of llama.cpp."""
    source_dir = Path(__file__).resolve().parent
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
        changes = subprocess.check_output(
            ["git", "-C", str(source_dir), "status", "--porcelain", "--", ".."],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
        return {"pipeline_commit": commit, "pipeline_dirty": bool(changes)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"pipeline_commit": None, "pipeline_dirty": None}


def parse_rows(stdout, req_id, repeat, order):
    rows = []
    for r in csv.DictReader(io.StringIO(stdout)):
        n_p, n_g = int(r["n_prompt"]), int(r["n_gen"])
        test = f"pp{n_p}" if n_g == 0 else f"tg{n_g}"
        rows.append({
            "request_id": req_id,
            "time": r["test_time"],
            "cpu": r["cpu_info"],
            "build": r["build_commit"],
            "model": Path(r["model_filename"]).name,
            "threads": r["n_threads"],
            "repeat": repeat,
            "test": test,
            "avg_ts": r["avg_ts"],
            "stddev_ts": r["stddev_ts"],
            "order": order,
        })
    return rows


def append_rows(path, rows, fields=FIELDS):
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fields).writerows(rows)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_request(request_path, settings, req_id=None):
    request_path = Path(request_path)
    req = load_yaml(request_path)
    validate_loading(req)
    fields=FIELDS+(['load_mode'] if 'load_mode' in req else [])

    bench = Path(settings["bench_path"])
    model = Path(settings["models_dir"]) / req["model"]
    for p in (bench, model):
        if not p.exists():
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {p}")

    started = datetime.datetime.now()
    req_id = req_id or f"{started:%Y%m%d-%H%M%S}-{req.get('name', request_path.stem)}"
    out_dir = Path(settings.get("work_dir", "work")) / "results" / req_id
    out_dir.mkdir(parents=True)

    shutil.copy(request_path, out_dir / "request.yaml")
    results_csv = out_dir / "results.csv"
    with open(results_csv, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()
    monitor_csv = out_dir / "monitor.csv"
    with monitor_csv.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=MONITOR_FIELDS).writeheader()

    loading_csv = out_dir / "loading.csv"
    with loading_csv.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=LOADING_FIELDS).writeheader()

    def save_loading(context, summary):
        loading = summary.get("loading", {"status": "unavailable"})
        with loading_csv.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=LOADING_FIELDS).writerow({**context, **loading})
        return loading

    meta = {
        "request_id": req_id,
        "started": started.isoformat(timespec="seconds"),
        "finished": None,
        "bench_path": str(bench),
        "platform": platform.platform(),
        "python": platform.python_version(),
        **system_info(),
        **code_version(),
        "model_file": model.name,
        "model_size_bytes": model.stat().st_size,
        "model_sha256": sha256_file(model),
        "shuffle": bool(req.get("shuffle", False)),
        "seed": None,
        "ok": 0,
        "failed": 0,
        "loading_measurement": {
            "metric": "startup_to_warmup_s",
            "scope": "monotonic wall time before process launch to first observed llama-bench warmup progress marker; includes startup, backend, model, context and threadpool initialization",
            "precision": "upper bound at polling observation; lower_bound_s is previous observation start; no marker means null",
            "cache": "uncontrolled OS page cache; model hash is read before benchmark, so this is not a cold-load measurement",
            "repetitions": "once per condition process, not once per internal benchmark repetition",
            "conditions": [],
        },
        "cpu_monitor": {
            "backend": "linux_proc_stat" if sys.platform.startswith("linux") else "unsupported",
            "interval_seconds": 1.0,
            "percent_basis": "100% = one logical CPU fully utilized; may exceed 100%",
            "scope": "llama-bench process including loading, warmup and all internal repetitions; excludes child processes",
            "conditions": [],
            "io_and_faults": {
                "sources": "/proc/PID/io read_bytes,rchar and /proc/PID/stat minflt,majflt",
                "scope": "llama-bench process lifetime including loading and warmup; stat child fault fields are excluded; no manual process-tree aggregation",
                "read_bytes": "storage-layer bytes attributed to process, not RAM bandwidth or model-only bytes; cached reads may be zero",
                "rchar": "bytes returned through read-like syscalls including cache hits; not total mmap access bytes; do not subtract read_bytes to estimate cache hits",
                "faults": "minor faults do not require page loading from disk; major faults require page loading; counts are not disk requests or page-count times page-size bytes",
                "deltas": "first baseline zero before launch; each counter uses time since its own last valid observation, including gaps; interval and per-second rate stored separately",
                "final_sample": "counters read before reaping exited child; final_observed=false means last observed cumulative value, not confirmed lifetime total",
                "cache": "page cache unchanged; worker reads model hash before launching bench and may warm cache",
            },
            "memory": {
                "source": "/proc/PID/status", "unit": "bytes (proc kB multiplied by 1024)",
                "scope": "llama-bench process including resident shared/file-backed pages; excludes child processes and whole-system/cgroup totals",
                "fields": "VmRSS resident memory; VmHWM kernel-reported RSS high-water mark; VmSize virtual address space, not physical RAM; VmSwap private anonymous swap, excludes shmem swap",
                "accuracy": "kernel-reported approximate RSS accounting, sampled about once per second; brief peaks may be missed",
                "summary": "unweighted mean of valid RSS snapshots; observed RSS maximum and highest observed VmHWM are distinct; neither guarantees lifetime peak",
                "final_sample": "memory omitted after process exit to avoid treating released memory as zero usage",
            },
            "frequency": {
                "source": "/sys/devices/system/cpu/cpuN/cpufreq/scaling_cur_freq",
                "unit": "MHz (sysfs kHz divided by 1000)",
                "scope": "logical CPUs in sampled main-thread affinity, including idle CPUs; not utilization weighted",
                "interpretation": "driver-reported snapshots, not exact hardware clock or per-process effective frequency",
                "summary": "unweighted mean of valid sample CPU means; min/max across available CPU snapshots",
                "final_sample": "frequency omitted after process exit to avoid reporting post-exit idle clocks",
            },
        },
    }
    write_json(out_dir / "meta.json", meta)

    combos = [(p,n,t,mode) for p,n,t in make_combos(req) for mode in req.get('load_mode',['auto'])]
    if req.get("shuffle"):
        meta["seed"] = req.get("seed", random.randrange(2 ** 32))
        random.Random(meta["seed"]).shuffle(combos)
    write_json(out_dir / "meta.json", meta)
    print(f"요청 {req_id}: 총 {len(combos)}개 조건")

    with open(out_dir / "run.log", "w", encoding="utf-8") as log:
        for i, (p, n, t, mode) in enumerate(combos, 1):
            cmd = [str(bench), "-m", str(model), "-p", str(p), "-n", str(n),
                   "-t", str(t), "-r", str(req["repeat"]), "-o", "csv", "--progress"]
            if 'load_mode' in req:
                cmd.extend(['-lm',mode])
            log.write(f"[{i}/{len(combos)}] {' '.join(cmd)}\n")
            context = {"request_id": req_id, "order": i,
                       "test": f"pp{p}" if n == 0 else f"tg{n}",
                       "threads": t, "repeat": req["repeat"]}
            try:
                result = run_monitored(cmd, monitor_csv, context,
                                       timeout=settings.get("condition_timeout_seconds", 3600))
            except subprocess.TimeoutExpired as exc:
                meta["cpu_monitor"]["conditions"].append(
                    getattr(exc, "monitor_summary", {**context, "status": "unavailable", "samples": 0}))
                loading = save_loading(context, getattr(exc, "monitor_summary", {}))
                meta["loading_measurement"]["conditions"].append({**context, **loading})
                if exc.stderr:
                    log.write(exc.stderr)
                meta["failed"] += 1
                log.write("  실패: 조건 실행 시간 초과\n")
                log.flush()
                write_json(out_dir / "meta.json", meta)
                continue
            summary = getattr(result, "monitor_summary", {**context, "status": "unavailable", "samples": 0})
            meta["cpu_monitor"]["conditions"].append(summary)
            loading = save_loading(context, summary)
            meta["loading_measurement"]["conditions"].append({**context, **loading})
            if result.stderr:
                log.write(result.stderr + "\n")
            if loading.get("startup_to_warmup_s") is not None:
                print(f"[{i}/{len(combos)}] 준비 시간 {loading['startup_to_warmup_s']:.3f}초 (워밍업 직전까지)")
            if summary["status"] != "ok":
                log.write(f"  CPU 기록 상태: {summary['status']} {summary.get('error', '')}\n")
            if result.returncode != 0:
                meta["failed"] += 1
                log.write(f"  실패 (종료 코드 {result.returncode})\n{result.stderr}\n")
                log.flush()
                print(f"[{i}/{len(combos)}] 실패: p={p} n={n} t={t}")
                write_json(out_dir / "meta.json", meta)
                continue

            rows = parse_rows(result.stdout, req_id, req["repeat"], i)
            append_rows(results_csv, [{**row,**({'load_mode':mode} if 'load_mode' in req else {})} for row in rows],fields)
            meta["ok"] += 1
            write_json(out_dir / "meta.json", meta)
            log.write("  성공\n")
            log.flush()
            for row in rows:
                print(f"[{i}/{len(combos)}] {row['test']} 스레드 {row['threads']} "
                      f"{float(row['avg_ts']):.1f} ± {float(row['stddev_ts']):.1f}")

    print('모델 로딩 시간 측정 중 (준비 시간과 별도, 프로세스별 독립 반복)',flush=True)
    meta['model_loading']=run_loading(req,settings,model,out_dir,req_id)
    meta["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    write_json(out_dir / "meta.json", meta)
    print("결과 폴더:", out_dir)


if __name__ == "__main__":
    request = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    run_request(request, load_yaml("settings.yaml"))
