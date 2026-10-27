import base64
import json
import re
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kuavo_dataset_editor import remote_access as remote
from kuavo_dataset_editor.core import EditError


@pytest.mark.parametrize('value', [True, 1.5, -1, 65536, None, 'oops'])
def test_bad_port(value):
    with pytest.raises(EditError):
        remote.port_number(value, auto=True)


def test_launcher_local_csrf_and_payload(monkeypatch):
    class Connection:
        def status(self): return {'connected': False}
        def connect(self, *args): self.args = args; return {'connected': True}
        def authenticate(self, host): self.host = host; return self.status()
        def disconnect(self): self.disconnected = True
    c = Connection()
    monkeypatch.setattr(remote, 'host_aliases', lambda: ['robot-up'])
    app = remote.create_launcher(c)
    app.state.authority = 'testserver'
    with TestClient(app) as client:
        html = client.get('/').text
        token = json.loads(re.search(r'const initial=(.*?);const', html)[1])['token']
        assert client.get('/api/info').json()['hosts'] == ['robot-up']
        assert client.get('/', headers={'host': 'attacker.example'}).status_code == 403
        assert client.post('/api/connect', json={}).status_code == 403
        headers = {'X-Launcher-Token': token}
        assert client.post('/api/connect', headers={**headers, 'origin': 'http://evil'}, json={}).status_code == 403
        payload = {'host': 'robot-up', 'remote_port': '8765', 'local_port': '0'}
        assert client.post('/api/connect', headers=headers, json=payload).status_code == 200
        assert c.args == ('robot-up', '8765', '0', None)
        client.post('/api/authenticate', headers=headers, json={'host': 'robot-up'})
        assert c.host == 'robot-up'
        client.post('/api/disconnect', headers=headers)
        assert c.disconnected


def test_owned_tunnel_reuse_and_disconnect(monkeypatch):
    c = remote.RemoteConnection()
    calls = []
    monkeypatch.setattr(remote, 'validate_host', lambda h: None)
    monkeypatch.setattr(remote.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(c, 'ready', lambda: bool(c.host))
    monkeypatch.setattr(remote, 'editor_config', lambda port: {'workspace': '/data/work', 'bag_profile': {}})
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
    monkeypatch.setattr(remote.subprocess, 'run', run)
    try:
        result = c.connect('robot-up', 8765, 18765)
        assert result['url'] == 'http://127.0.0.1:18765'
        assert result['workspace'] == '/data/work'
        c.connect('robot-up', 8765, 18765)
        assert sum('forward' in args for args in calls) == 1
        c.disconnect()
        assert any('exit' in args and c.control in args for args in calls)
        assert not c.status()['connected']
        assert not any('tmux' in args for args in calls)
    finally:
        c.close()


def test_failed_remote_probe_cancels_only_forward(monkeypatch):
    c = remote.RemoteConnection()
    c.host = 'robot-up'
    monkeypatch.setattr(c, 'prepare', lambda h: None)
    monkeypatch.setattr(c, 'ensure_authenticated', lambda: None)
    calls = []
    monkeypatch.setattr(remote.subprocess, 'run', lambda args, **kwargs: calls.append(args) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(remote, 'editor_config', lambda port: (_ for _ in ()).throw(EditError('not editor')))
    try:
        with pytest.raises(EditError, match='not editor'):
            c.connect('robot-up', local_port=18765)
        assert c.forward is None
        assert any('cancel' in args for args in calls)
        assert not any('exit' in args for args in calls)
    finally:
        c.host = None
        c.close()


def test_start_helper_reuses_matching_server_and_rejects_other_workspace():
    # Execute the real remote helper against a disposable local HTTP server.
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    import sys
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({'workspace': '/data/editor-workspace', 'bag_profile': {}}).encode())
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        payload = {'port': server.server_port, 'workspace': '/data/editor-workspace'}
        def run(p):
            encoded = base64.b64encode(json.dumps(p).encode()).decode()
            return subprocess.run([sys.executable, '-c', remote.START_HELPER, encoded], capture_output=True, timeout=5)
        result = run(payload)
        assert result.returncode == 0
        assert json.loads(result.stdout)['reused']
        result = run({**payload, 'workspace': '/another-workspace'})
        assert result.returncode == 2
        assert '다른 workspace' in result.stderr.decode()
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_start_helper_launches_only_requested_tmux(monkeypatch, tmp_path):
    import io
    import shlex
    import socket
    import sys
    import urllib.request
    repo = tmp_path / "repo with 'quotes'"
    repo.mkdir(); (repo / 'run.sh').touch()
    roots = tmp_path / 'bag folder'; roots.mkdir()
    workspace = tmp_path / 'workspace'
    config = {'workspace': str(workspace), 'bag_profile': {}}
    calls = []
    class Opener:
        def open(self, *args, **kwargs):
            if not any('new-session' in cmd for cmd in calls): raise ConnectionError()
            return io.BytesIO(json.dumps(config).encode())
    class Probe:
        def settimeout(self, *args): pass
        def connect_ex(self, *args): return 1
        def close(self): pass
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if 'has-session' in args else 0)
    monkeypatch.setattr(socket, 'socket', Probe)
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(remote.subprocess, 'run', run)
    monkeypatch.setattr(remote.shutil, 'which', lambda name: '/usr/bin/' + name)
    payload = {'port': 8765, 'repo': str(repo), 'workspace': str(workspace), 'roots': [str(roots)], 'session': 'editor-test'}
    monkeypatch.setattr(sys, 'argv', ['helper', base64.b64encode(json.dumps(payload).encode()).decode()])
    with pytest.raises(SystemExit) as stopped:
        exec(remote.START_HELPER, {})
    assert stopped.value.code == 0
    launch = next(cmd for cmd in calls if 'new-session' in cmd)
    command = shlex.split(launch[-1])
    assert command == ['bash', str(repo / 'run.sh'), 'serve', '--workspace', str(workspace),
                       '--host', '127.0.0.1', '--port', '8765', '--allow-root', str(roots)]
    assert not any('kill-session' in cmd for cmd in calls)
