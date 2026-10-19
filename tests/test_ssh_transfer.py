"""Small local protocol fixtures: no real SSH hosts or user datasets."""
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys
import threading

import pytest

from kuavo_dataset_editor import rsync_transfer as transfer
from kuavo_dataset_editor import ssh_sources as ssh
from kuavo_dataset_editor.core import EditError
from kuavo_dataset_editor.jobs import Cancelled, import_many


def test_transport_selection_and_safe_command(tmp_path, monkeypatch):
    monkeypatch.setattr(ssh, 'host_aliases', lambda:['fixture-host'])
    monkeypatch.setattr(transfer, '_available', lambda h:False)
    assert transfer.select_transport('fixture-host') == 'stream'
    with pytest.raises(EditError, match='3.1'):
        transfer.select_transport('fixture-host','rsync')
    monkeypatch.setattr(transfer, '_available', lambda h:True)
    assert transfer.select_transport('fixture-host') == 'rsync'
    assert transfer.select_transport('fixture-host','stream') == 'stream'
    with pytest.raises(EditError): transfer.select_transport('fixture-host','bogus')
    command = transfer.rsync_command('fixture-host',{'path':"/data/a [7];'$.bag"}, tmp_path/'download.partial')
    assert '-s' in command and '--append-verify' in command
    assert not any(value in command for value in ('--delete','--remove-source-files'))
    shell = shlex.split(command[command.index('-e')+1])
    assert 'StrictHostKeyChecking=yes' in shell
    assert '-n' not in shell and '-nTx' not in shell  # stdin must carry rsync protocol data
    assert transfer._supported_version('rsync  version 3.1.3  protocol version 31')
    assert not transfer._supported_version('rsync  version 2.6.9  protocol version 29')


@pytest.fixture
def rsync_protocol(tmp_path, monkeypatch):
    if not shutil.which('rsync'): pytest.skip('optional system rsync is not installed')
    driver = tmp_path/'fake_ssh.py'
    driver.write_text('import os,sys\nos.execvp(sys.argv[2],sys.argv[2:])\n')
    original = transfer.rsync_command
    def command(host, expected, partial):
        result = original(host, expected, partial)
        result[result.index('-e')+1] = shlex.join([sys.executable,str(driver)])
        return result
    monkeypatch.setattr(ssh, 'host_aliases', lambda:['fixture-host'])
    monkeypatch.setattr(transfer, 'rsync_command', command)
    monkeypatch.setattr(transfer, 'select_transport', lambda *a:'rsync')
    def helper(host,payload):
        import base64
        return [sys.executable,'-c',ssh.REMOTE_HELPER,base64.b64encode(json.dumps(payload).encode()).decode()]
    monkeypatch.setattr(ssh, 'ssh_command', helper)


@pytest.mark.parametrize('bad_prefix',[False,True])
def test_real_rsync_protocol_resume_hash_reuse_and_special_names(tmp_path, rsync_protocol, bad_prefix):
    source = tmp_path/"a [7] ' ; $ * ?.bag"
    data = bytes(range(256))*5000
    source.write_bytes(data)
    expected = {'path':str(source),'size':len(data),'mtime_ns':source.stat().st_mtime_ns}
    identity = {'host':'fixture-host',**expected}
    key = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:32]
    cache = tmp_path/'ssd'
    partial = cache/key/'download.partial'
    partial.parent.mkdir(parents=True)
    partial.write_bytes((b'x'*200000) if bad_prefix else data[:200000])
    original = (source.stat().st_size,source.stat().st_mtime_ns)
    messages = []
    result = ssh.download_bag('fixture-host',str(source),cache,lambda f,m:messages.append(m))
    assert result['transport'] == 'rsync' and result['resumed_bytes'] == 200000
    assert Path(result['path']).read_bytes() == data
    assert result['sha256'] == hashlib.sha256(data).hexdigest()
    assert any('rsync' in m for m in messages) and any('SHA-256' in m for m in messages)
    assert ssh.download_bag('fixture-host',str(source),cache)['reused']
    assert original == (source.stat().st_size,source.stat().st_mtime_ns)


def test_bad_complete_partial_is_quarantined_before_import(tmp_path, rsync_protocol):
    source = tmp_path/'trial.bag'; source.write_bytes(b'correct-data')
    partial = tmp_path/'download.partial'; partial.write_bytes(b'incorrect!!!')
    expected = {'path':str(source),'size':source.stat().st_size,'mtime_ns':source.stat().st_mtime_ns}
    with pytest.raises(EditError,match='SHA-256'):
        transfer.receive_rsync('fixture-host',expected,partial,lambda *a:None)
    assert not partial.exists() and list(tmp_path.glob('invalid-*.partial'))
    assert source.read_bytes() == b'correct-data'


