"""Local-only SSH connection launcher. No Store, bag downloads, or credentials."""
import base64
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler

from .core import EditError
from .ssh_sources import host_aliases, validate_host


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def editor_config(port):
    """Identify an editor, not an arbitrary service using the selected port."""
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(f'http://127.0.0.1:{port}/api/config', timeout=3) as response:
            value = json.loads(response.read(128 * 1024))
        if (not isinstance(value, dict) or not isinstance(value.get('workspace'), str)
                or not value['workspace'].startswith('/') or not isinstance(value.get('bag_profile'), dict)):
            raise ValueError('Not an editor config')
        return value
    except Exception as exc:
        raise EditError('선택한 포트의 편집기에 연결하지 못했습니다. 원격 서버/포트를 확인하거나 서버 시작을 선택하세요.') from exc


def port_number(value, auto=False):
    if isinstance(value, bool) or isinstance(value, float):
        raise EditError('포트 번호를 확인하세요.')
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise EditError('포트는 숫자여야 합니다.') from exc
    if not (1 <= number <= 65535 or (auto and number == 0)):
        raise EditError('포트 범위는 1~65535입니다. 로컬 포트 0은 자동 선택입니다.')
    return number


START_HELPER = r'''
import base64,json,pathlib,shlex,shutil,socket,subprocess,sys,time,urllib.request
a=json.loads(base64.b64decode(sys.argv[1])); port=a['port']
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
def config():
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with op.open('http://127.0.0.1:%d/api/config'%port,timeout=2) as r:
        v=json.loads(r.read(131072))
    if not isinstance(v,dict) or not isinstance(v.get('bag_profile'),dict) or not isinstance(v.get('workspace'),str): raise ValueError('Not an editor')
    return v
try:
    try: live=config()
    except Exception: live=None
    workspace=pathlib.Path(a['workspace']).expanduser().resolve()
    if live:
        if pathlib.Path(live['workspace']).resolve()!=workspace:
            raise ValueError('이 포트는 다른 workspace에서 실행 중입니다. 원격 포트를 바꾸세요. 기존 서버는 변경하지 않았습니다.')
        print(json.dumps({'reused':True,'workspace':live['workspace']}));sys.exit(0)
    probe=socket.socket(); probe.settimeout(1)
    occupied=probe.connect_ex(('127.0.0.1',port))==0;probe.close()
    if occupied: raise ValueError('포트가 다른 서비스에서 사용 중입니다. 종료하지 않고 중단합니다.')
    repo=pathlib.Path(a['repo']).expanduser().resolve()
    if not (repo/'run.sh').is_file(): raise ValueError('원격 저장소의 run.sh를 찾지 못했습니다. 먼저 설치하세요.')
    if not shutil.which('tmux'): raise ValueError('원격 PC에 tmux가 필요합니다. 자동 설치하지 않습니다.')
    if subprocess.run(['tmux','has-session','-t','='+a['session']],capture_output=True).returncode==0:
        raise ValueError('동명의 tmux 세션이 있습니다. 기존 세션은 건드리지 않습니다. 다른 이름을 지정하세요.')
    cmd=['bash',str(repo/'run.sh'),'serve','--workspace',str(workspace),'--host','127.0.0.1','--port',str(port)]
    for root in a['roots']:
        p=pathlib.Path(root).expanduser().resolve()
        if not p.is_dir(): raise ValueError('허용할 원격 폴더가 없습니다: '+str(p))
        cmd+=['--allow-root',str(p)]
    subprocess.run(['tmux','new-session','-d','-s',a['session'],'-c',str(repo),shlex.join(cmd)],check=True)
    for _ in range(60):
        try:
            live=config()
            print(json.dumps({'reused':False,'workspace':live['workspace'],'session':a['session']}));sys.exit(0)
        except Exception: time.sleep(.3)
    raise ValueError('서버가 준비되지 않았습니다. tmux attach -t '+a['session']+' 에서 로그를 확인하세요.')
except Exception as exc:
    print(str(exc),file=sys.stderr);sys.exit(2)
'''


