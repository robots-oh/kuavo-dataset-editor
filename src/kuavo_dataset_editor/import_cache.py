"""Versioned immutable bag catalogs, shared across jobs but never annotations.

Identity uses the original path, size/mtime fingerprint and complete profile.
It is not a content checksum. New packs have a small completion receipt; legacy
catalogs are adopted only after structural validation of every referenced image.
An invalid/partial generation is retained, never overwritten or auto-deleted.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4
from zipfile import BadZipFile

import numpy as np

from .sources import CatalogSource, catalog_json, file_stamp, fingerprint, write_json
from .image_store import child


def legacy_images_complete(root, references):
    folders = {}
    for ref in set(references):
        if ref is None:
            continue
        relative = Path(ref)
        if relative.is_absolute() or '..' in relative.parts:
            return False
        folders.setdefault(relative.parent, set()).add(relative.name)
    for folder, needed in folders.items():
        with os.scandir(child(root, str(folder))) as entries:
            for entry in entries:
                if entry.name in needed and entry.is_file(follow_symlinks=False) and entry.stat().st_size > 0:
                    needed.remove(entry.name)
        if needed:
            return False
    return True


def seal_catalog(root):
    root = Path(root)
    files = {p.name: list(file_stamp(p)) for p in root.iterdir() if p.is_file() and p.name != 'import-ready.json'}
    write_json(root / 'import-ready.json', {'version': 1, 'files': files})


def cache_key(path, profile):
    from .bag import IMPORT_VERSION
    value = {'path': str(Path(path).resolve()), 'fingerprint': fingerprint(path),
             'profile': profile, 'import_version': IMPORT_VERSION}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def valid_catalog(root, path, profile):
    """Validate completeness without rereading gigabytes of compressed pixels."""
    from .bag import IMPORT_VERSION
    root, path = Path(root).resolve(), Path(path).resolve()
    try:
        c = catalog_json(root)
        if (c.get('import_version', 1) not in (1, IMPORT_VERSION) or
                Path(c.get('original_path', '')).resolve() != path or c.get('fingerprint') != fingerprint(path) or
                c.get('profile') != profile or c.get('fps') != profile['fps'] or
                c.get('report', {}).get('clock') != 'bag_receipt_time'):
            return False
        n = c['episodes'][0]['num_frames']
        if len(c['episodes']) != 1 or n < 1:
            return False
        receipt = root / 'import-ready.json'
        sealed = receipt.exists()
        if sealed:
            proof = json.loads(receipt.read_text())
            if proof.get('version') != 1 or 'catalog.json' not in proof['files']:
                return False
            for name, stamp in proof['files'].items():
                if list(file_stamp(child(root, name))) != stamp:
                    return False
        elif c.get('import_version', 1) != 1:
            return False  # New generations publish a receipt last.
        for key, filename in c.get('arrays', {}).items():
            array = np.load(child(root, filename), mmap_mode='r', allow_pickle=False)
            feature = c['features'][key]
            if array.shape != (n, *feature['shape']) or str(array.dtype) != feature['dtype']:
                return False
        for spec in profile['signals']:
            if spec.get('required') and spec['key'] not in c.get('arrays', {}):
                return False
            if spec['key'] in c.get('arrays', {}):
                feature = c['features'][spec['key']]
                if feature['dtype'] != 'float32' or feature['shape'] != [spec['size']]:
                    return False
                if 'order' in spec and feature.get('names') != spec['order']:
                    return False
        with np.load(child(root, c['time_mapping']), allow_pickle=False) as mapping:
            if len(mapping['bag_time_ns']) != n:
                return False
        for key in profile['cameras']:
            paths = c['images'][key]
            if len(paths) != n or key not in c['features']:
                return False
            # A valid seal proves packed artifacts unchanged. Legacy images need
            # each referenced file checked (not just the directory's mtime).
            if not sealed and not legacy_images_complete(root, paths):
                return False
        if c.get('events'):
            from .storage import event_path
            if not event_path(child(root, c['events'])).is_file():
                return False
        elif not c.get('events_source'):
            return False
        return True
    except (OSError, ValueError, KeyError, TypeError, IndexError, EOFError, BadZipFile):
        return False


class ImportCache:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / 'import-cache'
        self.root.mkdir(exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS catalogs (key TEXT PRIMARY KEY, path TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS seen (path TEXT PRIMARY KEY, stamp TEXT NOT NULL)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'index.sqlite', timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def lookup(self, key):
        with self.db() as db:
            row = db.execute('SELECT path FROM catalogs WHERE key=?', (key,)).fetchone()
        return Path(row[0]) if row else None

    def register(self, key, root, replace=True):
        with self.db() as db:
            db.execute(('INSERT OR REPLACE' if replace else 'INSERT OR IGNORE') +
                       ' INTO catalogs VALUES (?,?)', (key, str(root)))

    def discover_legacy(self):
        """Index old finished job catalogs once per changed manifest, read-only."""
        jobs = self.workspace / 'jobs'
        for manifest in jobs.glob('*/*/catalog.json'):
            if not manifest.resolve().is_relative_to(jobs.resolve()):
                continue
            stamp = json.dumps(file_stamp(manifest))
            with self.db() as db:
                previous = db.execute('SELECT stamp FROM seen WHERE path=?', (str(manifest),)).fetchone()
            if previous and previous[0] == stamp:
                continue
            try:
                c = catalog_json(manifest.parent)
                if c.get('profile') and c.get('original_path') and Path(c['original_path']).is_file():
                    # Full validation is done only for the requested candidate.
                    if c.get('fingerprint') == fingerprint(c['original_path']):
                        self.register(cache_key(c['original_path'], c['profile']), manifest.parent, replace=False)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO seen VALUES (?,?)', (str(manifest), stamp))

    def obtain(self, path, profile, sid, progress):
        from .bag import import_bag
        key = cache_key(path, profile)
        with (self.root / (key+'.lock')).open('a+') as guard:
            while True:
                try:
                    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    # Keep cancellation responsive while another job prepares it.
                    import time
                    progress(0, '같은 bag 캐시 준비 완료 대기')
                    time.sleep(.2)
            progress(0, '기존 Import 캐시 확인')
            candidate = self.lookup(key)
            if candidate is None:
                self.discover_legacy()
                candidate = self.lookup(key)
            if candidate is not None and valid_catalog(candidate, path, profile):
                result = CatalogSource(candidate).descriptor(sid)
                result['import_cache'] = {'reused': True, 'key': key}
                progress(1, '기존 정상 캐시 재사용 · 이미지 재추출 없음')
                return result
            generation = self.root / (key+'-'+uuid4().hex)
            result = import_bag(path, generation, sid, profile, progress)
            if cache_key(path, profile) != key:
                raise ValueError('Import 도중 원본이 변경되었습니다. 캐시를 재사용하지 않습니다.')
            self.register(key, generation)
            result['import_cache'] = {'reused': False, 'key': key}
            return result


def cache_for(directory):
    directory = Path(directory).resolve()
    # Both single and batch jobs write <workspace>/jobs/<job>/<source>.
    if len(directory.parents) > 2 and directory.parents[1].name == 'jobs':
        return ImportCache(directory.parents[2])
    return None