def test_actual_rsync_cancel_keeps_partial_and_retry_completes(tmp_path, rsync_protocol, monkeypatch):
    source=tmp_path/'cancel.bag'; source.write_bytes(bytes(range(256))*4096)
    original=transfer.rsync_command
    def slow(*args):
        command=original(*args)
        command.insert(1,'--bwlimit=256')
        return command
    monkeypatch.setattr(transfer,'rsync_command',slow)
    def cancel(fraction,message):
        if fraction > 0.01: raise Cancelled()
    cache=tmp_path/'cache'
    with pytest.raises(Cancelled):
        ssh.download_bag('fixture-host',str(source),cache,cancel)
    partial=next(cache.rglob('download.partial'))
    assert 0 < partial.stat().st_size < source.stat().st_size
    assert not list(cache.rglob('download.lock'))
    monkeypatch.setattr(transfer,'rsync_command',original)
    result=ssh.download_bag('fixture-host',str(source),cache)
    assert result['resumed_bytes'] > 0
    assert Path(result['path']).read_bytes() == source.read_bytes()


def test_download_next_file_overlaps_import_without_unbounded_prefetch(tmp_path, monkeypatch):
    second_started = threading.Event()
    second_release = threading.Event()
    downloads, imported, checkpoints = [], [], []
    def download(host,path,cache,progress,**options):
        downloads.append(path)
        if path == 'two.bag':
            second_started.set()
            assert second_release.wait(3)
        progress(1,'finished')
        return {'path':path,'transport':'rsync'}
    def import_one(path,*args):
        if path == 'one.bag':
            assert second_started.wait(3), 'next download must start before current import finishes'
            assert downloads == ['one.bag','two.bag']
            second_release.set()
        imported.append(path)
        return {'source_id':path}
    monkeypatch.setattr(ssh,'download_bag',download)
    monkeypatch.setattr('kuavo_dataset_editor.jobs.import_one',import_one)
    payload={'host':'fixture-host','items':[{'path':p,'source_id':p} for p in ['one.bag','two.bag','three.bag']],
             'profile':{},'download_dir':str(tmp_path/'ssd'),'transport':'auto'}
    result=import_many(payload,tmp_path/'job',lambda *a:None,lambda r:checkpoints.append(len(r['sources'])))
    assert len(result['sources']) == 3 and imported == downloads and checkpoints == [1,2,3]


def test_cancel_stops_prefetch_and_keeps_completed_sources(tmp_path, monkeypatch):
    stopped = threading.Event()
    def download(host,path,cache,progress,**kwargs):
        if path == 'two.bag':
            try:
                while True:
                    progress(0.5,'receiving')
                    stopped.wait(0.02)
            except Cancelled:
                stopped.set()
                raise
        return {'path':path}
    def import_one(path,*args):
        raise Cancelled()
    monkeypatch.setattr(ssh,'download_bag',download)
    monkeypatch.setattr('kuavo_dataset_editor.jobs.import_one',import_one)
    payload={'host':'fixture-host','items':[{'path':p,'source_id':p} for p in ['one.bag','two.bag']],'profile':{}}
    with pytest.raises(Cancelled):
        import_many(payload,tmp_path/'job',lambda *a:None,lambda r:None)
    assert not any(t.name.startswith('bag-transfer') for t in threading.enumerate())


def test_download_destination_is_allowlisted_and_persisted(tmp_path,monkeypatch):
    from kuavo_dataset_editor.app import create_app
    ssd=tmp_path/'ssd'; ssd.mkdir()
    app=create_app(tmp_path/'workspace',[ssd])
    endpoints={r.name:r.endpoint for r in app.routes if hasattr(r,'endpoint')}
    pid=endpoints['create']({'name':'local laptop'})['project_id']
    monkeypatch.setattr(ssh,'host_aliases',lambda:['fixture-host'])
    started=[]
    app.state.jobs.start=lambda kind,payload:started.append(payload) or {'id':'fake'}
    body={'host':'fixture-host','paths':['/bags/one.bag'],'download_dir':str(ssd),'transport':'rsync'}
    endpoints['start_ssh_import'](pid,body)
    assert started[0]['download_dir'] == str(ssd) and started[0]['transport'] == 'rsync'
    assert endpoints['ssh_hosts']()['download_dir'] == str(ssd)
    with pytest.raises(EditError,match='허용'):
        endpoints['start_ssh_import'](pid,{**body,'download_dir':str(tmp_path/'outside')})
    assert len(started) == 1
    endpoints['start_ssh_import'](pid,{**body,'download_dir':''})
    assert started[-1]['download_dir'] == str(tmp_path/'workspace/downloads')


def test_disconnected_download_volume_is_not_created_on_system_disk(tmp_path,monkeypatch):
    started=[]
    monkeypatch.setattr(ssh,'download_bag',lambda *a,**kw:started.append(a))
    payload={'host':'fixture-host','items':[{'path':'/data/a.bag','source_id':'a'}],
             'profile':{},'download_dir':str(tmp_path/'missing-ssd'),'download_device':12345}
    result=import_many(payload,tmp_path/'job',lambda *a:None,lambda r:None)
    assert not started and result['failures']
    assert not (tmp_path/'missing-ssd').exists()