class RemoteConnection:
    """Own one private SSH master; disconnect never stops the remote editor."""
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='kuavo-editor-ssh-')
        self.control = str(Path(self.directory.name) / 'control')
        self.host = None
        self.process = None
        self.errors = tempfile.TemporaryFile()
        self.forward = None
        self.workspace = None
        self.lock = threading.RLock()

    def base(self):
        return ['ssh', '-x', '-S', self.control, '-o', 'ForwardAgent=no', '-o', 'RemoteCommand=none',
                '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2']

    def ready(self):
        if not self.host or not Path(self.control).exists():
            return False
        try:
            return subprocess.run([*self.base(), '-O', 'check', '--', self.host],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def prepare(self, host):
        validate_host(host)
        if not shutil.which('ssh'):
            raise EditError('로컬 PC에 OpenSSH client가 필요합니다.')
        if self.host != host:
            self.disconnect()
            self.host = host

    def authentication_command(self):
        return [*self.base(), '-M', '-N', '-o', 'ClearAllForwardings=yes',
                '-o', 'ControlPersist=no', '-o', 'BatchMode=no',
                '-o', 'StrictHostKeyChecking=ask', '--', self.host]

    def authenticate(self, host):
        with self.lock:
            self.prepare(host)
            if self.ready():
                return self.status()
            self.stop_process()
            command = self.authentication_command()
            terminal = next((args for name, args in (
                ('x-terminal-emulator', ['x-terminal-emulator', '-e']),
                ('gnome-terminal', ['gnome-terminal', '--']),
                ('konsole', ['konsole', '-e']), ('xterm', ['xterm', '-e'])) if shutil.which(name)), None)
            if not terminal or not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
                raise EditError('터미널을 자동으로 열 수 없습니다. 아래 SSH 인증 명령을 로컬 터미널에서 실행한 뒤 연결을 누르세요.')
            subprocess.Popen([*terminal, *command], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return self.status()

    def ensure_authenticated(self):
        if self.ready():
            return
        self.stop_process()
        self.errors.seek(0); self.errors.truncate()
        self.process = subprocess.Popen([*self.base(), '-M', '-N', '-o', 'ClearAllForwardings=yes',
            '-o', 'ControlPersist=no', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '--', self.host],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.errors, start_new_session=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.ready():
                return
            if self.process.poll() is not None:
                break
            time.sleep(.1)
        self.errors.seek(0)
        error = self.errors.read().decode(errors='replace')[-1500:]
        self.stop_process()
        raise EditError('SSH 인증이 필요합니다. 「SSH 인증 터미널」을 열거나 아래 인증 명령을 실행하세요.\n' + error)

    def cancel_forward(self):
        if self.forward:
            subprocess.run([*self.base(), '-O', 'cancel', '-L', self.forward, '--', self.host],
                           capture_output=True, timeout=5)
            self.forward = None

    def connect(self, host, remote_port=8765, local_port=0, start=None):
        with self.lock:
            self.prepare(host)
            remote_port, local_port = port_number(remote_port), port_number(local_port, auto=True)
            self.ensure_authenticated()
            started = None
            if start is not None:
                if not isinstance(start, dict):
                    raise EditError('서버 시작 설정을 확인하세요.')
                session = start.get('session', 'dataset-editor')
                if not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', session):
                    raise EditError('tmux 이름은 영문·숫자·_·- 1~48자로 지정하세요.')
                for key in ('repo', 'workspace'):
                    if not isinstance(start.get(key), str) or not start[key].startswith(('/', '~/')):
                        raise EditError('원격 저장소와 workspace의 절대 경로를 지정하세요.')
                roots = start.get('roots', [])
                if not isinstance(roots, list) or any(not isinstance(p, str) or not p.startswith(('/', '~/')) for p in roots):
                    raise EditError('원격 허용 폴더를 줄마다 절대 경로로 지정하세요.')
                payload = base64.b64encode(json.dumps({**start, 'roots': roots, 'session': session, 'port': remote_port}).encode()).decode()
                result = subprocess.run([*self.base(), '-T', '--', self.host,
                    shlex.join(['python3', '-c', START_HELPER, payload])], capture_output=True, timeout=35)
                if result.returncode:
                    raise EditError(result.stderr.decode(errors='replace')[-2000:])
                try:
                    started = json.loads(result.stdout)
                except (ValueError, UnicodeError) as exc:
                    raise EditError('원격 서버 응답을 읽지 못했습니다. 원격 tmux 로그와 SSH 로그인 출력을 확인하세요.') from exc
            if self.forward:
                current = self.status()
                if current['connected'] and self.forward.endswith(':' + str(remote_port)) and (not local_port or current['port'] == local_port):
                    return {**current, 'server_start': started}
                self.cancel_forward()
            for _ in range(3 if not local_port else 1):
                chosen = local_port
                if not chosen:
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0)); chosen = sock.getsockname()[1]
                forward = f'127.0.0.1:{chosen}:127.0.0.1:{remote_port}'
                result = subprocess.run([*self.base(), '-O', 'forward', '-o', 'ExitOnForwardFailure=yes',
                    '-L', forward, '--', self.host], capture_output=True, timeout=8)
                if result.returncode:
                    if local_port:
                        raise EditError('로컬 포트가 사용 중이거나 전달에 실패했습니다. 로컬 포트를 0으로 바꾸세요.')
                    continue
                self.forward = forward
                try:
                    config = editor_config(chosen)
                except EditError:
                    self.cancel_forward()
                    raise
                self.workspace = config['workspace']
                return {**self.status(), 'server_start': started}
            raise EditError('로컬 포트를 열지 못했습니다. SSH 설정과 사용 중인 포트를 확인하세요.')

    def status(self):
        with self.lock:
            authenticated = self.ready()
            port = int(self.forward.split(':')[1]) if self.forward else None
            return {'host': self.host, 'authenticated': authenticated,
                    'connected': authenticated and bool(self.forward), 'port': port,
                    'url': f'http://127.0.0.1:{port}' if port else None,
                    'workspace': self.workspace,
                    'auth_command': shlex.join(self.authentication_command()) if self.host else ''}

    def stop_process(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=3)
            self.process = None
        if Path(self.control).exists() and not self.ready():
            Path(self.control).unlink(missing_ok=True)

    def disconnect(self):
        with self.lock:
            if self.ready():
                subprocess.run([*self.base(), '-O', 'exit', '--', self.host], capture_output=True, timeout=5)
            self.stop_process()
            self.forward = None
            self.workspace = None

    def close(self):
        self.disconnect()
        self.errors.close()
        self.directory.cleanup()


def create_launcher(connection=None, host='', remote_port=8765, local_port=0):
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    app = FastAPI()
    manager = connection or RemoteConnection()
    app.state.connection = manager
    token = secrets.token_urlsafe(32)
    app.state.authority = None

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        from urllib.parse import urlsplit
        authority = request.headers.get('host', '')
        if app.state.authority and authority != app.state.authority:
            return JSONResponse({'detail': '로컬 주소에서만 연결할 수 있습니다.'}, 403)
        if request.method == 'POST':
            if (not hmac.compare_digest(request.headers.get('x-launcher-token', ''), token)
                    or (request.headers.get('origin') and urlsplit(request.headers['origin']).netloc != authority)):
                return JSONResponse({'detail': '다른 화면에서 연결을 변경할 수 없습니다.'}, 403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(EditError)
    async def error(request, exc):
        return JSONResponse({'detail': str(exc), 'connection': manager.status()}, 409)

    @app.exception_handler(subprocess.TimeoutExpired)
    async def timeout(request, exc):
        return JSONResponse({'detail': 'SSH 응답 시간이 초과됐습니다. 네트워크와 원격 tmux 로그를 확인한 뒤 다시 연결하세요.',
                             'connection': manager.status()}, 409)

    @app.exception_handler(OSError)
    async def unavailable(request, exc):
        return JSONResponse({'detail': 'SSH 또는 로컬 포트를 열지 못했습니다. OpenSSH 설치와 포트 사용 여부를 확인하세요.',
                             'connection': manager.status()}, 409)

    @app.get('/')
    def page():
        source = (Path(__file__).parent / 'static/remote.html').read_text()
        bootstrap = json.dumps({'token': token, 'host': host, 'remote_port': remote_port,
                                'local_port': local_port}).replace('<', '\\u003c')
        return HTMLResponse(source.replace('__BOOTSTRAP__', bootstrap))

    @app.get('/api/info')
    def info():
        return {'hosts': host_aliases(), 'connection': manager.status()}

    @app.post('/api/connect')
    def connect(payload: dict):
        return manager.connect(payload.get('host'), payload.get('remote_port', 8765),
                               payload.get('local_port', 0), payload.get('start'))

    @app.post('/api/authenticate')
    def authenticate(payload: dict):
        return manager.authenticate(payload.get('host'))

    @app.post('/api/disconnect')
    def disconnect():
        manager.disconnect()
        return manager.status()
    return app


def launch(args):
    import uvicorn
    import webbrowser
    manager = RemoteConnection()
    app = create_launcher(manager, args.host or '', args.remote_port, args.local_port)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', args.launcher_port)); listener.listen(128)
        port = listener.getsockname()[1]
        app.state.authority = f'127.0.0.1:{port}'
        url = f'http://127.0.0.1:{port}'
        print(f'Remote editor launcher: {url}', flush=True)
        print('Ctrl+C closes this local tunnel only. Remote editor/jobs keep running.', flush=True)
        if not args.no_browser:
            timer = threading.Timer(.7, lambda: webbrowser.open(url)); timer.daemon = True; timer.start()
        try:
            uvicorn.Server(uvicorn.Config(app, access_log=False)).run(sockets=[listener])
        finally:
            manager.close()
