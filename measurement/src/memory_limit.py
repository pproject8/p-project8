"""Per-condition user systemd scope, fail-closed limits and OOM evidence."""
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

GATE = Path(__file__).with_name('memory_gate.py').resolve()


def validate_request(req):
    if 'memory_max' in req:
        if 'load_mode' in req:
            raise ValueError('load_mode 조합은 #42 읽기 방식 기능이 병합된 뒤 연결해야 합니다. 현재는 기본 읽기 방식만 지원합니다')
        values = req['memory_max']
        if not isinstance(values, list) or not values:
            raise ValueError('memory_max는 양수 배수 목록이어야 합니다')
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError('memory_max는 유한한 양수 배수 목록이어야 합니다')
        if req.get('ttft', False):
            raise ValueError('메모리 제한 조건의 TTFT는 아직 지원하지 않습니다. ttft: false를 사용하세요')
    if 'condition_timeout_seconds' in req:
        value = req['condition_timeout_seconds']
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('condition_timeout_seconds는 유한한 양수여야 합니다')


def limit_bytes(model_size, multiplier, page_size=4096):
    # Round up to a kernel page and save both the multiplier and applied bytes.
    try:
        pages = (Decimal(model_size) * Decimal(str(multiplier)) / page_size).to_integral_value(rounding=ROUND_CEILING)
        result = int(pages) * page_size
    except (ValueError, OverflowError, InvalidOperation) as exc:
        raise ValueError('Invalid memory limit') from exc
    if result < page_size or result > 2**63 - 1:
        raise ValueError('메모리 한도가 지원 범위를 벗어났습니다')
    return result


def read_events(group):
    try:
        data = dict(line.split() for line in (Path(group)/'memory.events').read_text().splitlines())
        return {key: int(value) for key, value in data.items()}
    except (OSError, ValueError):
        return None


def classify(returncode, before, after, timed_out=False):
    if timed_out:
        return 'timeout'
    if returncode == 125:
        return 'setup_error'
    # SIGKILL alone is not evidence of OOM. Compare this condition's cgroup counters.
    delta = None
    if before is not None and after is not None and 'oom_kill' in before and 'oom_kill' in after:
        delta = after['oom_kill'] - before['oom_kill']
        if delta < 0:
            delta = None
    if returncode != 0 and delta is not None and delta > 0:
        return 'oom'
    if returncode == 0:
        return 'ok'
    return 'failed_unknown_oom' if delta is None else 'failed'


@contextmanager
def memory_scope(limit, log):
    if sys.platform != 'linux' or not shutil.which('systemd-run'):
        raise RuntimeError('메모리 제한은 cgroup v2와 사용자 systemd가 필요합니다. 관리자 설정을 확인하세요')
    with tempfile.TemporaryDirectory(prefix='pp8-scope-') as tmp:
        root = Path(tmp)
        ready, stop = root/'ready.json', root/'stop'
        unit = 'pp8-memory-' + uuid.uuid4().hex + '.scope'
        command = ['systemd-run', '--user', '--scope', '--quiet', '--unit', unit,
                   '-p', f'MemoryMax={limit}', '-p', 'MemorySwapMax=0', '-p', 'Delegate=yes',
                   sys.executable, str(GATE), 'hold', str(ready), str(stop)]
        log.write(json.dumps(command) + '\n'); log.flush()
        proc = subprocess.Popen(command, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while not ready.exists():
                if proc.poll() is not None:
                    raise RuntimeError('사용자 systemd scope 시작 실패: 관리자 설정 또는 scope 로그를 확인하세요')
                if time.monotonic() >= deadline:
                    raise RuntimeError('사용자 systemd scope 준비 시간 초과: 관리자 설정 필요')
                time.sleep(.02)
            group = Path(json.loads(ready.read_text())['cgroup_path']).resolve()
            if group.name != unit or Path('/sys/fs/cgroup') not in group.parents:
                raise RuntimeError('Unexpected cgroup path')
            if int((group/'memory.max').read_text()) != limit or (group/'memory.swap.max').read_text().strip() != '0':
                raise RuntimeError('메모리/스왑 한도가 실제로 적용되지 않았습니다')
            # A scope holder keeps counters alive until the parent has read them.
            yield {'unit': unit, 'cgroup_path': str(group), 'memory_max_bytes': limit,
                   'memory_swap_max_bytes': 0, 'events_before': read_events(group)}
        finally:
            stop.touch()
            # Kill all remaining tasks in this one randomly-named scope on every exit.
            try:
                subprocess.run(['systemctl', '--user', 'stop', unit], stdout=log,
                               stderr=log, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def run_limited(cmd, monitor_csv, context, timeout, multiplier, model_size, out_dir, runner):
    import os
    limit = limit_bytes(model_size, multiplier, os.sysconf('SC_PAGE_SIZE') if sys.platform == 'linux' else 4096)
    evidence = {'memory_max': multiplier, 'requested_memory_max_bytes': str(Decimal(model_size) * Decimal(str(multiplier))),
                'memory_max_bytes': limit, 'status': 'setup_error'}
    log_path = Path(out_dir)/f"memory-scope-{context['order']}.log"
    with log_path.open('w', encoding='utf-8') as log:
        try:
            with memory_scope(limit, log) as scope:
                evidence.update(scope)
                # Direct child execs in place: monitor the benchmark PID, not systemd-run.
                wrapped = [sys.executable, str(GATE), 'enter', scope['cgroup_path'], str(limit), *cmd]
                try:
                    result = runner(wrapped, monitor_csv, context, timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    evidence['events_after'] = read_events(scope['cgroup_path'])
                    evidence['status'] = classify(None, scope['events_before'], evidence['events_after'], timed_out=True)
                    exc.memory_limit = evidence
                    raise
                evidence['events_after'] = read_events(scope['cgroup_path'])
                evidence['status'] = classify(result.returncode, scope['events_before'], evidence['events_after'])
                result.memory_limit = evidence
                return result
        except (OSError, ValueError, RuntimeError) as exc:
            evidence['error'] = f'{type(exc).__name__}: {exc}'
            result = subprocess.CompletedProcess(cmd, 125, '', evidence['error'])
            result.memory_limit = evidence
            return result
