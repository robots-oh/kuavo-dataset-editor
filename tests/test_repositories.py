import json
import os
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from kuavo_dataset_editor.core import EditError, label, new_project, subtract
from kuavo_dataset_editor.repositories import repo_identity


def test_dataset_identity():
    assert repo_identity('rack-box-v1')['repo_id'] == 'local/rack-box-v1'
    assert repo_identity('team/rack-box-v1')['slug'] == 'rack-box-v1'
    for value in ('', '../escape', 'a/b/c', '/tmp/data', 'bad name', 'a.git'):
        with pytest.raises(EditError):
            repo_identity(value)


@pytest.mark.skipif(os.environ.get('EDITOR_TEST_EXPORT') != '1', reason='bounded official writer mutation smoke')
def test_new_append_replace_and_backup(tmp_path, monkeypatch):
    import pyarrow.dataset as ds
    from kuavo_dataset_editor.exporter import export_dataset
    from kuavo_dataset_editor.repositories import prepare_export, with_existing_labels
    from kuavo_dataset_editor.sources import LeRobotSource, demo_source, fingerprint, write_json
    from kuavo_dataset_editor.store import Store

    source = demo_source(tmp_path / 'source', 'a')
    source['events'] = 'events.jsonl'
    events = [{'source_time_s': 0.3, 'topic': '/tag7', 'marker_id': 7},
              {'source_time_s': 2.1, 'topic': '/tag7', 'marker_id': 7}]
    (tmp_path / 'source/events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    write_json(tmp_path / 'source/catalog.json', source)
    project = new_project('new')
    project['sources'] = [source]
    project = label(project, 'a', 0, 2, 6, 'align')
    project = label(project, 'a', 0, 6, 10, 'pick')
    target = tmp_path / 'dataset'
    run = lambda p, job, options: export_dataset(p, target, tmp_path / job, lambda *_: None, options)
    result = run(project, 'job-new', {'mode': 'new', 'repo_name': 'team/rack-box-v1'})
    assert result['frames'] == 8 and result['repo_id'] == 'team/rack-box-v1'
    assert json.loads((target / 'editor/dataset.json').read_text())['name'] == 'team/rack-box-v1'
    first = LeRobotSource(target).rows(0)
    before = fingerprint(target)
    addition = new_project('append')
    addition['sources'] = [source]
    addition = label(addition, 'a', 0, 20, 24, 'carry')
    imported = LeRobotSource(target).descriptor('target-itself')
    addition['sources'].append(imported)
    addition = with_existing_labels(addition, imported)
    assert prepare_export(addition, target, {'mode': 'append'})['kept_frames'] == 4
    with pytest.raises(EditError, match='확인'):
        run(addition, 'job-unconfirmed', {'mode': 'append'})
    result = run(addition, 'job-append', {'mode': 'append', 'confirm_existing': True})
    assert result['frames'] == 12 and result['episodes'] == 2
    assert result['episodes_added'] == 1 and result['frames_added'] == 4
    assert result['source_names'] == ['source'] and result['project_name'] == 'append'
    assert result['published_at'] > 0
    assert fingerprint(result['backup']) == before
    appended_fingerprint = fingerprint(target)
    with pytest.raises(EditError, match='이미.*Export'):
        run(addition, 'job-duplicate', {'mode': 'append', 'confirm_existing': True})
    assert fingerprint(target) == appended_fingerprint
    extended = deepcopy(addition)
    next(c for c in extended['clips'] if c['source_id'] == 'a')['end_frame'] = 26
    remaining = prepare_export(extended, target, {'mode': 'append'})
    assert remaining['kept_frames'] == 2 and remaining['skipped_duplicate_frames'] == 4
    appended = LeRobotSource(target)
    after = appended.rows(0)
    for a, b in zip(first, after):
        assert a['task_index'] == b['task_index']
        np.testing.assert_allclose(a['action'], b['action'])
    descriptor = appended.descriptor('existing')
    edit = new_project('edit')
    edit['sources'] = [descriptor]
    edit = with_existing_labels(edit, descriptor)
    assert len(edit['clips']) == 3  # All original tasks imported, not just new edits.
    edit = label(edit, 'existing', 0, 0, 2, 'precise align', replace=True)
    edit['clips'] = subtract(edit['clips'], 'existing', 1, 2, 4)
    store = Store(tmp_path / 'workspace')
    edit = store.save(edit, -1)
    labels_before = deepcopy(edit['clips'])
    before = fingerprint(target)
    result = run(edit, 'job-replace', {'mode': 'replace', 'confirm_existing': True})
    assert result['frames'] == 10 and result['episodes'] == 2
    assert fingerprint(result['backup']) == before
    relocation = result['source_relocation']
    store.relocate_sources(relocation['old_path'], relocation['backup'], relocation['fingerprint'])
    restored = store.get(edit['project_id'])
    assert restored['clips'] == labels_before
    assert restored['sources'][0]['path'] == result['backup']
    assert (target / 'editor/history/job-replace/project.json').is_file()
    rows = ds.dataset(str(target / 'data'), format='parquet').to_table().to_pylist()
    assert len(rows) == 10
    assert rows[0]['task_index'] != rows[2]['task_index']
    retained_events = [json.loads(line) for line in (target / 'editor/events.jsonl').read_text().splitlines()]
    assert len(retained_events) == 2 and {e['episode_index'] for e in retained_events} == {0, 1}
    assert all(e['marker_id'] == 7 for e in retained_events)
    before = fingerprint(target)
    def cancel(fraction, message):
        if fraction > 0:
            raise RuntimeError('synthetic interruption')
    with pytest.raises(RuntimeError, match='interruption'):
        export_dataset(addition, target, tmp_path / 'job-interrupted', cancel,
                       {'mode': 'append', 'confirm_existing': True})
    assert fingerprint(target) == before
    assert not (target.parent / '.dataset.editor-write.lock').exists()
    from kuavo_dataset_editor.repositories import RepositoryTransaction
    with RepositoryTransaction(addition, target, tmp_path / 'job-swap-failure',
                               {'mode': 'append', 'confirm_existing': True}) as transaction:
        original_rename = Path.rename
        def fail_publication(path, destination):
            if path == transaction.staging:
                raise OSError('synthetic publication failure')
            return original_rename(path, destination)
        with monkeypatch.context() as changes:
            changes.setattr(Path, 'rename', fail_publication)
            with pytest.raises(OSError, match='publication failure'):
                transaction.publish({'frames': 10, 'episodes': 2})
        assert fingerprint(target) == before
        assert not transaction.backup.exists()
    if os.environ.get('EDITOR_TEST_API') == '1':
        import time
        import urllib.request
        from urllib.error import HTTPError
        from kuavo_dataset_editor.desktop import LocalBackend
        backend = LocalBackend(tmp_path / 'api-workspace', [tmp_path])
        def request(path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(backend.url + path, data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        try:
            backend.start()
            opened = request('/api/projects', {'name': 'Disposable existing dataset editor'})
            pid = opened['project_id']
            job = request(f'/api/projects/{pid}/imports', {'path': str(target)})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = next(j for j in request('/api/jobs') if j['id'] == job['id'])
                if state['status'] in ('completed', 'failed'):
                    break
                time.sleep(0.1)
            assert state['status'] == 'completed'
            opened = request(f'/api/projects/{pid}/attach/{job["id"]}', {'expected_revision': opened['revision']})
            assert sum(c['end_frame'] - c['start_frame'] for c in opened['clips']) == 10
            plan = request(f'/api/projects/{pid}/export-plan', {'mode': 'new', 'repo_name': 'api-test'})
            assert plan['output_frames'] == 10 and plan['repo_id'] == 'local/api-test'
            with pytest.raises(HTTPError) as error:
                request(f'/api/projects/{pid}/exports', {'mode': 'replace', 'target': str(target)})
            assert error.value.code == 409
        finally:
            backend.stop()
