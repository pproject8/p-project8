"""Isolated model-load call timing, independent process and cache policy per repeat."""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import signal
import subprocess
import sys
import tempfile
import urllib.request

REVISION = 'f74e2fa6a'
METHOD = 'gnu_time_load_process_v1'
TIME = '/usr/bin/time'
MODES = ('auto', 'none', 'mmap', 'mlock', 'mmap+mlock', 'dio')
FIELDS = ['request_id', 'load_mode', 'cache', 'repetition', 'status', 'model_load_s',
          'model_file_gb_per_s', 'returncode', 'error', 'measurement_method', 'model_load_api_s']
ROOT = Path(__file__).resolve().parent


def validate_loading(req):
    if 'load_mode' in req:
        modes = req['load_mode']
        if not isinstance(modes, list) or not modes or any(m not in MODES for m in modes):
            raise ValueError('load_mode는 auto/none/mmap/mlock/mmap+mlock/dio 목록이어야 합니다')
    if 'loading_repeat' in req and (type(req['loading_repeat']) is not int or req['loading_repeat'] < 1):
        raise ValueError('loading_repeat는 양의 정수여야 합니다')
    if req.get('cache', 'cold') not in ('warm', 'cold'):
        raise ValueError('cache는 warm/cold여야 합니다')
    if type(req.get('measure_loading', True)) is not bool:
        raise ValueError('measure_loading은 true/false여야 합니다')


