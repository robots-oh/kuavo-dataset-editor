"""export trim_idle(2026-10-30, rwh_auto v3): drop long still stretches inside labeled clips.

A frame is still when, compared with the previous frame, no action value changed by more than eps_action and no
observation.state value (except base.rack.* — tag-anchored and noisy) moved by more than eps_state. Inside each clip, a
still stretch longer than min_s keeps only its first keep_s; the rest is dropped and the episode continues
(frame_index stays contiguous, like join_across_gaps). Scripted pipelines (PoC2) wait for planning and checks, and
53 % of rwh-auto-step-v2 frames were fully still — a VLA learns to stall from those.
"""
import numpy as np

DEFAULTS = {'idle_min_s': 1.0, 'idle_keep_s': 0.3, 'idle_eps_action': 1e-3, 'idle_eps_state': 2e-3}


def still_frames(action, state, eps_action=1e-3, eps_state=2e-3, state_names=None):
    """(n, k) arrays → bool[n]; frame 0 is never still. NaN (unrecorded) counts as moving — never drop unknown data."""
    action = np.asarray(action, dtype=np.float64)
    state = np.asarray(state, dtype=np.float64)
    if state_names is not None:
        keep = [i for i, name in enumerate(state_names) if not str(name).startswith('base.rack.')]
        state = state[:, keep]
    n = len(action)
    out = np.zeros(n, dtype=bool)
    if n < 2:
        return out
    da = np.abs(np.diff(action, axis=0))
    ds = np.abs(np.diff(state, axis=0))
    with np.errstate(invalid='ignore'):
        quiet = (np.nan_to_num(da, nan=np.inf).max(axis=1) <= eps_action) & (np.nan_to_num(ds, nan=np.inf).max(axis=1) <= eps_state)
    out[1:] = quiet
    return out


def keep_ranges(still, start, end, fps, min_s, keep_s):
    """Frames [start, end) of one clip → kept [a, b) ranges and dropped frame count. still is indexed by source frame."""
    min_n, keep_n = int(round(min_s * fps)), int(round(keep_s * fps))
    kept, dropped, f = [], 0, start
    a = start
    while f < end:
        if not still[f]:
            f += 1
            continue
        g = f
        while g < end and still[g]:
            g += 1
        if g - f > min_n:                         # long still stretch: keep its first keep_n frames
            cut = f + keep_n
            if cut > a:
                kept.append([a, cut])
            dropped += g - cut
            a = g
        f = g
    if end > a:
        kept.append([a, end])
    return kept, dropped


def trim_run(run, still, fps, min_s, keep_s):
    """Compiled run → (run with segments/clip ranges without long still stretches, dropped frames). Pure apart from numpy input."""
    segments, dropped, trimmed = [], 0, []
    for clip in run['clips']:
        ranges, d = keep_ranges(still, clip['start_frame'], clip['end_frame'], fps, min_s, keep_s)
        dropped += d
        if d:
            trimmed.append({'clip_id': clip['id'], 'dropped_frames': d})
        for a, b in ranges:
            if segments and segments[-1][1] == a:
                segments[-1][1] = b
            else:
                segments.append([a, b])
    out = dict(run, segments=segments)
    if trimmed:
        out['trimmed_idle'] = trimmed
    return out, dropped
