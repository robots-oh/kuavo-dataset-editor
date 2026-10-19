"""Optional rsync-over-SSH transport; no shell filenames or remote writes.

Only app-owned download.partial is updated in place. SHA-256 verification is
still performed against the remote file before promoting it to an import source.
"""
from functools import lru_cache
from contextlib import suppress
import hashlib
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from uuid import uuid4

from .core import EditError


def _supported_version(text):
    version = re.search(r'rsync\s+version\s+(\d+)\.(\d+)', text)
    return bool(version and tuple(map(int, version.groups())) >= (3, 1))


@lru_cache(maxsize=16)
def _available(host):
    from .ssh_sources import remote_rpc
    executable = shutil.which('rsync')
    if not executable:
        return False
    try:
        local = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if local.returncode or not _supported_version(local.stdout):
        return False
    remote = remote_rpc(host, {'op':'capabilities'})
    return _supported_version(remote.get('rsync_version', ''))


def select_transport(host, requested='auto'):
    if requested not in ('auto', 'rsync', 'stream'):
        raise EditError('지원하지 않는 SSH 전송 방식입니다.')
    if requested == 'stream':
        return 'stream'
    available = _available(host)
    if requested == 'rsync' and not available:
        raise EditError('rsync 전송은 로컬/원격 양쪽에 rsync 3.1 이상이 필요합니다. 자동 또는 기존 SSH 방식을 선택하세요.')
    return 'rsync' if available else 'stream'


def rsync_command(host, expected, partial):
    from .ssh_sources import validate_host, ssh_options
    validate_host(host)
    # -s sends the remote path through the protocol, not the remote shell.
    # No --delete/--remove-source-files, no recursive directory traversal.
    remote_path = ''.join('\\'+c if c in '\\*?[]' else c for c in expected['path'])
    return [shutil.which('rsync') or 'rsync', '-s', '--partial', '--append-verify',
            '--info=progress2,name0', '--outbuf=L', '--no-compress', '--timeout=120',
            '-e', shlex.join([*ssh_options(protocol=True), '-o', 'Compression=no']),
            '--', host + ':' + remote_path, str(partial)]


def _run_rsync(host, expected, partial, progress):
    size = expected['size']
    offset = partial.stat().st_size if partial.exists() else 0
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(rsync_command(host, expected, partial), stdout=subprocess.PIPE,
            stderr=errors, stdin=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ, 'LC_ALL':'C', 'SSH_ASKPASS_REQUIRE':'never'})
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        started = last_data = time.monotonic()
        try:
            while True:
                received = min(size, partial.stat().st_size if partial.exists() else 0)
                elapsed = max(0.001, time.monotonic()-started)
                speed = max(0, received-offset)/elapsed/1024**2
                progress(0.9*received/max(1,size),
                         f'rsync {received/1024**2:.1f}/{size/1024**2:.1f} MiB · {speed:.1f} MiB/s · {Path(expected["path"]).name}')
                if selector.select(0.2):
                    data = os.read(process.stdout.fileno(), 64*1024)
                    if not data:
                        break
                    last_data = time.monotonic()
                if time.monotonic()-last_data > 130:
                    raise EditError('rsync 응답이 없습니다. 부분 파일을 보존했으며 다시 선택하면 이어받습니다.')
            result = process.wait(timeout=5)
            if result:
                errors.seek(0)
                raise EditError(f'rsync 전송 실패 ({result}): '+errors.read().decode(errors='replace')[-2000:])
        finally:
            selector.close()
            process.stdout.close()
            if process.poll() is None:
                # This session belongs only to this transfer, never to the app/ROS.
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def receive_rsync(host, expected, partial, progress):
    from .ssh_sources import RemoteStream
    partial = Path(partial)
    if not partial.exists() or partial.stat().st_size < expected['size']:
        _run_rsync(host, expected, partial, progress)
    if not partial.is_file() or partial.stat().st_size != expected['size']:
        raise EditError('받은 파일 크기가 다릅니다. 미완료 파일을 보존했습니다.')
    digest = hashlib.sha256()
    checked = 0
    with partial.open('rb') as saved:
        while data := saved.read(4*1024*1024):
            digest.update(data)
            checked += len(data)
            progress(0.9+0.05*checked/max(1,expected['size']), '검증: 로컬 SHA-256 · '+partial.parent.name)
        os.fsync(saved.fileno())
    connection = RemoteStream(host, {'op':'checksum', 'path':expected['path'], 'expected':expected},
                              lambda: progress(0.95, '검증: 원격 SHA-256 · '+Path(expected['path']).name))
    try:
        while True:
            record = connection.line()
            if 'sha256' in record:
                if record['sha256'] != digest.hexdigest() or record.get('size') != expected['size']:
                    partial.rename(partial.with_name('invalid-'+uuid4().hex+'.partial'))
                    raise EditError('SHA-256이 원격 원본과 다릅니다. 손상 파일을 격리했으며 재시도하면 새로 받습니다.')
                return digest.hexdigest()
    finally:
        connection.close()
