from copy import deepcopy

import pytest

from kuavo_dataset_editor.core import EditError, compile_selection, label
from kuavo_dataset_editor.repositories import prepare_export, selected_project
from kuavo_dataset_editor.shared_annotations import source_annotations
from kuavo_dataset_editor.store import Store
from test_history import fixture


def remove(client, project, sid='a', value=True):
    response = client.post(f"/api/projects/{project['project_id']}/edit", json={
        'operation': 'source_removal', 'source_id': sid, 'removed': value,
        'expected_revision': project['revision']})
    assert response.status_code == 200, response.text
    return response.json()


def test_removal_keeps_exact_sources_labels_files_and_running_jobs(tmp_path):
    app, client, original = fixture(tmp_path)
    cache = tmp_path / 'source'; cache.mkdir()
    media = cache / 'preview.mp4'; media.write_bytes(b'preview fixture')
    bag = tmp_path / 'source.bag'; bag.write_bytes(b'original fixture')
    app.state.store.put_job({'id': 'in-flight', 'status': 'running', 'project': original})
    removed = remove(client, original)
    assert removed['source_removed'] == {'a': True}
    assert client.get(f"/api/projects/{original['project_id']}/plan").json()['kept_frames'] == 0
    for key in ('sources', 'clips', 'goals', 'tasks', 'exclusions'):
        assert removed[key] == original[key]
    assert media.read_bytes() == b'preview fixture'
    assert bag.read_bytes() == b'original fixture'
    assert app.state.store.job('in-flight') == {'id': 'in-flight', 'status': 'running', 'project': original}
    assert app.state.store.get(original['project_id'], original['revision']) == original
    # Restart persistence, with no reimport or media generation.
    reopened = Store(app.state.store.root).get(original['project_id'])
    assert reopened['source_removed'] == {'a': True}
    restored = remove(client, reopened, value=False)
    for key in ('sources', 'clips', 'goals', 'tasks', 'exclusions'):
        assert restored[key] == original[key]
    assert client.get('/api/config').json()['source_removal']


@pytest.mark.parametrize('mode', ['new', 'append', 'replace'])
@pytest.mark.parametrize('track', ['step', 'goal'])
def test_all_export_modes_and_tracks_omit_only_removed_source(tmp_path, mode, track):
    app, client, p = fixture(tmp_path)
    p['sources'].append({**deepcopy(p['sources'][0]), 'source_id': 'b', 'fingerprint': {'value': 'b'}})
    p = label(p, 'b', 0, 0, 30, 'keep this')
    p['goals'].append({**p['clips'][-1], 'id': 'goal-b'})
    p = app.state.store.save(p, p['revision'])
    removed = remove(client, p)
    before = deepcopy(removed)
    selection = selected_project(removed, tmp_path / 'target', mode, track)
    assert [s['source_id'] for s in selection['sources']] == ['b']
    assert {c['source_id'] for c in selection['clips']} == {'b'}
    assert compile_selection(selection)['kept_frames'] == 30
    assert selection['_export_removed_sources'] == ['a']
    assert removed == before
    only_removed = selected_project(removed, '', mode, track, ['a'])
    assert compile_selection(only_removed)['kept_frames'] == 0
    assert len(app.state.store.get(p['project_id'])['sources']) == 2


def test_all_removed_export_explains_restore_before_opening_cache(tmp_path):
    app, client, original = fixture(tmp_path)
    removed = remove(client, original)
    with pytest.raises(EditError, match='제거한 원본'):
        prepare_export(removed, tmp_path / 'output', {'mode': 'new', 'repo_name': 'test'})


def test_undo_redo_preserves_later_imports_and_integer_timestamps(tmp_path):
    app, client, original = fixture(tmp_path)
    removed = remove(client, original)
    removed['sources'].append({**deepcopy(removed['sources'][0]), 'source_id': 'b', 'fingerprint': {'value': 'b'}})
    removed = label(removed, 'b', 0, 0, 20, 'later import')
    current = app.state.store.save(removed, removed['revision'])
    url = f"/api/projects/{original['project_id']}"
    restored = client.put(url, json={'restore_revision': original['revision'], 'expected_revision': current['revision']})
    assert restored.status_code == 200
    restored = restored.json()
    assert restored['source_removed']['a'] is False
    assert restored['sources'] == current['sources']
    assert restored['sources'][0]['origin_bag_time_ns'] == 1792656486431530713
    assert [c for c in restored['clips'] if c['source_id'] == 'b'] == [c for c in current['clips'] if c['source_id'] == 'b']
    redone = client.put(url, json={'restore_revision': current['revision'], 'expected_revision': restored['revision']})
    assert redone.status_code == 200 and redone.json()['source_removed']['a'] is True


def test_shared_project_removal_is_local_and_restore_keeps_new_peer_labels(tmp_path):
    from test_shared_annotations import fixture as shared_fixture
    store, client, master, day, unrelated = shared_fixture(tmp_path)
    removed = remove(client, day)
    assert store.get(master['project_id']) == master
    assert store.get(unrelated['project_id']) == unrelated
    response = client.post(f"/api/projects/{master['project_id']}/edit", json={
        'operation': 'label', 'track': 'step', 'task': 'updated by colleague',
        'source_id': 'a', 'episode_id': 0, 'start_frame': 0, 'end_frame': 30,
        'replace': True, 'expected_revision': master['revision']})
    assert response.status_code == 200
    master_updated = response.json()
    removed = store.get(day['project_id'])
    assert removed['source_removed']['a'] is True
    restored = remove(client, removed, value=False)
    assert source_annotations(restored, 'a') == source_annotations(master_updated, 'a')
    assert store.get(master['project_id']) == master_updated


def test_removal_rejects_bad_values_unknown_sources_and_stale_requests(tmp_path):
    app, client, p = fixture(tmp_path)
    url = f"/api/projects/{p['project_id']}/edit"
    body = {'operation': 'source_removal', 'source_id': 'a', 'expected_revision': p['revision']}
    for value in [1, 'true', None]:
        assert client.post(url, json={**body, 'removed': value}).status_code == 409
    assert client.post(url, json={**body, 'source_id': 'missing', 'removed': True}).status_code == 409
    assert client.post(url, json={**body, 'expected_revision': -1, 'removed': True}).status_code == 409
    assert app.state.store.get(p['project_id']) == p
