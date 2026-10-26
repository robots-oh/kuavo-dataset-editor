"""Select recordings and omit original frames already in an append target."""
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path

from .core import EditError


def exclude_not_required(project):
    """Exclude marked recordings from export, preserving the saved annotations."""
    skipped = {sid for sid, value in project.get('source_not_required', {}).items() if value is True}
    result = deepcopy(project)
    for key in ('clips', 'goals', 'exclusions'):
        result[key] = [c for c in result.get(key, []) if c['source_id'] not in skipped]
    result['_export_not_required_sources'] = sorted(skipped)
    return result


def normalize_export_options(options=None):
    """One scope for planning/preview/writing, with ver2's singular alias."""
    result = dict(options or {})
    source_id = result.pop('source_id', None)
    ids = result.get('source_ids')
    if source_id is not None and (not isinstance(source_id, str) or not source_id):
        raise EditError('source_id는 원본 id 문자열이어야 합니다.')
    if ids is not None:
        if not isinstance(ids, list) or not ids or any(not isinstance(sid, str) or not sid for sid in ids):
            raise EditError('source_ids는 비어 있지 않은 원본 id 문자열 목록이어야 합니다.')
        ids = list(dict.fromkeys(ids))
    if source_id is not None:
        if ids is not None and ids != [source_id]:
            raise EditError('source_id와 source_ids의 Export 대상이 서로 다릅니다.')
        ids = [source_id]
    if result.get('mode') == 'replace' and ids is not None:
        raise EditError('기존 dataset 수정은 프로젝트 전체 라벨을 사용합니다. 원본별 추가는 추가 모드를 사용하세요.')
    result['source_ids'] = ids
    return result


def select_sources(project, source_ids):
    if source_ids is None:
        return project
    keep = set(normalize_export_options({'source_ids': source_ids})['source_ids'])
    if keep - {s['source_id'] for s in project['sources']}:
        raise EditError('Export할 원본이 현재 프로젝트에 없습니다. 원본을 다시 선택하세요.')
    result = deepcopy(project)
    result['sources'] = [s for s in result['sources'] if s['source_id'] in keep]
    for key in ('clips', 'goals', 'exclusions'):
        result[key] = [c for c in result.get(key, []) if c['source_id'] in keep]
    for key in ('source_splits', 'source_completed', 'source_not_required'):
        result[key] = {sid: value for sid, value in result.get(key, {}).items() if sid in keep}
    return result


def select_source(project, source_id):
    """Compatibility wrapper for existing ver2 callers."""
    return select_sources(project, normalize_export_options({'source_id': source_id})['source_ids'])


def skip_exported_frames(project, target):
    """Match actual output rows, including re-imports with a new source ID.

    A bag's unexported intervals remain eligible. Saved labels are never changed.
    Registration fingerprints describe name/size/mtime, not full content hashes.
    """
    used = {c['source_id'] for c in project['clips']}
    candidates = [s for s in project['sources'] if s['source_id'] in used]
    mapping = Path(target) / 'editor/frame_mapping.parquet'
    if not candidates or not mapping.is_file():
        return project
    matches = defaultdict(set)
    for source in candidates:
        matches[source['source_id']].add(source['source_id'])
    root = Path(target) / 'editor'
    snapshots = [root / 'project.json', *(root / 'history').glob('**/project.json')]
    for path in snapshots:
        if not path.is_file():
            continue
        saved = json.loads(path.read_text())
        for original in saved.get('sources', []):
            recorded = original.get('fingerprint')
            if recorded and recorded.get('value'):
                matches[original['source_id']].update(s['source_id'] for s in candidates
                                                     if s.get('fingerprint') == recorded)
    import pyarrow.parquet as pq
    intervals = defaultdict(list)
    for batch in pq.ParquetFile(mapping).iter_batches(columns=['source_id', 'source_episode', 'source_frame']):
        for row in batch.to_pylist():
            for sid in matches.get(row['source_id'], ()):
                ranges = intervals[(sid, int(row['source_episode']))]
                frame = int(row['source_frame'])
                if ranges and ranges[-1][1] == frame:
                    ranges[-1][1] = frame + 1
                else:
                    ranges.append([frame, frame + 1])
    before = sum(c['end_frame'] - c['start_frame'] for c in project['clips'])
    for (sid, episode), ranges in intervals.items():
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
        clips = []
        for clip in project['clips']:
            if (clip['source_id'], clip.get('episode_id', 0)) != (sid, episode):
                clips.append(clip)
                continue
            parts = [(clip['start_frame'], clip['end_frame'])]
            for start, end in merged:
                remaining = []
                for a, b in parts:
                    if b <= start or a >= end:
                        remaining.append((a, b))
                    else:
                        if a < start: remaining.append((a, start))
                        if b > end: remaining.append((end, b))
                parts = remaining
            for start, end in parts:
                part = deepcopy(clip)
                if (start, end) != (clip['start_frame'], clip['end_frame']):
                    part.update(id=f"{clip['id']}:dedup:{start}:{end}", parent_id=clip['id'],
                                start_frame=start, end_frame=end,
                                new_episode=clip.get('new_episode', False) if start == clip['start_frame'] else False)
                    part.pop('merged_clip_ids', None)
                clips.append(part)
        project['clips'] = clips
    after = sum(c['end_frame'] - c['start_frame'] for c in project['clips'])
    project['_export_duplicate_frames'] = project.get('_export_duplicate_frames', 0) + before - after
    return project
