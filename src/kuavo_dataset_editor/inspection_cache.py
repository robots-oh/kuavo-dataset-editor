"""Small process-local cache of immutable gap reports, never approvals.

Each lookup stats the current catalog/numeric/timestamp metadata. LeRobot inputs
also inventory current parquet metadata/data files. We do not cache the result of
checking arbitrary directory contents from just the root directory mtime.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading

from .core import EditError
from .sources import catalog_json, file_stamp

_cache, _lock = OrderedDict(), threading.RLock()


def _key(project):
    used = {clip['source_id'] for clip in project['clips']}
    stamps = []
    for source in project['sources']:
        if source['source_id'] not in used:
            continue
        root = Path(source['path'])
        if source['kind'] == 'catalog':
            manifest = catalog_json(root)
            files = [root / 'catalog.json', *(root / p for p in manifest.get('arrays', {}).values())]
            if manifest.get('time_mapping'):
                files.append(root / manifest['time_mapping'])
        else:
            files = [path for base in ('meta', 'data') for path in (root / base).rglob('*') if path.is_file()]
        stamps.append([source['source_id'], str(root), source.get('original_path'), source.get('fps'), source.get('episodes'),
                       [[str(path), file_stamp(path)] for path in sorted(set(files))]])
    body = {'revision': project['revision'], 'clips': project['clips'], 'tasks': project['tasks'],
            'sources': stamps}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def gap_report(project, compute):
    if any(source.get('export_derivation') for source in project['sources']):
        return compute(project)  # Job-local derived arrays are not the original catalog files.
    key = _key(project)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return deepcopy(_cache[key])
    report = compute(project)
    if _key(project) != key:
        raise EditError('누락 검사 중 데이터가 변경됐습니다. 새 데이터로 다시 미리보기 하세요.')
    with _lock:
        _cache[key] = deepcopy(report)
        _cache.move_to_end(key)
        while len(_cache) > 16:
            _cache.popitem(last=False)
    return report
