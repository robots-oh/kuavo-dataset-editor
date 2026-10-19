import base64
import hashlib
import json
from pathlib import Path
import shlex
import sys

import pytest

from kuavo_dataset_editor.core import EditError
from kuavo_dataset_editor.file_browser import browse_local
from kuavo_dataset_editor import ssh_sources as ssh
from kuavo_dataset_editor.jobs import Cancelled, import_many


def test_local_folder_browser(tmp_path):
    root = tmp_path / 'allowed'; root.mkdir()
    (root / 'trial one.bag').write_bytes(b'bag')
    (root / '.hidden.bag').write_bytes(b'hidden')
    (root / 'recording.bag.active').write_bytes(b'active')
    (root / 'video.mp4').write_bytes(b'video')
    dataset = root / 'dataset'; (dataset / 'meta').mkdir(parents=True)
    (dataset / 'meta/info.json').write_text('{}')
    (root / 'outside').symlink_to(tmp_path)
    assert browse_local([root])['roots'] == [str(root)]
    listed = browse_local([root], root)
    assert [e['kind'] for e in listed['entries']] == ['dataset', 'bag']
    assert listed['parent'] is None
    assert browse_local([root], dataset)['dataset']
    assert len(browse_local([root], root, 'trial')['entries']) == 1
    with pytest.raises(EditError): browse_local([root], root / '..')


def test_ssh_profiles_and_command_quoting(tmp_path, monkeypatch):
    config = tmp_path / 'config'
    includes = tmp_path / 'hosts'; includes.mkdir()
    config.write_text('Include hosts/*\nHost robot-up robot-down\n  IdentityFile secret-key\nHost * !blocked\nHost = lab-2\n')
    (includes / 'more').write_text('Host worker\nInclude ../config\n')
    aliases = ssh.host_aliases(config)
    assert aliases == ['lab-2', 'robot-down', 'robot-up', 'worker']
    monkeypatch.setattr(ssh, 'host_aliases', lambda: aliases)
    with pytest.raises(EditError): ssh.ssh_command('-oProxyCommand=bad', {})
    command = ssh.ssh_command('robot-up', {'op':'stat', 'path':"/data/a'; echo BAD;.bag"})
    assert 'BatchMode=yes' in command and 'StrictHostKeyChecking=yes' in command
    arguments = shlex.split(command[-1])
    assert arguments[:2] == ['python3', '-c']
    assert json.loads(base64.b64decode(arguments[-1]))['path'] == "/data/a'; echo BAD;.bag"


@pytest.fixture
def local_transport(monkeypatch):
    operations = []
    def command(host, payload):
        assert host == 'fixture-host'
        operations.append(payload['op'])
        return [sys.executable, '-c', ssh.REMOTE_HELPER, base64.b64encode(json.dumps(payload).encode()).decode()]
    monkeypatch.setattr(ssh, 'ssh_command', command)
    return operations


def test_remote_browse_download_resume_reuse_and_versions(tmp_path, local_transport):
    remote = tmp_path / 'remote'; remote.mkdir()
    source = remote / "trial ' weird ; name.bag"
    data = bytes(range(256))*6000
    source.write_bytes(data)
    (remote / 'in-progress.bag.active').write_bytes(b'not finished')
    original = source.stat()
    listing = ssh.browse_remote('fixture-host', str(remote))
    assert [e['name'] for e in listing['entries']] == [source.name]
    def cancel(fraction, message):
        if fraction > 0.07: raise Cancelled()
    with pytest.raises(Cancelled): ssh.download_bag('fixture-host', str(source), tmp_path / 'cache', cancel)
    partial = next((tmp_path / 'cache').rglob('download.partial'))
    assert 0 < partial.stat().st_size < len(data)
    result = ssh.download_bag('fixture-host', str(source), tmp_path / 'cache')
    assert result['resumed_bytes'] > 0 and not result['reused']
    assert Path(result['path']).read_bytes() == data
    assert result['sha256'] == hashlib.sha256(data).hexdigest()
    before_reads = local_transport.count('read')
    repeated = ssh.download_bag('fixture-host', str(source), tmp_path / 'cache')
    assert repeated['reused'] and local_transport.count('read') == before_reads
    assert source.stat().st_size == original.st_size and source.stat().st_mtime_ns == original.st_mtime_ns
    source.write_bytes(data + b'new version')
    changed = ssh.download_bag('fixture-host', str(source), tmp_path / 'cache')
    assert changed['path'] != result['path'] and Path(result['path']).read_bytes() == data
    assert not list((tmp_path / 'cache').rglob('download.lock'))