def prepare_helper(settings, log):
    if sys.platform != 'linux':
        raise RuntimeError('model-load helper는 현재 Linux만 지원합니다')
    bench = Path(settings['bench_path']).resolve()
    version = subprocess.run([str(bench), '--version'], capture_output=True, text=True, timeout=10)
    if version.returncode or REVISION not in version.stdout + version.stderr:
        raise RuntimeError(f'ABI 보호: llama.cpp {REVISION} 빌드만 지원합니다')
    libdir = bench.parent
    if not (libdir/'libllama.so').is_file():
        raise RuntimeError('bench_path 옆의 libllama.so가 필요합니다')
    source_hash = hashlib.sha256((ROOT/'model_load.cpp').read_bytes()).hexdigest()
    lib_hash = hashlib.sha256((libdir/'libllama.so').read_bytes()).hexdigest()
    build = Path(settings['work_dir'])/'tools'/f'model-load-{REVISION}'/(source_hash[:12]+'-'+lib_hash[:12])
    build.mkdir(parents=True, exist_ok=True)
    helper = build/'model-load'
    if not helper.is_file():
        # Fetch MIT-licensed headers once, from the exact ABI revision. Never use main.
        files = {'llama.h':'include/llama.h', 'LICENSE':'LICENSE'}
        files.update({name: 'ggml/include/'+name for name in
                      ('ggml.h','ggml-cpu.h','ggml-backend.h','ggml-opt.h','gguf.h','ggml-alloc.h','ggml-cpp.h')})
        manifest = {}
        for name, path in files.items():
            target = build/name
            if not target.is_file():
                url = f'https://raw.githubusercontent.com/ggml-org/llama.cpp/{REVISION}/{path}'
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = response.read()
                target.write_bytes(data)
            manifest[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        (build/'headers-sha256.json').write_text(json.dumps(manifest, indent=2))
        temporary = build/('model-load-'+str(os.getpid())+'.tmp')
        command = ['g++','-std=c++17','-O2',str(ROOT/'model_load.cpp'),'-I'+str(build),
                   '-L'+str(libdir),'-Wl,-rpath,'+str(libdir),'-lllama','-lggml-base','-lggml',
                   '-o',str(temporary)]
        compiled = subprocess.run(command, stdout=log, stderr=log, timeout=120)
        if compiled.returncode:
            temporary.unlink(missing_ok=True)
            raise RuntimeError('model-load 도구 컴파일 실패: model-loading.log를 확인하세요')
        temporary.replace(helper)
    return helper, libdir, {'llama_revision':REVISION, 'helper_source_sha256':source_hash,
                           'libllama_sha256':lib_hash, 'helper':str(helper)}


def clear_cache(settings):
    helper = Path(settings.get('drop_caches_path', '/usr/local/bin/pp8-drop-caches'))
    if not helper.is_absolute() or not helper.is_file():
        raise RuntimeError('cold 측정에는 관리자가 등록한 캐시 비우기 명령이 필요합니다')
    for process in ('llama-bench','llama-server','model-load','ttft-engine'):
        busy=subprocess.run(['pgrep','-x',process],capture_output=True)
        if busy.returncode != 1:
            raise RuntimeError('다른 측정이 실행 중이거나 확인할 수 없어 캐시를 비우지 않습니다')
    result = subprocess.run(['sudo','-n',str(helper)], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('캐시 비우기 실패: ' + result.stderr.strip())


def warm_model(model):
    with Path(model).open('rb') as source:
        while source.read(8*1024*1024):
            pass


def parse_measurement(stdout):
    data=json.loads(stdout)
    if not isinstance(data,dict) or 'model_load_s' not in data:
        raise ValueError('model_load_s is missing')
    value = data['model_load_s']
    if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid model_load_s')
    return value



def parse_elapsed(text):
    prefix = 'PP8_MODEL_LOAD_ELAPSED_S='
    values = [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
    if len(values) != 1:
        raise ValueError('time elapsed record is missing or duplicated')
    seconds = float(values[0])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('time elapsed must be positive and finite')
    return seconds


def stop_timed_process(proc):
    # GNU time and the loading helper share a new process group. Kill both on
    # timeout/interrupt, including a helper left behind if the wrapper exited.
    if os.name == 'posix':
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.poll() is None:
        proc.kill()
    proc.wait()


def run_timed_process(command, log, timeout):
    with tempfile.TemporaryDirectory(prefix='pp8-loading-time-') as directory, \
         tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        elapsed_path = Path(directory)/'elapsed.txt'
        timed = [TIME, '-f', 'PP8_MODEL_LOAD_ELAPSED_S=%e', '-o', str(elapsed_path), '--', *command]
        log.write(json.dumps(timed)+'\n'); log.flush()
        proc = subprocess.Popen(timed, stdout=stdout, stderr=stderr,
                                env={**os.environ, 'LC_ALL':'C'}, start_new_session=os.name=='posix')
        try:
            proc.wait(timeout=timeout)
        finally:
            # On success time has reaped the helper. On interruption terminate
            # the entire group before permitting another cache drop.
            if proc.poll() is None or proc.returncode != 0:
                stop_timed_process(proc)
            stderr.seek(0)
            error_text = stderr.read().decode('utf-8', errors='replace')
            log.write(error_text)
            elapsed = elapsed_path.read_text() if elapsed_path.exists() else ''
            log.write(elapsed); log.flush()
        stdout.seek(0)
        result = subprocess.CompletedProcess(command, proc.returncode,
                                             stdout.read().decode('utf-8',errors='replace'), error_text)
        return result, parse_elapsed(elapsed) if result.returncode == 0 else None


def summarize(rows):
    result = []
    groups = {}
    for row in rows:
        groups.setdefault((row['load_mode'],row['cache']),[]).append(row)
    for (mode,cache), items in groups.items():
        values = [r['model_load_s'] for r in items if r['status']=='ok']
        mean = statistics.mean(values) if values else None
        sd = statistics.stdev(values) if len(values)>1 else None
        result.append(dict(load_mode=mode,cache=cache,ok=len(values),failed=len(items)-len(values),
                           mean_s=mean,stddev_s=sd,cv_percent=sd/mean*100 if sd is not None and mean else None))
    return result


def run_loading(req, settings, model, out_dir, request_id):
    validate_loading(req)
    modes = list(dict.fromkeys(req.get('load_mode',['auto'])))
    repeats = req.get('loading_repeat', req['repeat'])
    cache = req.get('cache','cold')
    info = dict(status='disabled', measurement_method=METHOD, timer=TIME, timer_resolution_s=0.01,
                definition='plan 6.1.2: GNU time elapsed of a fresh load-only process; includes process startup, backend initialization, model load and cleanup; excludes context, tokenization, warmup and inference; API-only time is diagnostic',
                mmap_note='mmap may defer reads until inference; load-only process elapsed is not time to read every weight page',
                cache=cache,repetitions=repeats,conditions=[])
    rows=[]
    with (Path(out_dir)/'model_loading.csv').open('w',newline='',encoding='utf-8') as output:
        writer=csv.DictWriter(output,fieldnames=FIELDS); writer.writeheader()
        if not req.get('measure_loading',True):
            return info
        if 'memory_max' in req:
            info.update(status='disabled',reason='memory-limited model loading requires resource-runner integration; never run an unlimited helper')
            return info
        with (Path(out_dir)/'model-loading.log').open('w',encoding='utf-8') as log:
            error=None
            try:
                helper,libdir,provenance=prepare_helper(settings,log)
                info.update(provenance)
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
                error=f'{type(exc).__name__}: {exc}'
            for mode in modes:
                for repetition in range(1,repeats+1):
                    row=dict(request_id=request_id,load_mode=mode,cache=cache,repetition=repetition,measurement_method=METHOD)
                    try:
                        if error:
                            raise RuntimeError(error)
                        if cache=='cold':
                            clear_cache(settings)
                        else:
                            warm_model(model)
                        command=[str(helper),str(model),str(libdir),mode]
                        result,seconds=run_timed_process(command,log,settings.get('model_load_timeout_seconds',300))
                        if result.returncode:
                            row['returncode']=result.returncode
                            raise RuntimeError(f'model-load exited {result.returncode}')
                        api_seconds=parse_measurement(result.stdout)
                        row.update(status='ok',model_load_s=seconds,model_load_api_s=api_seconds,model_file_gb_per_s=Path(model).stat().st_size/1e9/seconds,returncode=0)
                    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as exc:
                        row.update(status='unavailable' if error else 'failed',error=f'{type(exc).__name__}: {exc}')
                    rows.append(row);writer.writerow(row);output.flush()
    info['conditions']=summarize(rows)
    ok=sum(row['status']=='ok' for row in rows)
    info.update(status='ok' if ok==len(rows) else 'partial' if ok else 'unavailable',ok=ok,failed=len(rows)-ok)
    return info
