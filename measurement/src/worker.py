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


def append_rows(path, rows):
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=FIELDS).writerows(rows)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_request(request_path, settings, req_id=None):
    request_path = Path(request_path)
    req = load_yaml(request_path)

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
        csv.DictWriter(f, fieldnames=FIELDS).writeheader()

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
    }
    write_json(out_dir / "meta.json", meta)

    combos = make_combos(req)
    if req.get("shuffle"):
        meta["seed"] = req.get("seed", random.randrange(2 ** 32))
        random.Random(meta["seed"]).shuffle(combos)
    write_json(out_dir / "meta.json", meta)
    print(f"요청 {req_id}: 총 {len(combos)}개 조건")

    with open(out_dir / "run.log", "w", encoding="utf-8") as log:
        for i, (p, n, t) in enumerate(combos, 1):
            cmd = [str(bench), "-m", str(model), "-p", str(p), "-n", str(n),
                   "-t", str(t), "-r", str(req["repeat"]), "-o", "csv"]
            log.write(f"[{i}/{len(combos)}] {' '.join(cmd)}\n")
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace",
                                        timeout=settings.get("condition_timeout_seconds", 3600))
            except subprocess.TimeoutExpired:
                meta["failed"] += 1
                log.write("  실패: 조건 실행 시간 초과\n")
                log.flush()
                write_json(out_dir / "meta.json", meta)
                continue
            if result.returncode != 0:
                meta["failed"] += 1
                log.write(f"  실패 (종료 코드 {result.returncode})\n{result.stderr}\n")
                log.flush()
                print(f"[{i}/{len(combos)}] 실패: p={p} n={n} t={t}")
                write_json(out_dir / "meta.json", meta)
                continue

            rows = parse_rows(result.stdout, req_id, req["repeat"], i)
            append_rows(results_csv, rows)
            meta["ok"] += 1
            write_json(out_dir / "meta.json", meta)
            log.write("  성공\n")
            log.flush()
            for row in rows:
                print(f"[{i}/{len(combos)}] {row['test']} 스레드 {row['threads']} "
                      f"{float(row['avg_ts']):.1f} ± {float(row['stddev_ts']):.1f}")

    meta["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    write_json(out_dir / "meta.json", meta)
    print("결과 폴더:", out_dir)


if __name__ == "__main__":
    request = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    run_request(request, load_yaml("settings.yaml"))
