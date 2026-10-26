"""Explicit offline repair of moved original bag paths, without editing labels."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from .storage import init_sources, pack_revision
from .storage_maintenance import require_offline


def bag_fingerprint(path):
    stat = path.stat()
    value = f'{path.name}:{stat.st_size}:{stat.st_mtime_ns}\n'
    return {'method': 'manifest-size-mtime-sha256',
            'value': hashlib.sha256(value.encode()).hexdigest()}


def relink(workspace, bag_roots, apply=False):
    workspace = Path(workspace).resolve(strict=True)
    database = workspace / 'editor.sqlite'
    if not database.is_file() or database.is_symlink():
        raise ValueError('Explicit editor workspace required')
    candidates = {}
    for root in dict.fromkeys(Path(p).resolve(strict=True) for p in bag_roots):
        if not root.is_dir() or root == Path('/') or root == Path.home():
            raise ValueError('Use explicit original rosbag folders')
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
            for name in files:
                path = Path(directory) / name
                if path.suffix == '.bag' and not path.is_symlink():
                    candidates.setdefault(name, {})[str(path.resolve())] = bag_fingerprint(path)
    if apply:
        require_offline([workspace])
    db = sqlite3.connect(str(database) if apply else 'file:' + str(database) + '?mode=ro',
                         uri=not apply, timeout=30)
    report = {'workspace': str(workspace), 'apply': apply, 'changes': [], 'skipped': [],
              'projects_changed': 0, 'historical_revisions_preserved': True}
    try:
        if apply:
            db.execute('BEGIN IMMEDIATE')
            init_sources(db)
            db.execute('CREATE TABLE IF NOT EXISTS source_relocations '
                       '(project_id TEXT, revision INTEGER, payload TEXT)')
        for pid, revision, payload in db.execute('SELECT id,revision,payload FROM projects').fetchall():
            original = json.loads(payload)
            updated = deepcopy(original)
            changes = []
            for index, source in enumerate(updated['sources']):
                old = source.get('original_path')
                if source.get('kind') != 'catalog' or not old or Path(old).suffix != '.bag' or Path(old).exists():
                    continue
                if not Path(source['path']).is_dir():
                    report['skipped'].append({'project': pid, 'source_id': source['source_id'],
                                               'reason': 'extraction_cache_missing'})
                    continue
                matching = [path for path, fp in candidates.get(Path(old).name, {}).items()
                            if fp == source.get('fingerprint')]
                if len(matching) != 1:
                    report['skipped'].append({'project': pid, 'source_id': source['source_id'],
                        'old': old, 'reason': 'no_exact_fingerprint' if not matching else 'ambiguous'})
                    continue
                change = {'project': pid, 'source_id': source['source_id'], 'index': index,
                          'old': old, 'new': matching[0], 'revision_before': revision}
                changes.append(change)
                source['original_path'] = matching[0]
            if not changes:
                continue
            # Prove the complete project is identical except the explicitly
            # approved path changes, not just its clips/tasks subset.
            restored = deepcopy(updated)
            for change in changes:
                restored['sources'][change['index']]['original_path'] = change['old']
            if restored != original:
                raise ValueError('Unexpected project change during source relocation')
            report['changes'].extend(changes)
            report['projects_changed'] += 1
            if apply:
                updated['revision'] = revision + 1
                encoded = json.dumps(updated, ensure_ascii=False)
                db.execute('UPDATE projects SET revision=?,payload=? WHERE id=?', (revision + 1, encoded, pid))
                db.execute('INSERT INTO revisions VALUES (?,?,?)', (pid, revision + 1, pack_revision(db, updated)))
                db.execute('INSERT INTO source_relocations VALUES (?,?,?)',
                           (pid, revision + 1, json.dumps(changes, ensure_ascii=False)))
        if apply:
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
    except BaseException:
        if apply:
            db.rollback()
        raise
    finally:
        db.close()
    report['unique_bags_relinked'] = len({c['new'] for c in report['changes']})
    report['descriptor_paths_relinked'] = len(report['changes'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--bag-root', action='append', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report')
    args = parser.parse_args(argv)
    result = relink(args.workspace, args.bag_root, args.apply)
    if args.report:
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
