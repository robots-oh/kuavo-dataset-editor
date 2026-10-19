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


@pytest.mark.skipif(os.environ.get('EDITOR_TEST_EXPORT') != '1', reason='bounded real episode resume smoke')
def test_interrupted_export_reuses_completed_episode(tmp_path, monkeypatch):
    import json
    from kuavo_dataset_editor.checkpoints import checkpoint_state
    from kuavo_dataset_editor.exporter import export_dataset
    from kuavo_dataset_editor.integrations.lerobot import LeRobotBackend
    from kuavo_dataset_editor.sources import write_json
    descriptor = demo_source(tmp_path / 'source', 'a')
    descriptor['time_mapping'] = 'times.npz'
    times = np.arange(120, dtype=np.int64) * 100_000_000
    np.savez_compressed(tmp_path / 'source/times.npz', bag_time_ns=times, **{'/state': times + 5})
    write_json(tmp_path / 'source/catalog.json', descriptor)
    project = new_project()
    project['sources'] = [descriptor]
    project = label(project, 'a', 0, 0, 4, 'first')
    project = label(project, 'a', 0, 10, 14, 'second')
    target, job = tmp_path / 'output', tmp_path / 'job'
    def interrupt(_, message):
        if 'episode 2/2 export' in message:
            raise RuntimeError('synthetic interruption')
    with pytest.raises(RuntimeError, match='interruption'):
        export_dataset(project, target, job, interrupt)
    checkpoint = checkpoint_state(job)
    assert checkpoint['completed_episodes'] == 1 and checkpoint['completed_frames'] == 4
    staging = tmp_path / '.output.job.partial'
    media = {p.relative_to(staging): (p.read_bytes(), p.stat().st_mtime_ns)
             for root in ('videos', 'data') for p in (staging / root).rglob('*') if p.is_file()}
    assert media
    # Simulate a death half way through a metadata write on the next episode.
    (staging / 'meta/info.json').write_text('{broken')
    (staging / 'videos/partial.tmp').write_bytes(b'uncommitted')
    original = LeRobotBackend.save_episode
    saved = []
    def count_save(writer):
        saved.append(True)
        return original(writer)
    monkeypatch.setattr(LeRobotBackend, 'save_episode', staticmethod(count_save))
    result = export_dataset(project, target, job, lambda *_: None)
    assert len(saved) == 1  # First episode was not re-encoded.
    assert result['episodes'] == 2 and result['frames'] == 8
    assert not (target / 'EDITOR_INCOMPLETE.json').exists()
    for relative, expected in media.items():
        assert ((target / relative).read_bytes(), (target / relative).stat().st_mtime_ns) == expected
    import pyarrow.parquet as pq
    rows = pq.read_table(target / 'editor/frame_mapping.parquet').to_pylist()
    assert len(rows) == 8 and [r['index'] for r in rows] == list(range(8))
    assert rows[4]['source_bag_time_ns'] == int(times[10])
    assert rows[4]['source//state'] == int(times[10] + 5)
    assert len(LeRobotBackend().load_dataset(target)) == 8
    assert len(json.loads((target / 'editor/report.json').read_text())['episode_timings']) == 2
