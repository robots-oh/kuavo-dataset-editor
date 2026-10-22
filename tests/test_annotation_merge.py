from copy import deepcopy
import json

import pytest

from kuavo_dataset_editor.core import (
    EditError, compile_selection, label, merge_adjacent_clips,
    new_project, normalize_step_annotations, split_clip,
)
from kuavo_dataset_editor.annotations import edit_annotation, project_annotations, AnnotationLookup, restore_exported_annotations
from kuavo_dataset_editor.store import Store


def legacy_project():
    p = new_project('merge-test')
    p['sources'] = [{'source_id': 'a', 'fps': 30, 'fingerprint': {'value': 'a'},
                     'episodes': [{'episode_id': 0, 'num_frames': 16589}]}]
    p = label(p, 'a', 0, 7998, 7999, 'carry')
    return label(p, 'a', 0, 7999, 8322, 'carry')


def labels(p):
    return {(c['source_id'], c.get('episode_id', 0), f): p['tasks'][c['task_id']]
            for c in p['clips'] for f in range(c['start_frame'], c['end_frame'])}


def test_touching_same_task_is_one_range_and_no_false_warning():
    p = legacy_project(); before = deepcopy(p)
    merged = normalize_step_annotations(p)
    assert p == before and labels(merged) == labels(p)
    assert len(merged['clips']) == 1
    c = merged['clips'][0]
    assert (c['start_frame'], c['end_frame']) == (7998, 8322)
    assert c['id'] == p['clips'][0]['id']
    assert c['merged_clip_ids'] == [p['clips'][1]['id']]
    assert normalize_step_annotations(merged) == merged
    plan = compile_selection(p)  # CLI/legacy snapshots use the same warning rules.
    assert plan['kept_frames'] == 324 and not plan['short_annotations']
    assert len(plan['runs'][0]['clips']) == 1


@pytest.mark.parametrize('change', ['gap', 'episode', 'source', 'task', 'reset', 'split'])
def test_real_boundaries_are_not_merged(change):
    p = legacy_project(); left, right = p['clips']
    if change == 'gap': right['start_frame'] += 1
    if change == 'episode': right['episode_id'] = 1
    if change == 'source': right['source_id'] = 'b'
    if change == 'task': p['tasks']['other'] = 'place'; right['task_id'] = 'other'
    if change == 'reset': right['new_episode'] = True
    if change == 'split': right['keep_boundary'] = True
    assert len(merge_adjacent_clips(p['clips'], p['tasks'])) == 2


def test_identical_text_aliases_and_three_way_update_merge():
    p = legacy_project()
    p['tasks']['alias'] = 'carry'; p['clips'][1]['task_id'] = 'alias'
    p = label(p, 'a', 0, 8322, 8400, 'place')
    right_id = p['clips'][-1]['id']
    p = edit_annotation(p, {'operation': 'update_clip', 'clip_id': right_id,
                           'start_frame': 8322, 'end_frame': 8400, 'task': 'carry'})
    assert len(p['clips']) == 1
    assert (p['clips'][0]['start_frame'], p['clips'][0]['end_frame']) == (7998, 8400)
    assert right_id in p['clips'][0]['merged_clip_ids']


def test_new_labels_merge_but_manual_split_and_goal_attempts_survive(tmp_path):
    p = legacy_project()
    p = edit_annotation(p, {'operation': 'label', 'source_id': 'a', 'episode_id': 0,
                           'start_frame': 8322, 'end_frame': 8400, 'task': 'carry'})
    assert len(p['clips']) == 1
    p = split_clip(p, p['clips'][0]['id'], 8322)
    store = Store(tmp_path)
    p = store.save(p, -1)
    assert len(p['clips']) == 2 and p['clips'][1]['keep_boundary']
    for a, b in [(7998, 8322), (8322, 8400)]:
        p = edit_annotation(p, {'operation': 'label', 'track': 'goal', 'source_id': 'a',
                               'episode_id': 0, 'start_frame': a, 'end_frame': b, 'task': 'Move box.'})
    assert len(p['goals']) == 2
    assert compile_selection(project_annotations(p, 'goal'))['num_episodes'] == 2


def test_legacy_load_migration_preserves_history_and_rejects_stale_tabs(tmp_path):
    store = Store(tmp_path); original = legacy_project(); pid = original['project_id']
    raw = json.dumps(original)
    store.db.execute('INSERT INTO projects VALUES (?,?,?)', (pid, 0, raw))
    store.db.execute('INSERT INTO revisions VALUES (?,?,?)', (pid, 0, raw))
    store.db.commit()
    current = store.get(pid)
    assert current['revision'] == 1 and len(current['clips']) == 1
    assert store.get(pid, 0) == original
    assert store.get(pid) == current  # Never migrate on every read.
    assert labels(current) == labels(original)
    with pytest.raises(EditError, match='다른 창'):
        store.save(original, 0)


def test_manual_split_provenance_survives_reimport(tmp_path):
    import pyarrow.parquet as pq
    import pyarrow as pa
    p = normalize_step_annotations(legacy_project())
    p = split_clip(p, p['clips'][0]['id'], 8000)
    lookup = AnnotationLookup(p)
    rows = [dict(episode_index=0, frame_index=i, task='carry', **lookup.at('a', 0, f))
            for i, f in enumerate(range(7998, 8322))]
    (tmp_path/'editor').mkdir()
    pq.write_table(pa.Table.from_pylist(rows), tmp_path/'editor/frame_mapping.parquet')
    descriptor = {'path':str(tmp_path), 'source_id':'reimported',
                  'episodes':[{'episode_id':0,'num_frames':324}]}
    restored = restore_exported_annotations(new_project(), descriptor)
    assert len(restored['clips']) == 2
    assert restored['clips'][1]['keep_boundary']
