"""Local writer exclusion, including conservative recovery of dead legacy owners."""
from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import socket
from uuid import uuid4

from .core import EditError


def _alive(owner):
    # Unknown/foreign owners are not permission to break a lock.
    if owner.get('host', socket.gethostname()) != socket.gethostname():
        return True
    pid = owner.get('pid')
    if not isinstance(pid, int) or pid <= 0:
        return True
    if owner.get('start_ticks') is not None:
        try:
            if _start_ticks(pid) != owner['start_ticks']:
                return False  # This PID now belongs to a different process.
        except OSError:
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _start_ticks(pid):
    # Process name can contain spaces and parentheses; field 22 follows ') '.
    return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]


@contextmanager
def dataset_lock(target, **owner):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    path = target.parent / f'.{target.name}.editor-write.lock'
    # Never unlink the advisory-lock inode: concurrent openers must share it.
    with path.with_suffix(path.suffix + '.guard').open('a+') as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise EditError('같은 dataset의 저장/업로드 작업이 진행 중입니다.') from exc
        if path.exists():
            try:
                previous = json.loads(path.read_text())
                if not isinstance(previous, dict) or _alive(previous):
                    raise ValueError('live or unknown owner')
            except (OSError, ValueError, TypeError) as exc:
                raise EditError(f'진행 중이거나 소유자를 확인할 수 없는 dataset lock입니다: {path}') from exc
            # Preserve stale metadata for diagnosis; no live process is terminated.
            path.rename(path.with_name(path.name + '.stale-' + uuid4().hex))
        with path.open('x') as stream:
            json.dump({**owner, 'pid': os.getpid(), 'host': socket.gethostname(),
                       'start_ticks': _start_ticks(os.getpid()), 'target': str(target)}, stream)
        try:
            yield
        finally:
            path.unlink(missing_ok=True)
