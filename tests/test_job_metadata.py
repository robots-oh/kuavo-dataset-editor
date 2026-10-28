"""No per-frame polling payloads or repeated parsing of unchanged histories."""
import json
from pathlib import Path
from types import SimpleNamespace

from kuavo_dataset_editor.app import create_app
from kuavo_dataset_editor.jobs import JobManager, publish_result, worker
from kuavo_dataset_editor.sources import write_json


def test_worker_keeps_full_result_outside_small_progress_state(tmp_path, monkeypatch):
    from kuavo_dataset_editor import jobs
    descriptor = {'source_id': 'a', 'kind': 'catalog', 'path': '/catalog',
                  'images': {'head': ['long-frame-path.jpg']*100000},
                  'episodes': [{'episode_id': 0, 'num_frames': 100000}]}
    monkeypatch.setattr(jobs, 'demo_source', lambda *_: descriptor)
    write_json(tmp_path/'request.json', {'kind': 'demo', 'payload': {'source_id': 'a'}})
    write_json(tmp_path/'status.json', {'id': 'a'*32, 'kind': 'demo', 'status': 'queued'})
    worker(tmp_path)
    status = json.loads((tmp_path/'status.json').read_text())
    assert status['status'] == 'completed' and status['result_file'] == 'result.json'
    assert (tmp_path/'status.json').stat().st_size < 2048
    assert 'images' not in status['result']
    assert json.loads((tmp_path/'result.json').read_text()) == descriptor


def test_polling_legacy_jobs_parses_each_unchanged_file_once(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    manager = app.state.jobs
    directory = manager.root/('a'*32); directory.mkdir()
    state = {'id': directory.name, 'kind': 'import-batch', 'status': 'completed',
             'result': {'sources': [{'source_id': 'a', 'path': '/cache', 'images': {'head': ['f.jpg']*10000}}],
                        'failures': []}}
    write_json(directory/'status.json', state)
    request = {'kind': 'import-batch', 'payload': {'project_id': 'p', 'items': [{'source_id': 'a'}],
                                                'unused_large_snapshot': ['x']*10000}}
    write_json(directory/'request.json', request)
    endpoint = next(r.endpoint for r in app.routes if r.name == 'list_jobs')
    reads = []
    original = Path.read_text
    def track(path, *args, **kwargs):
        if path in (directory/'status.json', directory/'request.json'):
            reads.append(path.name)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', track)
    first = endpoint(); second = endpoint()
    assert first == second and reads == ['status.json', 'request.json']
    assert 'images' not in first[0]['result']['sources'][0]
    assert first[0]['project_id'] == 'p' and not first[0]['retryable']
    request['payload']['project_id'] = 'updated'
    write_json(directory/'request.json', request)
    assert endpoint()[0]['project_id'] == 'updated'
    assert reads == ['status.json', 'request.json', 'request.json']
    assert manager.get(directory.name)['result']['sources'][0]['images']['head']


def test_running_batch_can_attach_only_published_sources_idempotently(tmp_path):
    from kuavo_dataset_editor.core import label
    from kuavo_dataset_editor.sources import demo_source
    app = create_app(tmp_path/'workspace', [tmp_path])
    routes = {r.name: r.endpoint for r in app.routes if hasattr(r, 'endpoint')}
    p = routes['create']({'name': 'incremental'})
    manager = app.state.jobs
    jid = 'b'*32; directory = manager.root/jid; directory.mkdir()
    write_json(directory/'request.json', {'kind': 'import-batch',
               'payload': {'project_id': p['project_id'], 'items': [{'source_id': 'a'}, {'source_id': 'b'}]}})
    state = {'id': jid, 'kind': 'import-batch', 'status': 'running'}
    first = demo_source(directory/'first', 'a')
    publish_result(directory, state, {'sources': [first], 'failures': []})
    write_json(directory/'status.json', state)
    manager.processes[jid] = SimpleNamespace(poll=lambda: None)
    attached = routes['attach'](p['project_id'], jid, {'expected_revision': 0})
    assert [s['source_id'] for s in attached['sources']] == ['a']
    attached = app.state.store.save(label(attached, 'a', 0, 0, 10, 'pick'), attached['revision'])
    clips = attached['clips']
    second = demo_source(directory/'second', 'b')
    publish_result(directory, state, {'sources': [first, second], 'failures': []})
    write_json(directory/'status.json', state)
    attached = routes['attach'](p['project_id'], jid, {'expected_revision': attached['revision']})
    assert [s['source_id'] for s in attached['sources']] == ['a', 'b']
    assert attached['clips'] == clips
    assert routes['attach'](p['project_id'], jid, {'expected_revision': attached['revision']}) == attached
    assert manager.get(jid)['status'] == 'running'


def test_polling_does_not_overwrite_legacy_result_when_worker_has_died(tmp_path):
    manager = JobManager(tmp_path)
    directory = manager.root/('a'*32); directory.mkdir()
    full = {'source_id': 'a', 'images': {'head': ['f.jpg']*10}}
    write_json(directory/'status.json', {'id': directory.name, 'kind': 'import', 'status': 'running', 'result': full})
    assert manager.all()[0]['status'] == 'failed'
    assert manager.get(directory.name)['result'] == full
