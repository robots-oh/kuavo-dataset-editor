import json
from pathlib import Path

import pytest

from kuavo_dataset_editor.checkpoints import EpisodeCheckpoint, checkpoint_state
from kuavo_dataset_editor.core import EditError
from kuavo_dataset_editor.dataset_lock import dataset_lock
from kuavo_dataset_editor.sources import write_json


def test_metadata_rollback_keeps_completed_media_and_quarantines_partial(tmp_path):
    staging, job = tmp_path / 'out.partial', tmp_path / 'job'
    (staging / 'meta').mkdir(parents=True)
    (staging / 'videos').mkdir()
    job.mkdir()
    write_json(staging / 'meta/info.json', {'total_episodes': 1})
    (staging / 'videos/complete.mp4').write_bytes(b'completed-media')
    checkpoint = EpisodeCheckpoint(staging, job, {'selection': 'first'})
    checkpoint.save(1, 4, [{'index': 0}], [{'episode_index': 0}], [])
    write_json(staging / 'meta/info.json', {'total_episodes': 999})
    (staging / 'videos/interrupted.mp4').write_bytes(b'not-complete')
    restored = checkpoint.restore()
    assert restored['completed_episodes'] == 1 and restored['rows'] == [{'index': 0}]
    assert json.loads((staging / 'meta/info.json').read_text())['total_episodes'] == 1
    assert (staging / 'videos/complete.mp4').read_bytes() == b'completed-media'
    assert not (staging / 'videos/interrupted.mp4').exists()
    assert list(job.glob('interrupted-*/videos/interrupted.mp4'))
    with pytest.raises(EditError, match='변경'):
        EpisodeCheckpoint(staging, job, {'selection': 'changed'}).restore()
    (staging / 'videos/complete.mp4').write_bytes(b'corrupted')
    with pytest.raises(EditError, match='변경/누락'):
        checkpoint.restore()


def test_zero_episode_checkpoint_restarts_create_without_existing_root(tmp_path):
    staging, job = tmp_path / 'out.partial', tmp_path / 'job'
    job.mkdir()
    checkpoint = EpisodeCheckpoint(staging, job, {})
    checkpoint.save(0, 0, [], [], [])
    (staging / 'meta').mkdir(parents=True)
    (staging / 'meta/broken').write_text('broken')
    assert checkpoint.restore()['completed_frames'] == 0
    assert not staging.exists()
    assert list(job.glob('interrupted-*/meta/broken'))


def test_lock_excludes_live_owner_and_archives_dead_legacy_owner(tmp_path):
    target = tmp_path / 'dataset'
    lock = tmp_path / '.dataset.editor-write.lock'
    with dataset_lock(target, kind='export'):
        with pytest.raises(EditError, match='진행 중'):
            with dataset_lock(target):
                pytest.fail('duplicate writer')
    assert not lock.exists()
    write_json(lock, {'pid': 2_000_000_000, 'job': 'legacy'})
    with dataset_lock(target):
        assert json.loads(lock.read_text())['pid'] != 2_000_000_000
    assert list(tmp_path.glob('.dataset.editor-write.lock.stale-*'))
    lock.write_text('not-json')
    with pytest.raises(EditError, match='소유자'):
        with dataset_lock(target):
            pytest.fail('unknown lock')
    assert lock.read_text() == 'not-json'

