import gzip
import json
import os
import sqlite3
from pathlib import Path

from kuavo_dataset_editor.core import new_project, label
from kuavo_dataset_editor.store import Store
from kuavo_dataset_editor.storage import open_events, dataset_event_files, new_event_chunk
from kuavo_dataset_editor.storage_maintenance import compact_db, compress_events, deduplicate_videos


def test_revision_source_dedup_and_legacy_compaction_preserve_every_revision(tmp_path):
    store = Store(tmp_path)
    p = new_project('storage')
    p['sources'] = [{'source_id': 'a', 'kind': 'catalog', 'path': '/fixture', 'fps': 30,
                     'images': {'head': ['same-frame.jpg'] * 2000},
                     'episodes': [{'episode_id': 0, 'num_frames': 100}]}]
    p = label(p, 'a', 0, 0, 50, 'pick')
    first = store.save(p, -1)
    second = store.save({**first, 'name': 'second'}, 0)
    assert store.get(p['project_id'], 0) == first
    assert store.get(p['project_id'], 1) == second
    assert store.db.execute('SELECT COUNT(*) FROM revision_sources').fetchone()[0] == 1
    with store.db:
        store.db.execute('UPDATE revisions SET payload=? WHERE revision=0', (json.dumps(first),))
    store.db.close()
    compact_db(tmp_path / 'editor.sqlite')
    reopened = Store(tmp_path)
    assert reopened.get(p['project_id'], 0) == first
    assert reopened.get(p['project_id'], 1) == second
    assert reopened.get(p['project_id']) == second


def test_lossless_compression_keeps_hardlinks_and_old_manifest_paths(tmp_path):
    first, second = tmp_path / 'a/events.jsonl', tmp_path / 'b/events.jsonl'
    first.parent.mkdir(); second.parent.mkdir()
    raw = b'{"topic":"/tf","data":"repeated repeated repeated"}\n' * 100
    first.write_bytes(raw)
    os.link(first, second)
    compress_events([first, second])
    assert not first.exists() and not second.exists()
    with open_events(first) as stream:
        assert stream.read().encode() == raw
    assert Path(str(first) + '.gz').stat().st_ino == Path(str(second) + '.gz').stat().st_ino


def test_chunk_append_never_rewrites_previous_events(tmp_path):
    old = tmp_path / 'events.jsonl'
    old.write_text('old\n')
    chunk, index = new_event_chunk(tmp_path, True)
    with open_events(chunk, 'wt') as stream:
        stream.write('new\n')
    (tmp_path / 'events.index.json').write_text(json.dumps(index))
    paths = list(dataset_event_files(tmp_path))
    old_stamp = paths[0].stat()
    chunk2, index2 = new_event_chunk(tmp_path, True)
    with open_events(chunk2, 'wt') as stream:
        stream.write('third\n')
    assert paths[0].stat() == old_stamp
    assert index2['chunks'][:2] == index['chunks']
    assert [open_events(path).read() for path in paths] == ['old\n', 'new\n']


def test_video_dedup_keeps_content_and_fingerprint_timestamps(tmp_path):
    paths = [tmp_path / name / 'videos/test.mp4' for name in ('a', 'b', 'c')]
    for path in paths:
        path.parent.mkdir(parents=True)
        path.write_bytes(b'same bytes')
    os.utime(paths[0], ns=(1000, 2000)); os.utime(paths[1], ns=(1000, 2000))
    os.utime(paths[2], ns=(1000, 3000))
    deduplicate_videos(paths)
    assert paths[0].stat().st_ino == paths[1].stat().st_ino
    assert paths[2].stat().st_ino != paths[0].stat().st_ino
    assert paths[2].stat().st_mtime_ns == 3000
