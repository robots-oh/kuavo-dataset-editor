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
