"""Read-only gap inspection and explicitly approved, export-only corrections."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np

from .core import EditError, compile_selection, subtract
from .sources import CatalogSource, open_source


def _groups(indices):
    if not len(indices):
        return []
    return [(int(g[0]), int(g[-1]) + 1) for g in np.split(indices, np.where(np.diff(indices) != 1)[0] + 1)]


def _data(adapter, episode):
    if isinstance(adapter, CatalogSource):
        n = adapter.manifest['episodes'][0]['num_frames']
        if adapter.manifest.get('time_mapping'):
            with np.load(adapter.root / adapter.manifest['time_mapping'], allow_pickle=False) as saved:
                times = {key: saved[key] for key in saved.files}
        else:
            times = {}
        times.setdefault('bag_time_ns', np.arange(n, dtype=np.int64) * 1_000_000_000 // adapter.manifest['fps'])
        files = ['catalog.json', *adapter.manifest.get('arrays', {}).values()]
        if adapter.manifest.get('time_mapping'):
            files.append(adapter.manifest['time_mapping'])
        stamp = [(name, (adapter.root / name).stat().st_size, (adapter.root / name).stat().st_mtime_ns) for name in files]
        return adapter.arrays, adapter.manifest['images'], times, stamp
    rows = adapter.rows(episode)
    arrays = {key: np.asarray([row[key] for row in rows], dtype=ft['dtype'])
              for key, ft in adapter.features.items() if ft['dtype'] not in ('video', 'image', 'string')}
    times = {'bag_time_ns': np.asarray([int(round(row['timestamp'] * 1e9)) for row in rows], dtype=np.int64)}
    files = [adapter.root / 'meta/info.json', *sorted((adapter.root / 'data').rglob('*.parquet'))]
    stamp = [(str(path.relative_to(adapter.root)), path.stat().st_size, path.stat().st_mtime_ns) for path in files]
    # Existing encoded LeRobot video is not fully decoded during a metadata preview.
    return arrays, {}, times, stamp


def inspect_quality(project):
    from .inspection_cache import gap_report
    return gap_report(project, _inspect_quality)


def _inspect_quality(project):
    plan = compile_selection(project)
    sources = {s['source_id']: s for s in project['sources']}
    selected = {}
    for run in plan['runs']:
        key = run['source_id'], run['episode_id']
        selected.setdefault(key, []).extend(range(run['start_frame'], run['end_frame']))
    issues, stamps, invalid_total = [], [], 0
    for (sid, episode), frames in selected.items():
        source = sources[sid]
        adapter = open_source(source)
        arrays, images, times, stamp = _data(adapter, episode)
        stamps.append([sid, episode, stamp])
        target_times = times['bag_time_ns']
        n = len(target_times)
        selected_mask = np.zeros(n, dtype=bool)
        selected_mask[frames] = True
        invalid = np.zeros(n, dtype=bool)
        features = {}
        for key, array in arrays.items():
            if np.issubdtype(array.dtype, np.number):
                good = np.isfinite(array).reshape(n, -1).all(axis=1)
                features[key] = (good, False, array)
        for key, paths in images.items():
            features[key] = (np.asarray([path is not None for path in paths]), True, None)
        for key, (good, image, array) in features.items():
            missing = ~good & selected_mask
            invalid |= missing
            sample_times = times.get(key + '/message_ns', target_times)
            candidates = np.flatnonzero(good & (sample_times >= 0))
            for start, end in _groups(np.flatnonzero(missing)):
                lefts = candidates[sample_times[candidates] <= target_times[start]]
                rights = candidates[sample_times[candidates] >= target_times[end - 1]]
                left = int(lefts[np.argmax(sample_times[lefts])]) if len(lefts) else None
                right = int(rights[np.argmin(sample_times[rights])]) if len(rights) else None
                method = ('hold_image' if image else 'hold') if left is not None else None
                if not image and key.startswith('observation.') and array.dtype.kind == 'f':
                    method = 'linear' if left is not None and right is not None and sample_times[right] > sample_times[left] else None
                identity = [sid, episode, key, start, end]
                issue = {'id': hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24],
                         'source_id': sid, 'episode_id': episode,
                         'source_name': Path(source.get('original_path', source['path'])).name,
                         'feature': key, 'start_frame': start, 'end_frame': end,
                         'missing_frames': end - start, 'duration_s': (end - start) / source['fps'],
                         'start_time_s': start / source['fps'], 'end_time_s': end / source['fps'],
                         'method': method, 'left_frame': left, 'right_frame': right,
                         'left_time_ns': int(sample_times[left]) if left is not None else None,
                         'right_time_ns': int(sample_times[right]) if right is not None else None,
                         'last_target_time_ns': int(target_times[end - 1]),
                         'sample_gap_s': float((sample_times[right] - sample_times[left]) / 1e9) if left is not None and right is not None else None,
                         'max_hold_age_s': float((target_times[end - 1] - sample_times[left]) / 1e9) if left is not None else None}
                if not image and left is not None and right is not None:
                    issue['max_endpoint_delta'] = float(np.max(np.abs(array[right].astype('float64') - array[left])))
                    specs = getattr(adapter, 'manifest', {}).get('profile', {}).get('signals', [])
                    issue['unit'] = next((s.get('unit') for s in specs if s['key'] == key), None)
                issues.append(issue)
        invalid_total += int(invalid.sum())
    report = {'version': 1, 'project_revision': project['revision'], 'checked_frames': plan['kept_frames'],
              'invalid_frames': invalid_total, 'issues': issues,
              'scope': 'catalog required numeric/camera availability and LeRobot numeric values; no full video decode'}
    payload = {'report': report, 'stamps': stamps, 'clips': project['clips']}
    report['token'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    report['ready'] = not issues
    return report


def resolve_quality(project, approval=None):
    report = inspect_quality(project)
    if not approval:
        return deepcopy(project), report
    if not isinstance(approval, dict) or approval.get('token') != report['token']:
        raise EditError('누락 점검 후 데이터나 라벨이 바뀌었습니다. 다시 점검하고 선택하세요.')
    decisions = approval.get('decisions')
    if not isinstance(decisions, dict) or set(decisions) != {i['id'] for i in report['issues']}:
        raise EditError('모든 누락 구간에 보정 또는 제외를 명시적으로 선택하세요.')
    for issue in report['issues']:
        choice = decisions[issue['id']]
        allowed = ['exclude'] + ([issue['method']] if issue['method'] else [])
        if choice not in allowed:
            raise EditError('지원하지 않거나 앞뒤 실측값이 부족한 보정 방법입니다.')
        issue['decision'] = choice
    report['ready'] = True
    return selection_with_quality(project, report), report


def selection_with_quality(project, report):
    result = deepcopy(project)
    for issue in report['issues']:
        if issue.get('decision') == 'exclude':
            result['clips'] = subtract(result['clips'], issue['source_id'], issue['episode_id'],
                                       issue['start_frame'], issue['end_frame'])
    return result


def repair_index(report):
    result = {}
    for issue in report['issues']:
        if issue.get('decision') in ('linear', 'hold', 'hold_image'):
            for frame in range(issue['start_frame'], issue['end_frame']):
                result.setdefault((issue['source_id'], issue['episode_id'], frame), []).append(issue)
    return result


def corrected_row(adapter, episode, frame, repairs, time_map=None):
    if not repairs:
        return adapter.row(episode, frame)
    fixes = {issue['feature']: issue for issue in repairs}
    result = {}
    for key in adapter.features:
        fix = fixes.get(key)
        if fix is None:
            result.update(adapter.row(episode, frame, camera_key=key))
            continue
        left = adapter.row(episode, fix['left_frame'], camera_key=key)[key]
        if fix['decision'] == 'hold_image':
            result[key] = left
        elif fix['decision'] == 'hold':
            raw = adapter.row(episode, frame, camera_key=key, validate=False)[key]
            result[key] = np.where(np.isfinite(raw), raw, left).astype(raw.dtype)
        else:
            right = adapter.row(episode, fix['right_frame'], camera_key=key)[key]
            if time_map is not None:
                target = int(time_map['bag_time_ns'][frame])
            elif isinstance(adapter, CatalogSource):
                target = frame * 1_000_000_000 // adapter.manifest['fps']
            else:
                target = int(round(frame / adapter.fps * 1e9))
            alpha = (target - fix['left_time_ns']) / (fix['right_time_ns'] - fix['left_time_ns'])
            if not 0 <= alpha <= 1:
                raise EditError('보간은 같은 recording의 앞뒤 실측값 사이에서만 가능합니다.')
            raw = adapter.row(episode, frame, camera_key=key, validate=False)[key]
            result[key] = np.where(np.isfinite(raw), raw, left + alpha * (right - left)).astype(raw.dtype)
    return result
