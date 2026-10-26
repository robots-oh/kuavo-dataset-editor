"""Read-only, frame-aligned cues for annotation; never infer missing commands."""
from functools import lru_cache

import numpy as np

from .core import EditError
from .masking import mask_mapping
from .sources import CatalogSource, file_stamp, fingerprint, open_source


TORSO_JOINTS = ('knee_joint', 'leg_joint', 'waist_pitch_joint', 'waist_yaw_joint')


def separate_gripper_feedback(records):
    """Separate a repeated zero-effort angular echo, never discard Reached alone.

    Some recordings interleave percent feedback with Reached/all-zero-effort
    values in [-0.7, 0] every ~1 ms on the very same ROS connection. Require
    sustained close pairs with a different, nonzero percent measurement before
    treating that specific signature as an echo. A lone zero remains valid.
    """
    echoes = np.array([r[2] for r in records], dtype=bool)
    if echoes.sum() < 5 or (~echoes).sum() < 5:
        return records, 0
    measured = [r for r in records if not r[2] and np.isfinite(r[1]).all()]
    if len(measured) < 5:
        return records, 0
    times = np.array([r[0] for r in measured], dtype=np.int64)
    values = np.array([r[1] for r in measured])
    candidates = [r for r in records if r[2]]
    candidate_times = np.array([r[0] for r in candidates], dtype=np.int64)
    from .bag import matched_indices
    indices = matched_indices(times, candidate_times, 'nearest', 5_000_000)
    paired = indices >= 0
    paired &= np.max(np.abs(values[np.maximum(indices, 0)] - np.array([r[1] for r in candidates])), axis=1) > 1
    if paired.sum() < max(5, .8 * len(candidates)):
        return records, 0
    return [r for r in records if not r[2]], int(echoes.sum())


def gripper_window(source, episode_id, frame, bag_path):
    """Re-read a bounded display-only segment; no cache or training data changes."""
    adapter = open_source(source)
    if not isinstance(adapter, CatalogSource) or source.get('robot_type') != 'kuavo_5w':
        raise EditError('그리퍼 실측 조회는 Kuavo bag 원본에서만 지원합니다.')
    manifest = adapter.manifest
    spec = next((s for s in manifest.get('profile', {}).get('signals', [])
                 if s.get('key') == 'observation.gripper'), {})
    if (spec.get('topic') != '/leju_claw_state' or spec.get('field') != 'data.position'
            or spec.get('scale') != .01 or spec.get('size') != 2
            or manifest.get('profile', {}).get('clock') != 'bag_receipt_time'):
        raise EditError('그리퍼 원본 신호 구성을 확인할 수 없습니다.')
    episode = next((e for e in source['episodes'] if e['episode_id'] == episode_id), None)
    if episode is None or episode_id != 0 or not 0 <= frame < episode['num_frames']:
        raise EditError('원본 프레임 범위 밖입니다.')
    fps = float(source['fps'])
    chunk = max(1, round(fps * 5))
    start = frame // chunk * chunk
    stop = min(start + chunk, episode['num_frames'])
    return _gripper_window(str(bag_path), file_stamp(bag_path),
        manifest['fingerprint']['value'], manifest['origin_bag_time_ns'], fps,
        start, stop, float(manifest['profile']['state_tolerance_s']),
        spec.get('method', 'nearest'), tuple(spec.get('order', ('left_claw', 'right_claw'))))


