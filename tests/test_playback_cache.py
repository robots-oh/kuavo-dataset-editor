from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from kuavo_dataset_editor.app import create_app
from kuavo_dataset_editor.core import label, new_project
from kuavo_dataset_editor.sources import demo_source
from kuavo_dataset_editor.store import Store


def fixture(tmp_path):
    app = create_app(tmp_path / 'workspace', [tmp_path])
    source = demo_source(tmp_path / 'catalog', 'a')
    project = new_project('playback')
    project['sources'] = [source]
    project = label(project, 'a', 0, 5, 80, 'carry')
    project = app.state.store.save(project, -1)
    return app, TestClient(app), project


def test_camera_playback_reads_one_project_payload_and_never_writes_labels(tmp_path):
    app, client, project = fixture(tmp_path)
    store = app.state.store; statements = []
    store.db.set_trace_callback(statements.append)
    pid = project['project_id']; images = []
    for frame in [10, 11, 12]:
        for camera in project['sources'][0]['cameras']:
            response = client.get(f'/api/projects/{pid}/frame/a/0/{frame}', params={'camera': camera})
            assert response.status_code == 200
            assert Image.open(io.BytesIO(response.content)).size == (320, 180)
            images.append(response.content)
    payload_reads = [s for s in statements if s.startswith('SELECT revision,payload FROM projects')]
    assert len(payload_reads) == 1, 'One cold load must serve all cameras and later frames.'
    assert images[0] != images[3], 'Caching registration metadata must not freeze the video.'
    assert not any(s.startswith(('INSERT', 'UPDATE', 'DELETE')) for s in statements)
    assert store.get(pid) == project
    assert store.db.execute('SELECT count(*) FROM revisions WHERE id=?', (pid,)).fetchone()[0] == 1


def test_concurrent_cameras_share_a_cold_descriptor_load(tmp_path):
    app, _, project = fixture(tmp_path)
    store = app.state.store; statements = []
    store.db.set_trace_callback(statements.append)
    with ThreadPoolExecutor(max_workers=3) as pool:
        descriptors = list(pool.map(lambda _: store.source(project['project_id'], 'a'), range(3)))
    assert all(s == project['sources'][0] for s in descriptors)
    assert len([s for s in statements if s.startswith('SELECT revision,payload FROM projects')]) == 1


def test_external_save_invalidates_cached_registration_and_preserves_labels(tmp_path):
    app, client, project = fixture(tmp_path)
    store = app.state.store; pid = project['project_id']
    assert store.source(pid, 'a')['path'] == project['sources'][0]['path']
    other = Store(tmp_path / 'workspace')
    changed = deepcopy(project)
    changed['sources'].append({**deepcopy(changed['sources'][0]), 'source_id': 'b'})
    changed['sources'][0]['path'] = str(tmp_path / 'relocated')
    changed = other.save(changed, project['revision'])
    assert store.source(pid, 'a')['path'] == changed['sources'][0]['path']
    assert store.source(pid, 'b')['source_id'] == 'b'
    assert store.get(pid)['clips'] == project['clips']
    # Undo/restore is another revision and must invalidate in the same way.
    restored = client.put(f'/api/projects/{pid}', json={
        'restore_revision': project['revision'], 'expected_revision': changed['revision']})
    assert restored.status_code == 200
    assert store.source(pid, 'a')['path'] == changed['sources'][0]['path']
    assert store.get(pid)['clips'] == project['clips']


def test_cached_sources_still_enforce_membership_camera_and_allowed_paths(tmp_path):
    app, client, project = fixture(tmp_path)
    pid = project['project_id']; camera = project['sources'][0]['cameras'][0]
    url = f'/api/projects/{pid}/frame/a/0/10'
    assert client.get(url, params={'camera': camera}).status_code == 200
    assert client.get(url, params={'camera': 'unknown'}).status_code == 404
    assert client.get(f'/api/projects/{pid}/frame/missing/0/10', params={'camera': camera}).status_code == 404
    empty = app.state.store.save(new_project('another project'), -1)
    assert client.get(f"/api/projects/{empty['project_id']}/frame/a/0/10", params={'camera': camera}).status_code == 404
    app.state.allowed_roots.clear()
    assert client.get(url, params={'camera': camera}).status_code == 409


def test_relocated_and_archived_sources_do_not_leave_stale_cache_entries(tmp_path):
    store = Store(tmp_path / 'workspace')
    project = new_project('relocation')
    project['sources'] = [{'source_id': 'a', 'kind': 'lerobot', 'path': str(tmp_path / 'old'),
        'fps': 30, 'fingerprint': {'value': 'recording'},
        'episodes': [{'episode_id': 0, 'num_frames': 10}]}]
    project = store.save(project, -1); pid = project['project_id']
    assert store.source(pid, 'a')['path'] == str(tmp_path / 'old')
    store.relocate_sources(tmp_path / 'old', tmp_path / 'backup', {'value': 'recording'})
    assert store.source(pid, 'a')['path'] == str(tmp_path / 'backup')
    with store.db:
        store.db.execute('DELETE FROM projects WHERE id=?', (pid,))
    with pytest.raises(KeyError):
        store.source(pid, 'a')
