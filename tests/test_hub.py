"""No account, network or real data: Hub SDK calls are replaced by a fake."""
import json
from types import SimpleNamespace

import pytest
import httpx
from huggingface_hub import CommitOperationAdd, CommitOperationDelete
from huggingface_hub.errors import RepositoryNotFoundError

from kuavo_dataset_editor.core import EditError
from kuavo_dataset_editor.hub import local_snapshot, managed_file, prepare_upload, publish_upload


class FakeHub:
    def __init__(self, exists=False, private=True):
        self.exists, self.private, self.sha = exists, private, 'old-head'
        self.created, self.commits = [], []

    def repo_info(self, repo_id, **kwargs):
        assert kwargs['repo_type'] == 'dataset'
        if not self.exists:
            raise RepositoryNotFoundError('not found', response=httpx.Response(404, request=httpx.Request('GET', 'https://huggingface.co/api/datasets/team/test')))
        return SimpleNamespace(sha=self.sha, private=self.private)

    def list_repo_files(self, *args, **kwargs):
        assert kwargs['revision'] == self.sha
        return ['README.md', '.gitattributes', 'data/obsolete.parquet', 'videos/old.mp4', 'notes.txt']

    def create_repo(self, repo_id, **kwargs):
        assert not self.exists and kwargs['exist_ok'] is False
        self.created.append((repo_id, kwargs))
        self.exists, self.private = True, kwargs['private']

    def create_commit(self, repo_id, **kwargs):
        assert kwargs['repo_type'] == 'dataset' and kwargs['parent_commit'] == self.sha
        self.commits.append(kwargs)
        return SimpleNamespace(oid='new-head', commit_url='https://huggingface.co/datasets/team/test/commit/new-head')


@pytest.fixture
def dataset(tmp_path):
    for directory in ('meta', 'data', 'videos', 'editor'):
        (tmp_path / directory).mkdir()
    (tmp_path / 'meta/info.json').write_text(json.dumps({
        'codebase_version': 'v3.0', 'total_frames': 5, 'total_episodes': 1,
        'fps': 30, 'features': {'rgb': {'dtype': 'video'}}}))
    # Only test the publication boundary here. Actual writer/loader roundtrip is
    # covered separately by test_export.py and test_repositories.py.
    (tmp_path / 'data/part.parquet').write_bytes(b'synthetic-table')
    (tmp_path / 'videos/part.mp4').write_bytes(b'synthetic-video')
    (tmp_path / 'editor/project.json').write_text('{"private_path": "/home/example/private"}')
    (tmp_path / '.env').write_text('DO_NOT_PUBLISH=example')
    return tmp_path


def test_private_new_upload_allowlist_and_confirmation(dataset):
    api = FakeHub()
    plan = prepare_upload(dataset, 'team/test', api=api)
    assert plan['private'] and not api.created and not api.commits
    assert 'editor/project.json' in plan['snapshot']['omitted']
    assert '.env' in plan['snapshot']['omitted']
    with pytest.raises(EditError, match='확인'):
        publish_upload(plan, api=api)
    result = publish_upload(plan, confirmed=True, api=api)
    assert api.created[0][1]['private'] is True
    assert result['loader_revision'] == 'new-head'
    adds = api.commits[0]['operations']
    names = [op.path_in_repo for op in adds]
    assert set(names) == {'meta/info.json', 'data/part.parquet', 'videos/part.mp4', 'README.md'}
    assert all(isinstance(op, CommitOperationAdd) for op in adds)
    assert not (dataset.parent / f'.{dataset.name}.editor-write.lock').exists()


def test_existing_sync_and_changes(dataset):
    api = FakeHub(exists=True, private=False)
    plan = prepare_upload(dataset, 'team/test', private=True, api=api)
    assert plan['private'] is False  # Never change existing visibility.
    assert plan['delete'] == ['data/obsolete.parquet', 'videos/old.mp4']
    with pytest.raises(EditError, match='동기화'):
        publish_upload(plan, confirmed=True, api=api)
    api.sha = 'another-writer'
    with pytest.raises(EditError, match='Hub dataset이 변경'):
        publish_upload(plan, confirmed=True, sync_existing=True, api=api)
    assert not api.commits
    api.sha = 'old-head'
    publish_upload(plan, confirmed=True, sync_existing=True, api=api)
    ops = api.commits[-1]['operations']
    assert {o.path_in_repo for o in ops if isinstance(o, CommitOperationDelete)} == set(plan['delete'])
    assert not any(o.path_in_repo == 'README.md' for o in ops)
    (dataset / 'data/part.parquet').write_bytes(b'changed')
    with pytest.raises(EditError, match='로컬 dataset이 변경'):
        publish_upload(plan, confirmed=True, sync_existing=True, api=api)


def test_paths_links_and_lock(dataset):
    for name in ('../secret.json', '/meta/a.json', 'meta/.env.json', '.git/config', 'editor/events.jsonl', 'data/auth.token'):
        assert not managed_file(name)
    with pytest.raises(EditError):
        prepare_upload(dataset, 'just-a-name', api=FakeHub())
    link = dataset / 'data/link.parquet'
    link.symlink_to('/etc/passwd')
    with pytest.raises(EditError, match='심볼릭'):
        local_snapshot(dataset)
    link.unlink()
    plan = prepare_upload(dataset, 'team/test', api=FakeHub())
    lock = dataset.parent / f'.{dataset.name}.editor-write.lock'
    lock.write_text('another writer')
    with pytest.raises(EditError, match='진행 중'):
        publish_upload(plan, confirmed=True, api=FakeHub())
    assert lock.read_text() == 'another writer'


def test_api_only_persists_allowlisted_fields(dataset, tmp_path):
    from fastapi.testclient import TestClient
    from kuavo_dataset_editor.app import create_app
    app = create_app(tmp_path / 'workspace', [dataset.parent])
    calls = []
    app.state.jobs.start = lambda kind, payload: calls.append((kind, payload)) or {'id': 'fake'}
    plan = prepare_upload(dataset, 'team/test', api=FakeHub(exists=True))
    app.state.jobs.get = lambda jid: {'kind': 'hub-plan', 'status': 'completed', 'result': plan}
    with TestClient(app) as client:
        response = client.post('/api/hub/plans', json={'path': str(dataset), 'repo_id': 'team/test', 'token': 'NEVER_SAVE'})
        assert response.status_code == 200
        assert 'token' not in calls[-1][1]
        url = '/api/hub/plans/abc/upload'
        assert client.post(url, json={}).status_code == 409
        assert client.post(url, json={'confirmed': True, 'digest': plan['digest']}).status_code == 409
        assert client.post(url, json={'confirmed': True, 'digest': plan['digest'], 'sync_existing': True}).status_code == 200
        assert calls[-1][0] == 'hub-upload'
