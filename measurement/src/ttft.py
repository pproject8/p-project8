"""Plan 6.1.2 TTFT from llama.cpp timing logs through the first sampled token."""
import csv
import hashlib
import json
import math
import os
import random
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import tempfile
import urllib.request

REVISION = "f74e2fa6a"
METHOD = "llama_cpp_first_eval_timing_v1"
ROOT = Path(__file__).resolve().parent
FIELDS = ["request_id", "threads", "prompt_tokens", "repetition", "status",
          "ttft_ms", "first_token_id", "evaluated_tokens", "error",
          "measurement_method", "prefill_ms", "first_token_ms", "prompt_eval_ms",
          "eval_ms", "first_token_wall_ms", "sampled_tokens", "cache", "load_mode", "order"]


def validate_options(req, settings):
    if type(req.get("ttft", True)) is not bool:
        raise ValueError("ttft는 true/false여야 합니다")
    if type(req.get('shuffle',False)) is not bool:
        raise ValueError('shuffle은 true/false여야 합니다')
    if 'seed' in req and type(req['seed']) is not int:
        raise ValueError('seed는 정수여야 합니다')
    for key in ("ttft_startup_timeout_seconds", "ttft_request_timeout_seconds"):
        value = settings.get(key, 300)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key}는 양수여야 합니다")


def prepare_helper(settings, log):
    if sys.platform != "linux":
        raise RuntimeError("TTFT 엔진 도구는 현재 Linux만 지원합니다")
    bench = Path(settings["bench_path"]).resolve()
    version = subprocess.run([str(bench), "--version"], capture_output=True, text=True, timeout=10)
    if version.returncode or REVISION not in version.stdout + version.stderr:
        raise RuntimeError(f"ABI 보호: llama.cpp {REVISION} 빌드만 지원합니다")
    libdir = bench.parent
    library = libdir / "libllama.so"
    if not library.is_file():
        raise RuntimeError("bench_path 옆의 libllama.so가 필요합니다")
    source = ROOT / "ttft_engine.cpp"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    lib_hash = hashlib.sha256(library.read_bytes()).hexdigest()
    build = Path(settings["work_dir"]) / "tools" / f"ttft-{REVISION}" / (source_hash[:12] + "-" + lib_hash[:12])
    build.mkdir(parents=True, exist_ok=True)
    helper = build / "ttft-engine"
    files = {"llama.h": "include/llama.h", "LICENSE": "LICENSE"}
    files.update({name: "ggml/include/" + name for name in
                  ("ggml.h", "ggml-cpu.h", "ggml-backend.h", "ggml-opt.h", "gguf.h", "ggml-alloc.h", "ggml-cpp.h")})
    manifest = {}
    for name, path in files.items():
        target = build / name
        if not target.is_file():
            with urllib.request.urlopen(f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{REVISION}/{path}", timeout=30) as response:
                data = response.read()
            temporary = build / (name + f".{os.getpid()}.tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
        manifest[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    if not helper.is_file():
        temporary = build / f"ttft-engine.{os.getpid()}.tmp"
        command = ["g++", "-std=c++17", "-O2", str(source), "-I" + str(build),
                   "-L" + str(libdir), "-Wl,-rpath," + str(libdir), "-lllama", "-lggml-base", "-lggml", "-o", str(temporary)]
        try:
            result = subprocess.run(command, stdout=log, stderr=log, timeout=120)
            if result.returncode:
                raise RuntimeError("TTFT 도구 컴파일 실패: ttft-build.log 확인")
            temporary.replace(helper)
        finally:
            temporary.unlink(missing_ok=True)
    return helper, libdir, dict(llama_revision=REVISION, helper_source_sha256=source_hash,
                               libllama_sha256=lib_hash, headers_sha256=manifest)


def stop_process(proc):
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            proc.kill()
        proc.wait()


def parse_timing(log, prompt):
    records = [line[len("PP8_TTFT "):] for line in log.splitlines() if line.startswith("PP8_TTFT ")]
    if len(records) != 1 or "llama_perf_context_print:" not in log or "llama_perf_sampler_print:" not in log:
        raise ValueError("첫 토큰의 엔진 timing 로그가 없거나 중복되었습니다")
    data = json.loads(records[0])
    if not isinstance(data, dict):
        raise ValueError("잘못된 timing 데이터")
    for key in ("ttft_ms", "prefill_ms", "first_token_ms", "prompt_eval_ms", "eval_ms", "first_token_wall_ms"):
        value = data.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"잘못된 timing 값: {key}")
    if data["ttft_ms"] <= 0 or data["first_token_wall_ms"] <= 0:
        raise ValueError("TTFT는 양수여야 합니다")
    if type(data.get("evaluated_tokens")) is not int or data["evaluated_tokens"] != prompt:
        raise ValueError("실제 입력 토큰 수 불일치")
    if type(data.get("sampled_tokens")) is not int or data["sampled_tokens"] != 1:
        raise ValueError("첫 토큰만 생성해야 합니다")
    if type(data.get("first_token_id")) is not int or data["first_token_id"] < 0:
        raise ValueError("첫 토큰 ID가 없습니다")
    if not math.isclose(data["prefill_ms"], data["prompt_eval_ms"] + data["eval_ms"], abs_tol=1e-6):
        raise ValueError("Prefill timing 합계 불일치")
    if not math.isclose(data["ttft_ms"], data["prefill_ms"] + data["first_token_ms"], abs_tol=1e-6):
        raise ValueError("TTFT timing 합계 불일치")
    if prompt > 1 and data["eval_ms"] != 0:
        raise ValueError("첫 토큰 이후 decode 시간이 섞였습니다")
    return {key: data[key] for key in FIELDS if key in data}


def measure(helper, libdir, model, threads, prompt, timeout, log_path):
    command = [str(helper), str(model), str(libdir), str(threads), str(prompt)]
    with log_path.open("w", encoding="utf-8") as log:
        log.write(json.dumps(command) + "\n"); log.flush()
        proc = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=os.name == "posix")
        try:
            proc.wait(timeout=timeout)
            if proc.returncode:
                raise RuntimeError(f"TTFT engine exited {proc.returncode}")
        finally:
            stop_process(proc)
    return parse_timing(log_path.read_text(encoding="utf-8", errors="replace"), prompt)


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row["threads"], row["prompt_tokens"]), []).append(row)
    result = []
    for (threads, prompt), items in groups.items():
        values = [r["ttft_ms"] for r in items if r["status"] == "ok"]
        mean = statistics.mean(values) if values else None
        sd = statistics.stdev(values) if len(values) > 1 else None
        result.append(dict(threads=threads, prompt_tokens=prompt, ok=len(values), failed=len(items)-len(values),
                           mean_ms=mean, stddev_ms=sd, cv_percent=sd/mean*100 if sd is not None and mean else None))
    return result


