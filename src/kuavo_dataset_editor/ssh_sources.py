"""Read-only SSH bag browsing and resumable, checksum-verified local downloads.

Uses existing OpenSSH profiles/keys/agent. No password, private key or ssh config
is copied into the editor workspace. The remote helper only lists/stats/reads.
"""
import base64
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

from .core import EditError


REMOTE_HELPER = r'''
import base64, hashlib, json, pathlib, shutil, subprocess, sys, time
a = json.loads(base64.b64decode(sys.argv[1]))
def stamp(p):
    s = p.stat()
    if not p.is_file() or p.suffix.lower() != '.bag':
        raise ValueError('Select a completed .bag file, not .bag.active')
    return {'path':str(p), 'size':s.st_size, 'mtime_ns':s.st_mtime_ns}
def output(value):
    sys.stdout.buffer.write(json.dumps(value).encode()+b'\n')
    sys.stdout.buffer.flush()
try:
    p = pathlib.Path(a.get('path', '~')).expanduser().resolve()
    if a['op'] == 'capabilities':
        version = ''
        if shutil.which('rsync'):
            result = subprocess.run(['rsync','--version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0: version = result.stdout.splitlines()[0]
        output({'rsync_version':version})
    elif a['op'] == 'checksum':
        before = stamp(p)
        if before != a['expected']: raise ValueError('Remote file changed before verification')
        digest = hashlib.sha256(); read = 0; last = 0
        with p.open('rb') as f:
            while True:
                data = f.read(4*1024*1024)
                if not data: break
                digest.update(data); read += len(data)
                if time.monotonic()-last > 0.5:
                    output({'checked_bytes':read}); last = time.monotonic()
        if stamp(p) != before: raise ValueError('Remote file changed during verification')
        output({'sha256':digest.hexdigest(), 'size':read})
    elif a['op'] == 'list':
        entries = []
        for c in p.iterdir():
            if c.name.startswith('.') or a.get('query', '').casefold() not in c.name.casefold():
                continue
            try:
                if c.is_dir():
                    entries.append({'name':c.name, 'path':str(c.resolve()), 'kind':'directory', 'size':None})
                elif c.is_file() and c.suffix.lower() == '.bag':
                    entries.append(dict(stamp(c.resolve()), name=c.name, kind='bag'))
            except (OSError, ValueError):
                continue
        entries.sort(key=lambda e:(e['kind']=='bag',e['name'].casefold()))
        off = int(a.get('offset',0)); limit = 200
        if off < 0: raise ValueError('Invalid offset')
        output({'path':str(p),'parent':str(p.parent) if p!=p.parent else None,
                'entries':entries[off:off+limit],'total':len(entries),'offset':off,
                'next_offset':off+limit if off+limit<len(entries) else None})
    elif a['op'] == 'stat':
        output(stamp(p))
    elif a['op'] == 'read':
        before = stamp(p)
        if before != a['expected']: raise ValueError('Remote file changed; select it again after recording ends')
        offset = int(a['offset'])
        if not 0 <= offset <= before['size']: raise ValueError('Invalid resume offset')
        digest = hashlib.sha256()
        with p.open('rb') as f:
            remaining = offset
            while remaining:
                data = f.read(min(1024*1024,remaining))
                if not data: raise ValueError('Remote file truncated')
                digest.update(data); remaining -= len(data)
            output(dict(before, offset=offset, prefix_sha256=digest.hexdigest()))
            remaining = before['size']-offset
            while remaining:
                data = f.read(min(1024*1024,remaining))
                if not data: raise ValueError('Remote file truncated')
                digest.update(data); sys.stdout.buffer.write(data); remaining -= len(data)
        if stamp(p) != before: raise ValueError('Remote file changed during transfer')
        output({'sha256':digest.hexdigest()})
    else:
        raise ValueError('Unsupported read-only operation')
except Exception as exc:
    print(type(exc).__name__+': '+str(exc), file=sys.stderr)
    sys.exit(2)
'''