@lru_cache(maxsize=32)
def _gripper_window(path, stamp, expected_fingerprint, origin, fps, start, stop, tolerance, method, order):
    from .bag import signal_value
    from .ros1_reader import Ros1Reader
    from .wholebody import align

    if fingerprint(path)['value'] != expected_fingerprint:
        raise EditError('원본 bag이 import 이후 변경되어 그리퍼 동기화를 확인할 수 없습니다.')
    window = max(1, round(fps * .25))
    first = max(0, start - window)
    targets = origin + (np.arange(first, stop, dtype=np.int64) * 1_000_000_000 // fps).astype(np.int64)
    records = []
    spec = dict(topic='/leju_claw_state', field='data.position', names_field='data.name', order=order, size=2)
    with Ros1Reader(path) as reader:
        connections = [c for c in reader.connections if c.topic == spec['topic']]
        lower = max(reader.start_time, int(targets[0]) - int(tolerance * 1e9))
        upper = min(reader.end_time, int(targets[-1]) + int(tolerance * 1e9) + 1)
        if connections and lower < upper:
            for connection, timestamp, raw in reader.messages(connections=connections, start=lower, stop=upper):
                msg = reader.deserialize(raw, connection.msgtype)
                try:
                    value = signal_value(msg, spec)
                    echo = (list(msg.state) == [2, 2] and len(msg.data.velocity) == 2
                            and len(msg.data.effort) == 2 and np.all(np.asarray(msg.data.velocity) == 0)
                            and np.all(np.asarray(msg.data.effort) == 0)
                            and np.all((value >= -.71) & (value <= 0)))
                except (AttributeError, ValueError, IndexError):
                    value, echo = np.full(2, np.nan), False
                records.append((int(timestamp), value, bool(echo)))
    if file_stamp(path) != stamp:
        raise EditError('그리퍼 조회 중 원본 bag이 변경되었습니다.')
    filtered, removed = separate_gripper_feedback(records)
    values = align([(t, value / 100) for t, value, _ in filtered], targets, 2, method, tolerance)
    values[~np.isfinite(values)] = np.nan
    changes = window_change(values, window)
    series = {}
    for i, side in enumerate(('left', 'right')):
        series[side + '_gripper'] = values[start-first:, i]
        series[side + '_gripper_change'] = changes[start-first:, i]
    return {'start_frame': start, 'separated_messages': removed,
            'series': {key: [round(float(v), 6) if np.isfinite(v) else None for v in values]
                       for key, values in series.items()}}


def torso_window(source, episode_id, frame, bag_path):
    """Read a bounded segment for older arm-only imports, leaving their cache intact."""
    adapter = open_source(source)
    if not isinstance(adapter, CatalogSource) or source.get('robot_type') != 'kuavo_5w':
        raise EditError('Torso 실측 조회는 Kuavo bag 원본에서만 지원합니다.')
    manifest = adapter.manifest
    episode = next((e for e in source['episodes'] if e['episode_id'] == episode_id), None)
    if episode is None or episode_id != 0 or not 0 <= frame < episode['num_frames']:
        raise EditError('원본 프레임 범위 밖입니다.')
    fps = float(source['fps'])
    chunk = max(1, round(fps * 5))
    start = frame // chunk * chunk
    stop = min(start + chunk, episode['num_frames'])
    return _torso_window(str(bag_path), file_stamp(bag_path),
        manifest['fingerprint']['value'], manifest['origin_bag_time_ns'], fps,
        start, stop, float(manifest['profile']['state_tolerance_s']))


@lru_cache(maxsize=32)
def _torso_window(path, stamp, expected_fingerprint, origin, fps, start, stop, tolerance):
    from .bag import signal_value
    from .ros1_reader import Ros1Reader
    from .wholebody import align

    if fingerprint(path)['value'] != expected_fingerprint:
        raise EditError('원본 bag이 import 이후 변경되어 Torso 동기화를 확인할 수 없습니다.')
    window = max(1, round(fps * .25))
    first = max(0, start - window)
    targets = origin + (np.arange(first, stop, dtype=np.int64) * 1_000_000_000 // fps).astype(np.int64)
    entries = []
    spec = dict(topic='/sensors_data_raw', field='joint_data.joint_q', slice=[0,4], size=4)
    with Ros1Reader(path) as reader:
        connections = [c for c in reader.connections if c.topic == spec['topic']]
        lower = max(reader.start_time, int(targets[0]) - int(tolerance * 1e9))
        upper = min(reader.end_time, int(targets[-1]) + int(tolerance * 1e9) + 1)
        if connections and lower < upper:
            for connection, timestamp, raw in reader.messages(connections=connections, start=lower, stop=upper):
                msg = reader.deserialize(raw, connection.msgtype)
                try:
                    value = signal_value(msg, spec)
                except (AttributeError, ValueError, IndexError):
                    value = np.full(4, np.nan)
                entries.append((int(timestamp), value))
    if file_stamp(path) != stamp:
        raise EditError('Torso 조회 중 원본 bag이 변경되었습니다.')
    values = align(entries, targets, 4, 'nearest', tolerance)
    values[~np.isfinite(values)] = np.nan
    motion = np.max(window_excursion(values, window), axis=1) * 180 / np.pi
    return {'start_frame': start,
            'motion': [round(float(v), 6) if np.isfinite(v) else None for v in motion[start-first:]]}


def window_change(values, frames):
    """Past-only displacement; an incomplete or invalid window stays unknown."""
    result = np.full_like(values, np.nan, dtype=np.float64)
    if len(values) <= frames:
        return result
    invalid = np.vstack([np.zeros((1, values.shape[1])),
                         np.cumsum(~np.isfinite(values), axis=0)])
    valid = invalid[frames + 1:] == invalid[:-frames - 1]
    result[frames:] = np.where(valid, values[frames:] - values[:-frames], np.nan)
    return result


def window_excursion(values, frames):
    """Include an out-and-back motion, even when the endpoints are identical."""
    result = np.full_like(values, np.nan, dtype=np.float64)
    if len(values) > frames:
        windows = np.lib.stride_tricks.sliding_window_view(values, frames + 1, axis=0)
        result[frames:] = windows.max(axis=-1) - windows.min(axis=-1)
    return result


def build_activity(source, episode_id):
    adapter = open_source(source)
    episode = next((e for e in source['episodes'] if e['episode_id'] == episode_id), None)
    if episode is None:
        raise EditError('원본 episode가 없습니다.')
    n, fps = episode['num_frames'], float(source['fps'])
    window = max(1, round(fps * .25))
    features, loaded = adapter.features, {}
    masks = mask_mapping(features, source.get('validity_masks'))
    manifest = adapter.manifest if isinstance(adapter, CatalogSource) else {}
    profile = {s['key']: s for s in manifest.get('profile', {}).get('signals', [])}
    kuavo = source.get('robot_type') == 'kuavo_5w'
    grip_spec = profile.get('observation.gripper', {})
    gripper_source = ('bag' if kuavo and isinstance(adapter, CatalogSource)
        and manifest.get('original_path', '').lower().endswith('.bag')
        and manifest.get('origin_bag_time_ns') is not None
        and manifest.get('profile', {}).get('clock') == 'bag_receipt_time'
        and grip_spec.get('topic') == '/leju_claw_state'
        and grip_spec.get('field') == 'data.position' and grip_spec.get('scale') == .01
        and grip_spec.get('size') == 2 else 'schema')

    def matrix(key):
        if key in loaded:
            return loaded[key]
        if key not in features:
            return None
        if isinstance(adapter, CatalogSource):
            value = adapter.arrays.get(key)
        else:
            rows = adapter.rows(episode_id)
            value = [r[key] for r in rows]
        if value is None:
            return None
        value = np.asarray(value, dtype=np.float64).reshape(n, -1).copy()
        if key in masks:
            mask = matrix(masks[key])
            if mask is None or mask.shape[1] not in (1, value.shape[1]):
                value[:] = np.nan
            else:
                value[~np.broadcast_to(mask == 1, value.shape)] = np.nan
        value[~np.isfinite(value)] = np.nan
        loaded[key] = value
        return value

    def named(key, names):
        available = features.get(key, {}).get('names')
        if not isinstance(available, list) or not all(name in available for name in names):
            return None
        value = matrix(key)
        return value[:, [available.index(name) for name in names]] if value is not None else None

    def legacy(key, width, unit):
        spec = profile.get(key)
        if not kuavo or (spec and spec.get('unit') != unit):
            return None
        value = matrix(key)
        return value if value is not None and value.shape[1] == width else None

    def unknown(width):
        return np.full((n, width), np.nan)

    def arms(key, suffix):
        result = named(key, [f'joint.zarm_{side}{i}_joint.{suffix}'
                             for side in ('l', 'r') for i in range(1, 8)])
        if result is None:
            result = legacy(key, 14, 'rad')
        return unknown(14) if result is None else result

    def grippers(key, suffix, fallback):
        value = named(key, [f'gripper.{side}_claw.{suffix}' for side in ('left', 'right')])
        if value is None:
            value = legacy(fallback, 2, 'open_fraction_0_closed_1_open')
        if value is None:
            return unknown(2)
        # Real claw feedback can cross zero slightly at a mechanical stop.
        # Preserve finite recorded values; the viewer alone clamps its meter.
        return value

    states = arms('observation.state', 'position')
    commands = arms('action', 'target_position')
    grip = grippers('observation.state', 'open_fraction', 'observation.gripper')
    grip_cmd = grippers('action', 'target_open_fraction', 'action.gripper')
    delta_state, delta_cmd = window_excursion(states, window), window_excursion(commands, window)
    delta_grip = window_change(grip, window)
    series = {}
    torso = named('observation.state', [f'joint.{joint}.position' for joint in TORSO_JOINTS])
    torso_source = 'schema' if torso is not None else 'unavailable'
    if (torso is None and kuavo and isinstance(adapter, CatalogSource)
            and manifest.get('original_path', '').lower().endswith('.bag')
            and manifest.get('origin_bag_time_ns') is not None
            and manifest.get('profile', {}).get('clock') == 'bag_receipt_time'):
        torso_source = 'bag'
    series['torso_motion'] = np.max(window_excursion(unknown(4) if torso is None else torso, window), axis=1) * 180 / np.pi
    for i, side in enumerate(('left', 'right')):
        indices = slice(i * 7, (i + 1) * 7)
        series[f'{side}_gripper'] = grip[:, i]
        series[f'{side}_gripper_command'] = grip_cmd[:, i]
        series[f'{side}_gripper_change'] = delta_grip[:, i]
        series[f'{side}_arm_recorded'] = np.where(np.isfinite(commands[:, indices]).all(axis=1), 1, np.nan)
        # A missing joint makes the group unknown; nanmax would hide missing data.
        series[f'{side}_arm_command'] = np.max(np.abs(delta_cmd[:, indices]), axis=1) * 180 / np.pi
        series[f'{side}_arm_motion'] = np.max(np.abs(delta_state[:, indices]), axis=1) * 180 / np.pi

    velocity = named('action', ['base.velocity.vx', 'base.velocity.vy', 'base.velocity.yaw_rate'])
    if velocity is None:
        velocity = legacy('action.base', 3, 'm/s,m/s,rad/s')
    if velocity is None:
        velocity = unknown(3)
    series['base_command_speed'] = np.linalg.norm(velocity[:, :2], axis=1)
    series['base_command_yaw'] = np.abs(velocity[:, 2]) * 180 / np.pi

    pose = legacy('observation.base', 6, 'm,m,quaternion_xyzw')
    base_source = 'odom'
    if pose is not None:
        q = pose[:, 2:6]
        norm = np.linalg.norm(q, axis=1, keepdims=True)
        q = q / np.where(norm > 1e-8, norm, np.nan)
        x, y, z, w = q.T
        yaw = np.arctan2(2 * (w*z + x*y), 1 - 2 * (y*y + z*z))
        xy = pose[:, :2]
    else:
        pose = named('observation.state', ['base.rack.x', 'base.rack.y', 'base.rack.sin_yaw', 'base.rack.cos_yaw'])
        base_source = 'rack' if pose is not None else 'unavailable'
        pose = unknown(4) if pose is None else pose
        xy = pose[:, :2]
        yaw = np.where(np.linalg.norm(pose[:, 2:4], axis=1) > 1e-8,
                       np.arctan2(pose[:, 2], pose[:, 3]), np.nan)
    delta_xy = window_change(xy, window)
    # Wrapping the *difference* avoids a false rotation at +/- pi and preserves gaps.
    delta_yaw = window_change(yaw[:, None], window)[:, 0]
    series['base_motion_speed'] = np.linalg.norm(delta_xy, axis=1) / (window / fps)
    series['base_motion_yaw'] = np.abs(np.arctan2(np.sin(delta_yaw), np.cos(delta_yaw))) * 180 / np.pi / (window / fps)
    return {'fps': fps, 'num_frames': n, 'window_frames': window, 'window_s': window / fps,
            'base_source': base_source, 'torso_source': torso_source, 'gripper_source': gripper_source,
            'gripper_chunk_frames': max(1, round(fps * 5)),
            'torso_chunk_frames': max(1, round(fps * 5)),
            'series': {key: [round(float(v), 6) if np.isfinite(v) else None for v in values]
                       for key, values in series.items()}}
