"""Trusted-team CLI for a single Linux measurement queue."""
import argparse
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

import yaml
from worker import load_yaml, run_request

ROOT = Path(__file__).resolve().parents[1]
STATES = ("queue", "running", "done", "failed")


def prepare(settings):
    base = Path(settings["work_dir"])
    for key in ("bench_path", "models_dir", "work_dir"):
        if not Path(settings[key]).is_absolute():
            raise ValueError(f"settings의 {key}는 절대 경로여야 합니다")
    for name in (*STATES, "results"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return base


def validate(req, settings):
    if not isinstance(req, dict):
        raise ValueError("요청은 YAML 객체여야 합니다")
    model = req.get("model")
    if not isinstance(model, str) or not model or model in (".", "..") or "/" in model or "\\" in model:
        raise ValueError("model은 models_dir 안의 파일 이름만 지정하세요")
    if not (Path(settings["models_dir"]) / model).is_file():
        raise ValueError(f"모델 파일이 없습니다: {model}")
    for key in ("repeat", "threads", "prompt_tokens", "gen_tokens"):
        value = req.get(key)
        values = value if isinstance(value, list) else [value]
        if not values or any(type(v) is not int or v < 1 for v in values):
            raise ValueError(f"{key}는 양의 정수 또는 양의 정수 목록이어야 합니다")
        if key == "repeat" and type(value) is not int:
            raise ValueError("repeat는 정수여야 합니다")
    if "shuffle" in req and type(req["shuffle"]) is not bool:
        raise ValueError("shuffle은 true/false로 지정하세요")
    if "seed" in req and type(req["seed"]) is not int:
        raise ValueError("seed는 정수여야 합니다")


def submit(args, settings, base):
    preset = ROOT / "requests" / (args.request + ".yaml")
    source = preset if args.request in ("baseline", "threads") else Path(args.request)
    req = load_yaml(source)
    if isinstance(req, dict):
        if source == preset and settings.get("default_model"):
            req["model"] = settings["default_model"]
        for key in ("model", "threads", "repeat", "prompt_tokens", "gen_tokens"):
            value = getattr(args, key, None)
            if value is not None:
                req[key] = value
    validate(req, settings)
    user = re.sub(r"[^a-zA-Z0-9_-]", "_", getpass.getuser())[:40] or "user"
    ident = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + user + "-" + uuid.uuid4().hex[:8]
    req["submitted_by"] = getpass.getuser()
    req["submitted_at"] = dt.datetime.now().astimezone().isoformat()
    tmp = base / "queue" / ("." + ident + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as f:
            yaml.safe_dump(req, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, base / "queue" / (ident + ".yaml"))
    finally:
        tmp.unlink(missing_ok=True)
    print(f"제출 완료: {ident}\n상태 확인: pp8 status {ident}\n결과 확인: pp8 result {ident}")
    return ident


def check_id(ident):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", ident):
        raise ValueError("출력된 요청 ID를 그대로 사용하세요")


def outcome(base, ident, state):
    path = base / "results" / ident / "meta.json"
    if state == "done" and path.is_file():
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            return "partial" if meta.get("failed", 0) else "done"
        except (ValueError, OSError):
            pass
    return state


def status(base, ident=None):
    if ident:
        check_id(ident)
    found = []
    for state in STATES:
        for p in (base / state).glob("*.yaml"):
            if ident is None or ident == p.stem:
                found.append((p.stem, outcome(base, p.stem, state)))
    for request_id, state in sorted(found):
        print(f"{state:8} {request_id}")
    if not found:
        print("요청이 없습니다.")


def result(base, ident):
    check_id(ident)
    status(base, ident)
    folder = base / "results" / ident
    if not folder.is_dir():
        print("아직 결과가 없습니다.")
        return
    print(f"결과 폴더: {folder}")
    for name in ("meta.json", "results.csv", "error.txt"):
        path = folder / name
        if path.is_file():
            print(f"\n--- {name} ---\n{path.read_text(encoding='utf-8')}")


def process_one(path, settings, base):
    running = base / "running" / path.name
    path.rename(running)
    folder = base / "results" / path.stem
    print(f"시작: {path.stem}", flush=True)
    try:
        validate(load_yaml(running), settings)
        run_request(running, settings, req_id=path.stem)
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        target = "failed" if meta.get("ok", 0) == 0 else "done"
    except Exception as exc:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        target = "failed"
    running.rename(base / target / running.name)
    print(f"종료: {path.stem} ({outcome(base, path.stem, target)})", flush=True)


def worker(settings, base, once=False):
    if sys.platform != "linux":
        raise ValueError("공용 워커는 Linux 노트북에서 실행하세요")
    import fcntl
    with (base / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("이 공용 큐의 워커가 이미 실행 중입니다")
        # Only recover after acquiring the exclusive lock.
        for path in (base / "running").glob("*.yaml"):
            folder = base / "results" / path.stem
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "error.txt").write_text("워커 중단 후 복구됨. 새 요청으로 다시 제출하세요.\n", encoding="utf-8")
            path.rename(base / "failed" / path.name)
        print("공용 워커 대기 중 (종료: Ctrl+C)", flush=True)
        while True:
            requests = sorted((base / "queue").glob("*.yaml"))
            if requests:
                process_one(requests[0], settings, base)
            if once:
                return
            if not requests:
                time.sleep(2)


def main():
    os.umask(0o007)
    parser = argparse.ArgumentParser(description="pp8 공용 측정 요청")
    parser.add_argument("--settings", default=os.environ.get("PP8_SETTINGS", str(ROOT / "settings.yaml")))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("submit", help="요청 제출 (측정은 공용 워커가 수행)")
    p.add_argument("request", help="baseline, threads 또는 YAML 파일 경로")
    p.add_argument("--model")
    p.add_argument("--threads", type=int, nargs="+")
    p.add_argument("--repeat", type=int)
    p.add_argument("--prompt", dest="prompt_tokens", type=int, nargs="+")
    p.add_argument("--gen", dest="gen_tokens", type=int, nargs="+")
    p = sub.add_parser("status", help="queue/running/done/partial/failed 확인")
    p.add_argument("id", nargs="?")
    p = sub.add_parser("result", help="결과 폴더 및 CSV 확인")
    p.add_argument("id")
    p = sub.add_parser("worker", help="관리자가 실행하는 단일 워커")
    p.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        settings = load_yaml(args.settings)
        base = prepare(settings)
        if args.command == "submit":
            submit(args, settings, base)
        elif args.command == "status":
            status(base, args.id)
        elif args.command == "result":
            result(base, args.id)
        else:
            worker(settings, base, args.once)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        parser.exit(1, f"오류: {exc}\n")
    except KeyboardInterrupt:
        print("\n워커 종료. 중단된 요청은 다음 시작 때 failed로 이동합니다.")


if __name__ == "__main__":
    main()
