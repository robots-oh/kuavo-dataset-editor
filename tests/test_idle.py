"""export trim_idle(idle.py, 2026-10-30): long still stretches inside clips are dropped, the episode continues."""
import types

import numpy as np
import pytest

from kuavo_dataset_editor import exporter
from kuavo_dataset_editor.core import EditError, run_segments
from kuavo_dataset_editor.idle import keep_ranges, still_frames, trim_run


def series(n, moving):
    """n frames; joints move by 0.01 rad on frames in moving, otherwise frozen."""
    a = np.zeros((n, 3))
    for f in range(1, n):
        a[f] = a[f - 1] + (0.01 if f in moving else 0.0)
    return a


def test_still_frames_ignores_rack_pose_and_treats_nan_as_moving():
    n = 10
    action, state = series(n, {3, 4}), series(n, {3, 4})
    state = np.hstack([state, np.random.RandomState(0).normal(0, 0.01, (n, 1))])     # noisy base.rack.x
    still = still_frames(action, state, state_names=['j1', 'j2', 'j3', 'base.rack.x'])
    assert still.tolist() == [False, True, True, False, False, True, True, True, True, True]
    action[7, 0] = np.nan
    assert not still_frames(action, state, state_names=['j1', 'j2', 'j3', 'base.rack.x'])[7]


def test_keep_ranges_keeps_the_start_of_long_still_stretches_only():
    still = np.zeros(200, dtype=bool)
    still[20:50] = True          # 30 frames = 1 s at 30 fps  — not longer than min 1 s, kept
    still[100:190] = True        # 3 s — keep 0.3 s(9 frames), drop 81
    kept, dropped = keep_ranges(still, 0, 200, 30, 1.0, 0.3)
    assert kept == [[0, 109], [190, 200]] and dropped == 81
    assert keep_ranges(still, 120, 200, 30, 1.0, 0.3) == ([[120, 129], [190, 200]], 61)   # clip starts inside a stretch


def test_trim_run_works_per_clip_and_merges_touching_ranges():
    still = np.zeros(300, dtype=bool)
    still[40:140] = True
    run = {'source_id': 's', 'episode_id': 0, 'start_frame': 0, 'end_frame': 300, 'segments': [[0, 300]],
           'clips': [{'id': 'a', 'start_frame': 0, 'end_frame': 100}, {'id': 'b', 'start_frame': 100, 'end_frame': 300}]}
    out, dropped = trim_run(run, still, 30, 1.0, 0.3)
    # clip a: still 40..100 (60 frames > 30) → keep 40..49; clip b: still 100..140 (40 > 30) → keep 100..109
    assert run_segments(out) == [[0, 49], [100, 109], [140, 300]] and dropped == 51 + 31
    assert [t['clip_id'] for t in out['trimmed_idle']] == ['a', 'b']


def test_exporter_trim_idle_updates_plan_and_needs_wholebody_arrays():
    n = 120
    action, state = series(n, set(range(1, 30))), series(n, set(range(1, 30)))          # still after frame 30 (3 s)
    adapter = types.SimpleNamespace(arrays={'action': action, 'observation.state': state}, features={})
    plan = {'runs': [{'source_id': 's', 'episode_id': 0, 'start_frame': 0, 'end_frame': n, 'segments': [[0, n]],
                      'clips': [{'id': 'c', 'start_frame': 0, 'end_frame': n}]}], 'kept_frames': n}
    project = {'export': {'trim_idle': True}}
    exporter.trim_idle(project, plan, {'s': adapter}, {'s': {'fps': 30}})
    assert run_segments(plan['runs'][0]) == [[0, 39]] and plan['kept_frames'] == 39 and plan['trimmed_idle_frames'] == 81
    off = {'runs': [dict(plan['runs'][0], segments=[[0, n]])], 'kept_frames': n}
    exporter.trim_idle({'export': {}}, off, {'s': adapter}, {'s': {'fps': 30}})
    assert off['kept_frames'] == n                                                     # off by default
    with pytest.raises(EditError):
        exporter.trim_idle(project, {'runs': plan['runs'], 'kept_frames': 1}, {'s': types.SimpleNamespace(arrays={}, features={})},
                           {'s': {'fps': 30}})
