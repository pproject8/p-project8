"""Small scope holder and exec gate. Invoked only by memory_limit.py."""
import json
import os
from pathlib import Path
import sys
import time
import subprocess


def own_cgroup():
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::/'):
            return (Path('/sys/fs/cgroup') / line[4:]).resolve()
    raise RuntimeError('cgroup v2 is required')


def enter(group, limit, command):
    group = Path(group).resolve()
    if Path('/sys/fs/cgroup') not in group.parents:
        raise ValueError('Invalid cgroup path')
    # Do not execute the benchmark until the actual kernel controls are verified.
    # Let the user manager move the process; scope control files are not
    # necessarily writable by the login session itself.
    moved = subprocess.run(['busctl', '--user', 'call', 'org.freedesktop.systemd1',
                            '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
                            'AttachProcessesToUnit', 'ssau', group.name, '', '1', str(os.getpid())],
                           capture_output=True, text=True, timeout=10)
    if moved.returncode:
        raise RuntimeError('systemd process attachment failed: ' + moved.stderr.strip())
    if own_cgroup() != group:
        raise RuntimeError('benchmark did not enter the requested cgroup')
    if int((group/'memory.max').read_text()) != limit:
        raise RuntimeError('memory.max mismatch')
    if (group/'memory.swap.max').read_text().strip() != '0':
        raise RuntimeError('memory.swap.max must be zero')
    os.execv(command[0], command)


def main():
    if sys.argv[1] == 'hold':
        ready, stop = map(Path, sys.argv[2:4])
        staging = ready.with_suffix('.tmp')
        staging.write_text(json.dumps({'cgroup_path': str(own_cgroup())}))
        staging.replace(ready)
        # Also exit if the controller removed its temporary directory after an error.
        while ready.parent.exists() and not stop.exists():
            time.sleep(.05)
    elif sys.argv[1] == 'enter':
        enter(sys.argv[2], int(sys.argv[3]), sys.argv[4:])
    else:
        raise ValueError('Unknown gate mode')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'MEMORY_SETUP_ERROR: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        sys.exit(125)