def test_sequential_batch_keeps_successes_and_failures(tmp_path, monkeypatch):
    calls, checkpoints = [], []
    def import_one(path, directory, sid, profile, progress):
        calls.append(path)
        if path == 'bad.bag': raise ValueError('sample failure')
        return {'source_id':sid, 'path':path}
    monkeypatch.setattr('kuavo_dataset_editor.jobs.import_one', import_one)
    payload = {'items':[{'path':p, 'source_id':p} for p in ['one.bag','bad.bag','two.bag']], 'profile':{}}
    result = import_many(payload, tmp_path, lambda *a:None, lambda value:checkpoints.append(len(value['sources'])))
    assert calls == ['one.bag','bad.bag','two.bag'] and len(result['sources']) == 2
    assert len(result['failures']) == 1 and checkpoints == [1,1,2]


def test_ssh_download_to_real_import_and_attach(tmp_path, monkeypatch, local_transport):
    from kuavo_dataset_editor.app import create_app
    from kuavo_dataset_editor.jobs import worker
    from kuavo_dataset_editor.sources import CatalogSource
    from test_bag import make_fixture_bag
    remote = tmp_path / 'remote'; remote.mkdir()
    bag, profile = make_fixture_bag(remote)
    original = bag.stat()
    workspace = tmp_path / 'workspace'
    app = create_app(workspace)
    endpoints = {route.name:route.endpoint for route in app.routes if hasattr(route, 'endpoint')}
    project = endpoints['create']({'name':'SSH fixture'})
    jid = 'a'*32
    job = workspace / 'jobs' / jid; job.mkdir(parents=True)
    payload = {'host':'fixture-host','project_id':project['project_id'],
               'items':[{'path':str(bag),'source_id':'fixture-source'}], 'profile':profile}
    (job / 'request.json').write_text(json.dumps({'kind':'ssh-import','payload':payload}))
    (job / 'status.json').write_text(json.dumps({'id':jid,'kind':'ssh-import','status':'queued'}))
    worker(job)
    status = json.loads((job / 'status.json').read_text())
    assert status['status'] == 'completed' and not status['result']['failures']
    source = status['result']['sources'][0]
    assert CatalogSource(source['path']).row(0,3)['observation.images.head'].shape == (48,64,3)
    attached = endpoints['attach'](project['project_id'], jid, {'expected_revision':0})
    assert len(attached['sources']) == 1
    assert endpoints['attach'](project['project_id'], jid, {'expected_revision':1})['revision'] == 1
    assert bag.stat().st_size == original.st_size and bag.stat().st_mtime_ns == original.st_mtime_ns


def test_file_picker_api_batch_and_ssh_parameters(tmp_path, monkeypatch):
    from kuavo_dataset_editor.app import create_app
    root = tmp_path / 'data'; root.mkdir()
    paths = [str(root / 'one.bag'), str(root / 'two.bag')]
    for path in paths: Path(path).write_bytes(b'fixture')
    app = create_app(tmp_path / 'workspace', [root])
    started = []
    app.state.jobs.start = lambda kind, payload: started.append((kind,payload)) or {'id':'fake'}
    monkeypatch.setattr(ssh, 'host_aliases', lambda:['fixture-host'])
    endpoints = {route.name:route.endpoint for route in app.routes if hasattr(route, 'endpoint')}
    project = endpoints['create']({'name':'temporary'})
    pid = project['project_id']
    assert len(endpoints['browse_files'](path=str(root))['entries']) == 2
    with pytest.raises(EditError): endpoints['browse_files'](path=str(tmp_path))
    endpoints['start_import'](pid, {'paths':paths})
    assert started[-1][0] == 'import-batch'
    assert endpoints['ssh_hosts']() == {'hosts':['fixture-host']}
    body = {'host':'fixture-host', 'paths':['/data/one.bag'], 'password':'DO_NOT_STORE', 'key':'DO_NOT_STORE'}
    endpoints['start_ssh_import'](pid, body)
    assert started[-1][0] == 'ssh-import'
    assert 'password' not in started[-1][1] and 'key' not in started[-1][1]
    jid = 'b'*32
    job = app.state.jobs.root / jid; job.mkdir()
    original = {'project_id':pid, 'items':[{'path':p, 'source_id':str(i)} for i,p in enumerate(paths)], 'profile':{}}
    request = {'kind':'import-batch','payload':original}
    (job / 'request.json').write_text(json.dumps(request))
    app.state.jobs.get = lambda _: {'kind':'import-batch','status':'cancelled','result':{'sources':[{'source_id':'0'}]}}
    endpoints['retry_import'](jid)
    assert len(started[-1][1]['items']) == 1 and started[-1][1]['items'][0]['path'] == paths[1]
    assert json.loads((job / 'request.json').read_text()) == request
