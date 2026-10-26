"""Source-scoped annotation sharing for explicitly linked project views."""
from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

from .core import EditError


TRACKS = ('clips', 'goals', 'exclusions')
SOURCE_FIELDS = {'source_completed': False, 'source_not_required': False,
                 'source_splits': 'unspecified'}


def source_annotations(project, sid):
    """Compare instructions by text; task IDs may differ between projects."""
    project = project or {}
    state = {}
    for key in TRACKS:
        state[key] = []
        for item in project.get(key, []):
            if item['source_id'] != sid:
                continue
            row = deepcopy(item)
            if 'task_id' in row:
                row['task'] = project['tasks'][row.pop('task_id')]
            state[key].append(row)
    state.update({key: project.get(key, {}).get(sid, default)
                  for key, default in SOURCE_FIELDS.items()})
    return state


def copy_source_annotations(origin, target, source_ids):
    """Replace only changed recordings, preserving all other project content."""
    result = deepcopy(target)
    for sid in source_ids:
        left = next(s for s in origin['sources'] if s['source_id'] == sid)
        right = next(s for s in target['sources'] if s['source_id'] == sid)
        identity = lambda s: (s.get('fingerprint'), s['fps'],
                              [(e['episode_id'], e['num_frames']) for e in s['episodes']])
        if identity(left) != identity(right):
            raise EditError('연결된 프로젝트의 같은 원본 ID가 다른 recording을 가리킵니다.')
        for key in TRACKS:
            copied = deepcopy([c for c in origin.get(key, []) if c['source_id'] == sid])
            for item in copied:
                if 'task_id' not in item:
                    continue
                tid = item['task_id']; text = origin['tasks'][tid]
                if tid in result['tasks'] and result['tasks'][tid] != text:
                    tid = uuid5(NAMESPACE_URL, 'kuavo-shared-task:' + text).hex
                    if tid in result['tasks'] and result['tasks'][tid] != text:
                        raise EditError('공유 instruction ID가 충돌했습니다.')
                result['tasks'][tid] = text
                item['task_id'] = tid
            result[key] = [c for c in result.get(key, []) if c['source_id'] != sid] + copied
        for key, default in SOURCE_FIELDS.items():
            result.setdefault(key, {})[sid] = origin.get(key, {}).get(sid, default)
    return result
