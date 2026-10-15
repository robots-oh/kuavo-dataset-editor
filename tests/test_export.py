"""Small actual writer/loader round trip, explicitly opt-in (about 20 frames)."""
import os
import numpy as np
import pytest
from kuavo_dataset_editor.core import label, new_project
from kuavo_dataset_editor.sources import LeRobotSource, demo_source, fingerprint


@pytest.mark.skipif(os.environ.get("EDITOR_TEST_EXPORT") != "1", reason="small export round trip is opt-in")
def test_official_writer_round_trip(tmp_path):
    from kuavo_dataset_editor.exporter import export_dataset
    import pyarrow.dataset as ds
    descriptor = demo_source(tmp_path / "demo", "a")
    before = fingerprint(tmp_path / "demo")
    p = new_project()
    p["sources"] = [descriptor]
    p = label(p, "a", 0, 2, 8, "approach")
    p = label(p, "a", 0, 8, 12, "grasp")
    p = label(p, "a", 0, 20, 30, "carry")
    result = export_dataset(p, tmp_path / "output", tmp_path / "job", lambda *_: None)
    assert result["frames"] == 20 and result["episodes"] == 2
    assert fingerprint(tmp_path / "demo") == before
    rows = ds.dataset(str(tmp_path / "output/data"), format="parquet").to_table().to_pylist()
    assert rows[0]["task_index"] != rows[6]["task_index"]
    assert rows[10]["frame_index"] == 0 and rows[10]["episode_index"] == 1
    reopened = LeRobotSource(tmp_path / "output")
    assert np.allclose(reopened.row(0, 0)["action"], [2/120+0.1, np.sin(2/20)+0.1])
    assert reopened.row(1, 0)["observation.images.head"].shape == (180, 320, 3)
