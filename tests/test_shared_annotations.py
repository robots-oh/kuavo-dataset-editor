from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from kuavo_dataset_editor.app import create_app
from kuavo_dataset_editor.core import EditError, label, new_project
from kuavo_dataset_editor.shared_annotations import source_annotations


def fixture(tmp_path):
    app = create_app(tmp_path / 'workspace', [tmp_path])
    store = app.state.store
    master = new_project('all'); master['annotation_group'] = 'dates'
    master['sources'] = [{'source_id': sid, 'kind': 'catalog', 'path': str(tmp_path / sid),
        'fps': 30, 'fingerprint': {'value': sid},
        'episodes': [{'episode_id': 0, 'num_frames': 120}]} for sid in ['a', 'b']]
    master = label(master, 'a', 0, 0, 30, 'align')
    master = label(master, 'b', 0, 0, 120, 'other recording')
    master = store.save(master, -1)
    day = deepcopy(master); day.update(project_id=uuid4().hex, name='day')
    day['sources'] = day['sources'][:1]
    day['clips'] = [c for c in day['clips'] if c['source_id'] == 'a']
    day = store.save(day, -1)
    test = deepcopy(day); test.update(project_id=uuid4().hex, name='test')
    test.pop('annotation_group'); test = store.save(test, -1)
    return store, TestClient(app), master, day, test


@pytest.mark.parametrize('operation', ['step', 'goal', 'exclude', 'split',
    'remove_annotation', 'remove_clip', 'update_clip', 'source_completion',
    'source_not_required', 'split_assignment'])
def test_bidirectional_sharing_is_source_scoped_and_revisioned(tmp_path, operation):
    store, client, master, day, test = fixture(tmp_path)
    request = {'operation': operation, 'source_id': 'a', 'episode_id': 0,
               'start_frame': 10, 'end_frame': 20, 'expected_revision': day['revision']}
    if operation in ['step', 'goal']:
        request.update(operation='label', track=operation, task='carry', replace=True)
    elif operation in ['remove_clip', 'update_clip']:
        request.update(clip_id=day['clips'][0]['id'], task='updated')
    elif operation == 'split': request.update(clip_id=day['clips'][0]['id'], frame=15)
    elif operation == 'source_completion': request['completed'] = True
    elif operation == 'source_not_required': request['not_required'] = True
    elif operation == 'split_assignment': request['split'] = 'train'
    response = client.post(f"/api/projects/{day['project_id']}/edit", json=request)
    assert response.status_code == 200, response.text
    changed = response.json(); linked = store.get(master['project_id'])
    assert linked['revision'] == master['revision'] + 1
    assert source_annotations(changed, 'a') == source_annotations(linked, 'a')
    assert source_annotations(master, 'b') == source_annotations(linked, 'b')
    assert store.get(test['project_id']) == test
    assert store.get(master['project_id'], master['revision']) == master
    assert client.get(f"/api/projects/{master['project_id']}/revision").json() == {'revision': linked['revision']}
    # A change back from the all-project view reaches the date view too.
    response = client.post(f"/api/projects/{master['project_id']}/edit", json={
        'operation': 'source_not_required', 'source_id': 'a', 'not_required': True,
        'expected_revision': linked['revision']})
    assert response.status_code == 200
    assert source_annotations(response.json(), 'a') == source_annotations(store.get(day['project_id']), 'a')


def test_undo_redo_and_stale_tabs_share_the_same_revision_guard(tmp_path):
    store, client, master, day, _ = fixture(tmp_path)
    request = {'operation': 'exclude', 'source_id': 'a', 'episode_id': 0,
               'start_frame': 10, 'end_frame': 20, 'expected_revision': day['revision']}
    excluded = client.post(f"/api/projects/{day['project_id']}/edit", json=request).json()
    url = f"/api/projects/{master['project_id']}"
    assert client.put(url, json={'restore_revision': 0, 'expected_revision': master['revision']}).status_code == 409
    current = store.get(master['project_id'])
    undone = client.put(url, json={'restore_revision': master['revision'],
        'expected_revision': current['revision']}).json()
    assert source_annotations(undone, 'a') == source_annotations(day, 'a')
    assert source_annotations(store.get(day['project_id']), 'a') == source_annotations(day, 'a')
    redone = client.put(f"/api/projects/{day['project_id']}", json={
        'restore_revision': excluded['revision'],
        'expected_revision': store.revision(day['project_id'])}).json()
    assert source_annotations(redone, 'a') == source_annotations(store.get(master['project_id']), 'a')
    assert source_annotations(redone, 'a') == source_annotations(excluded, 'a')


def test_failure_rolls_back_every_linked_revision_and_group_cannot_be_removed(tmp_path):
    store, _, master, day, _ = fixture(tmp_path)
    invalid = deepcopy(master); invalid['sources'][0]['fingerprint'] = {'value': 'wrong'}
    # Simulate inconsistent legacy registration without altering any annotations.
    import json
    with store.db:
        store.db.execute('UPDATE projects SET payload=? WHERE id=?',
                         (json.dumps(invalid), master['project_id']))
    changed = label(day, 'a', 0, 30, 60, 'carry')
    with pytest.raises(EditError, match='다른 recording'):
        store.save(changed, day['revision'])
    assert store.get(day['project_id']) == day
    assert store.get(master['project_id']) == invalid
    assert store.revision(master['project_id']) == master['revision']
    changed = deepcopy(day); changed.pop('annotation_group')
    with pytest.raises(EditError, match='연결'):
        store.save(changed, day['revision'])


def test_shared_task_ids_do_not_overwrite_instructions_on_another_recording(tmp_path):
    store, _, master, day, _ = fixture(tmp_path)
    collision = next(c['task_id'] for c in master['clips'] if c['source_id'] == 'b')
    changed = deepcopy(day); changed['tasks'][collision] = 'new instruction'
    changed['clips'][0]['task_id'] = collision
    changed = store.save(changed, day['revision'])
    linked = store.get(master['project_id'])
    assert source_annotations(changed, 'a') == source_annotations(linked, 'a')
    assert source_annotations(linked, 'b') == source_annotations(master, 'b')
    assert linked['tasks'][collision] == 'other recording'


def test_project_settings_are_local_and_a_shared_label_edit_reopens_both_views(tmp_path):
    store, client, master, day, _ = fixture(tmp_path)
    changed = deepcopy(day); changed['name'] = 'renamed'; changed['export']['scope'] = 'source'
    changed = store.save(changed, day['revision'])
    assert store.get(master['project_id']) == master
    marked = client.post(f"/api/projects/{day['project_id']}/edit", json={
        'operation': 'source_completion', 'source_id': 'a', 'completed': True,
        'expected_revision': changed['revision']}).json()
    assert store.get(master['project_id'])['source_completed']['a'] is True
    result = client.post(f"/api/projects/{day['project_id']}/edit", json={
        'operation': 'label', 'source_id': 'a', 'episode_id': 0, 'start_frame': 30,
        'end_frame': 60, 'task': 'carry', 'expected_revision': marked['revision']}).json()
    assert result['source_completed']['a'] is False
    assert store.get(master['project_id'])['source_completed']['a'] is False