def make_schedule(req):
    def values(key):
        value=req[key]
        return list(dict.fromkeys(value if isinstance(value,list) else [value]))
    jobs=[(threads,prompt,repetition)
          for threads in values('threads') for prompt in values('prompt_tokens')
          for repetition in range(1,req['repeat']+1)]
    seed=None
    if req.get('shuffle',False):
        seed=req['seed'] if 'seed' in req else random.randrange(2**32)
        random.Random(seed).shuffle(jobs)
    return seed,jobs


def run_ttft(req, settings, model, out_dir, request_id, on_progress=None):
    validate_options(req,settings)
    out_dir=Path(out_dir)
    seed,jobs=make_schedule(req)
    info=dict(enabled=req.get('ttft',True),measurement_method=METHOD,
              definition="plan 6.1.2: llama.cpp timing-log prompt evaluation plus first-token sampler time; excludes model load, process/context startup, HTTP, tokenization and second-token decode",
              cache_prompt=False,cache='uncontrolled',load_mode='auto',
              prompt="llama-bench-style synthetic rand()%vocab token IDs, optional BOS; srand(1) per fresh process; not identical benchmark token stream",
              repetitions="one discarded fresh-process warmup on first encounter of each threads/prompt condition; repeat measured fresh-model/context evaluations per condition; gen_tokens does not multiply conditions",
              mmap_note="first evaluation may include deferred mmap reads; OS file cache is not cleared or forced warm; not a cold-disk TTFT",
              shuffle=req.get('shuffle',False),seed=seed,
              execution_order=[dict(order=i,threads=t,prompt_tokens=p,repetition=n) for i,(t,p,n) in enumerate(jobs,1)],
              expected_samples=len(jobs),completed_samples=0,conditions=[],status='pending')
    if 'memory_max' in req:
        info.update(enabled=False,reason='memory-limited TTFT requires resource-runner integration; never run an unlimited helper')
    info['status']='running' if info['enabled'] else 'disabled'
    rows=[]
    def publish_progress():
        info['conditions']=summarize(rows)
        ok=sum(row['status']=='ok' for row in rows)
        info.update(ok=ok,failed=len(rows)-ok,completed_samples=len(rows))
        if on_progress is not None:
            on_progress(info.copy())
    publish_progress()
    with (out_dir/'ttft.csv').open('w',newline='',encoding='utf-8') as output:
        writer=csv.DictWriter(output,fieldnames=FIELDS);writer.writeheader()
        if not info['enabled']:
            return info
        error=None
        try:
            with (out_dir/'ttft-build.log').open('w',encoding='utf-8') as log:
                helper,libdir,provenance=prepare_helper(settings,log)
            info.update(provenance)
            publish_progress()
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
            error=f'{type(exc).__name__}: {exc}'
        warmed={}
        timeout=settings.get('ttft_request_timeout_seconds',300)
        for order,(threads,prompt,repetition) in enumerate(jobs,1):
            key=(threads,prompt)
            if error is None and key not in warmed:
                warmed[key]=None
                try:
                    measure(helper,libdir,model,threads,prompt,timeout,
                            out_dir/f'ttft-t{threads}-p{prompt}-warmup.log')
                except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
                    warmed[key]=f'warmup failed: {type(exc).__name__}: {exc}'
            warmup_error=warmed.get(key)
            row=dict(request_id=request_id,threads=threads,prompt_tokens=prompt,repetition=repetition,
                     measurement_method=METHOD,cache='uncontrolled',load_mode='auto',order=order)
            try:
                if error or warmup_error:
                    raise RuntimeError(error or warmup_error)
                row.update(measure(helper,libdir,model,threads,prompt,timeout,
                                   out_dir/f'ttft-t{threads}-p{prompt}-r{repetition}.log'),status='ok')
            except subprocess.TimeoutExpired as exc:
                row.update(status='timeout',error=f'TimeoutExpired: {exc}')
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
                row.update(status='unavailable' if error or warmup_error else 'failed',error=f'{type(exc).__name__}: {exc}')
            rows.append(row);writer.writerow(row);output.flush()
            publish_progress()
    ok=sum(row['status']=='ok' for row in rows)
    info.update(status='ok' if ok==len(rows) else 'partial' if ok else 'unavailable')
    return info
