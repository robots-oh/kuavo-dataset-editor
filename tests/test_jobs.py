import json
from kuavo_dataset_editor.jobs import worker
from kuavo_dataset_editor.sources import write_json


def test_cancel_before_work_keeps_sources_untouched(tmp_path):
    write_json(tmp_path / "request.json", {"kind": "demo", "payload": {"source_id": "cancelled-source"}})
    write_json(tmp_path / "status.json", {"id": "test", "kind": "demo", "status": "queued"})
    (tmp_path / "cancel").touch()
    worker(tmp_path)
    assert json.loads((tmp_path / "status.json").read_text())["status"] == "cancelled"
    assert not (tmp_path / "source").exists()


def test_spawn_is_detached_and_resume_preserves_request(tmp_path, monkeypatch):
    import subprocess
    from kuavo_dataset_editor.jobs import JobManager
    from kuavo_dataset_editor.core import EditError
    import pytest
    calls = []
    class Process:
        pid = 12345
        def poll(self):
            return None
    def spawn(*args, **kwargs):
        calls.append(kwargs)
        return Process()
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    manager = JobManager(tmp_path)
    started = manager.start('export', {'project': {'revision': 7}, 'target': str(tmp_path / 'out')})
    assert calls[0]['start_new_session'] is True and calls[0]['stdin'] == subprocess.DEVNULL
    jid = started['id']
    directory = manager.root / jid
    original = (directory / 'request.json').read_bytes()
    with pytest.raises(EditError, match='중복'):
        manager.resume_export(jid)
    status = json.loads((directory / 'status.json').read_text())
    status['status'] = 'cancelled'
    write_json(directory / 'status.json', status)
    with pytest.raises(EditError, match='checkpoint'):
        manager.resume_export(jid)
    write_json(directory / 'export-checkpoint.json', {'completed_episodes': 2, 'completed_frames': 10})
    (directory / 'cancel').touch()
    resumed = manager.resume_export(jid)
    assert resumed['status'] == 'queued' and resumed['checkpoint']['completed_episodes'] == 2
    assert (directory / 'request.json').read_bytes() == original
    assert not (directory / 'cancel').exists() and len(calls) == 2
