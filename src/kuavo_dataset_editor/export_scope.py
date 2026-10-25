"""Select recordings and omit original frames already in an append target."""
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path

from .core import EditError


def select_source(project, source_id):
    if source_id is None:
        return project
    if not isinstance(source_id, str) or not any(s['source_id'] == source_id for s in project['sources']):
        raise EditError('Export할 원본이 현재 프로젝트에 없습니다. 원본을 다시 선택하세요.')
    result = deepcopy(project)
    result['sources'] = [s for s in result['sources'] if s['source_id'] == source_id]
    for key in ('clips', 'goals', 'exclusions'):
        result[key] = [c for c in result.get(key, []) if c['source_id'] == source_id]
    for key in ('source_splits', 'source_completed', 'source_not_required'):
        result[key] = {sid: value for sid, value in result.get(key, {}).items() if sid == source_id}
    return result


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