def host_aliases(config=None):
    config = Path(config) if config is not None else Path.home() / '.ssh/config'
    base, visited, hosts = config.parent, set(), set()
    def read(path, depth=0):
        path = path.expanduser()
        if path in visited or depth > 8 or len(visited) >= 128:
            return
        visited.add(path)
        try:
            lines = path.read_text().splitlines()
        except (OSError, UnicodeError):
            return
        for line in lines:
            try:
                fields = shlex.split(line, comments=True)
            except ValueError:
                continue
            if not fields:
                continue
            if len(fields) > 1 and fields[1] == '=':
                fields.pop(1)
            if '=' in fields[0]:
                key, value = fields[0].split('=', 1)
                fields = [key, value, *fields[1:]]
            if fields[0].casefold() == 'host':
                hosts.update(h for h in fields[1:] if re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', h))
            elif fields[0].casefold() == 'include':
                for pattern in fields[1:]:
                    pattern = Path(pattern).expanduser()
                    if not pattern.is_absolute():
                        pattern = base / pattern
                    for match in sorted(glob.glob(str(pattern))):
                        read(Path(match), depth+1)
    read(config)
    return sorted(hosts, key=str.casefold)


def validate_host(host):
    if not isinstance(host, str) or host not in host_aliases():
        raise EditError('~/.ssh/config에 등록된 구체적인 Host 별칭을 선택하세요. Host * 패턴은 목록에 표시하지 않습니다.')
    return host


def ssh_options(*, protocol=False):
    if not shutil.which('ssh'):
        raise EditError('편집기 PC에 OpenSSH client(ssh)가 필요합니다.')
    # rsync needs SSH stdin for its bidirectional protocol; helper reads do not.
    return ['ssh', '-Tx' if protocol else '-nTx', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes', '-o', 'RemoteCommand=none']


def ssh_command(host, payload):
    validate_host(host)
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    # One quoted command; remote filenames are base64 JSON data, never shell code.
    command = shlex.join(['python3', '-c', REMOTE_HELPER, encoded])
    return [*ssh_options(), '--', host, command]


def remote_rpc(host, payload):
    try:
        result = subprocess.run(ssh_command(host, payload), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=25, check=False,
                                start_new_session=True, env={**os.environ, 'SSH_ASKPASS_REQUIRE':'never'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EditError('SSH 연결/응답이 없습니다. 네트워크와 기존 SSH 설정을 확인하세요.') from exc
    if result.returncode:
        raise EditError('SSH: '+result.stderr.decode(errors='replace')[-2000:]
                        +'\n먼저 터미널에서 ssh <별칭>으로 호스트 키와 키/에이전트 로그인을 확인하세요.')
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise EditError('원격 응답을 읽지 못했습니다. Python 3과 로그인 shell의 불필요한 stdout 출력을 확인하세요.') from exc


def browse_remote(host, path='~', query='', offset=0):
    if not isinstance(path, str) or not isinstance(offset, int) or offset < 0:
        raise EditError('잘못된 원격 폴더 또는 목록 범위입니다.')
    return remote_rpc(host, {'op':'list','path':path or '~','query':query,'offset':offset})


class RemoteStream:
    def __init__(self, host, payload, tick):
        self.tick, self.buffer = tick, bytearray()
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen(ssh_command(host, payload), stdout=subprocess.PIPE, stderr=self.errors,
            stdin=subprocess.DEVNULL, start_new_session=True, env={**os.environ, 'SSH_ASKPASS_REQUIRE':'never'})
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.last_data = time.monotonic()

    def fill(self):
        while True:
            self.tick()
            if time.monotonic()-self.last_data > 120:
                raise EditError('SSH 전송 응답이 120초 동안 없습니다. 재시도하면 .partial에서 이어받습니다.')
            if self.selector.select(0.2):
                data = os.read(self.process.stdout.fileno(), 256*1024)
                if not data:
                    self.errors.seek(0)
                    raise EditError('SSH 전송 중단: '+self.errors.read().decode(errors='replace')[-2000:])
                self.buffer.extend(data)
                self.last_data = time.monotonic()
                return

    def line(self):
        while b'\n' not in self.buffer:
            if len(self.buffer) > 1024*1024:
                raise EditError('잘못된 SSH 전송 header입니다.')
            self.fill()
        end = self.buffer.index(b'\n')
        data = bytes(self.buffer[:end])
        del self.buffer[:end+1]
        return json.loads(data)

    def read(self, size):
        if not self.buffer:
            self.fill()
        data = bytes(self.buffer[:size])
        del self.buffer[:len(data)]
        return data

    def close(self):
        self.selector.close()
        self.process.stdout.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.errors.close()


def download_bag(host, path, cache, progress=lambda *args: None, *, transport='auto'):
    expected = remote_rpc(host, {'op':'stat', 'path':path})
    identity = {'host':host, **expected}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
    directory = Path(cache) / key
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / Path(expected['path']).name
    partial, metadata, lock = directory / 'download.partial', directory / 'download.json', directory / 'download.lock'
    try:
        stream = lock.open('x')
    except FileExistsError as exc:
        raise EditError('같은 bag의 다른 다운로드 또는 중단된 lock이 있습니다. 기존 작업/PID를 확인하세요.') from exc
    try:
        with stream:
            json.dump({'pid':os.getpid()}, stream)
        record = json.loads(metadata.read_text()) if metadata.exists() else {}
        if target.is_file() and record.get('identity') == identity and record.get('complete'):
            stat = target.stat()
            if stat.st_size == record['size'] and stat.st_mtime_ns == record['local_mtime_ns']:
                progress(1, '이미 받은 파일 재사용 · '+target.name)
                return {'path':str(target), 'reused':True, 'resumed_bytes':0, 'sha256':record['sha256'],
                        'transport':record.get('transport','stream')}
        if target.exists():
            # Never mutate a file that may already be referenced by a project.
            raise EditError('받아둔 bag이 외부에서 변경됐습니다. 기존 cache를 별도로 보존하고 새 다운로드를 준비하세요.')
        if partial.exists() and partial.stat().st_size > expected['size']:
            partial.rename(directory / ('invalid-'+uuid4().hex+'.partial'))
        offset = partial.stat().st_size if partial.exists() else 0
        from .rsync_transfer import select_transport, receive_rsync
        selected = select_transport(host, transport)
        if selected == 'rsync':
            checksum = receive_rsync(host, expected, partial, progress)
        else:
            progress(0, '기존 SSH 전송 사용 · rsync 미설치/미지원 또는 직접 선택')
            checksum = _receive_stream(host, expected, partial, progress, offset)
        stat = partial.stat()
        value = {'identity':identity, 'complete':True, 'sha256':checksum,
                 'size':stat.st_size, 'local_mtime_ns':stat.st_mtime_ns, 'transport':selected}
        temp = metadata.with_suffix('.tmp')
        temp.write_text(json.dumps(value, indent=2))
        temp.replace(metadata)
        partial.rename(target)
        progress(1, '다운로드 완료 · '+target.name)
        return {'path':str(target), 'reused':False, 'resumed_bytes':offset, 'sha256':checksum, 'transport':selected}
    finally:
        lock.unlink(missing_ok=True)


def _receive_stream(host, expected, partial, progress, offset):
    digest = hashlib.sha256()
    name = Path(expected['path']).name
    if offset:
        with partial.open('rb') as saved:
            while data := saved.read(1024*1024):
                progress(offset/max(1,expected['size']), '재개할 로컬 데이터 확인 · '+name)
                digest.update(data)
    received = offset
    started = time.monotonic()
    def tick():
        speed = (received-offset)/max(0.001,time.monotonic()-started)/1024**2
        progress(received/max(1,expected['size']), f"SSH {received/1024**2:.1f}/{expected['size']/1024**2:.1f} MiB · {speed:.1f} MiB/s · {name}")
    connection = RemoteStream(host, {'op':'read', 'path':expected['path'], 'expected':expected, 'offset':offset}, tick)
    try:
        header = connection.line()
        if header.get('prefix_sha256') != digest.hexdigest() or header.get('offset') != offset:
            if partial.exists():
                partial.rename(partial.parent / ('invalid-'+uuid4().hex+'.partial'))
            raise EditError('이어받을 데이터가 원격 파일과 다릅니다. 손상된 임시 파일은 보존했으며 다시 가져오면 처음부터 받습니다.')
        with partial.open('ab') as output:
            while received < expected['size']:
                data = connection.read(min(256*1024, expected['size']-received))
                output.write(data)
                digest.update(data)
                received += len(data)
                tick()
            output.flush()
            os.fsync(output.fileno())
        trailer = connection.line()
        if trailer.get('sha256') != digest.hexdigest():
            raise EditError('SSH 전송 checksum이 다릅니다. .partial을 보존했습니다.')
    finally:
        connection.close()
    return digest.hexdigest()
