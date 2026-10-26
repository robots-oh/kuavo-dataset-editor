"""Lossless storage primitives; old uncompressed catalogs remain readable."""
import gzip
import hashlib
import json
from pathlib import Path
import zlib


def event_path(path):
    path = Path(path)
    return path if path.exists() else path.with_name(path.name + '.gz')


def open_events(path, mode='rt'):
    path = event_path(path) if mode.startswith('r') else Path(path)
    if path.suffix == '.gz':
        return gzip.open(path, mode, compresslevel=1, encoding='utf-8')
    return path.open(mode.replace('t', ''), encoding='utf-8')


def dataset_event_files(root):
    root = Path(root)
    index = root / 'events.index.json'
    if index.is_file():
        for name in json.loads(index.read_text())['chunks']:
            path = root / name
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError('Event chunk escapes dataset')
            yield path
    else:
        path = event_path(root / 'events.jsonl')
        if path.is_file():
            yield path


def new_event_chunk(root, append):
    """Published chunks are immutable; append does not copy cumulative events."""
    from uuid import uuid4
    root = Path(root)
    chunks = []
    if append:
        for path in dataset_event_files(root):
            if path.parent == root:  # legacy cumulative log, retain bytes exactly
                destination = root / 'event_chunks' / (uuid4().hex + path.suffix)
                destination.parent.mkdir(exist_ok=True)
                path.rename(destination)
                path = destination
            chunks.append(path.relative_to(root).as_posix())
    destination = root / 'event_chunks' / (uuid4().hex + '.jsonl.gz')
    destination.parent.mkdir(exist_ok=True)
    chunks.append(destination.relative_to(root).as_posix())
    return destination, {'version': 1, 'chunks': chunks}


def share_unchanged_media(target, staging):
    """After the writer closes, share equal immutable chunks with the backup.

    Staging still uses independent writable copies while the official writer is
    active. Only finalized bytes with matching timestamps are eligible.
    """
    import os
    from uuid import uuid4
    for media in ('videos', 'data'):
        root = Path(target) / media
        if not root.is_dir():
            continue
        for old in root.rglob('*'):
            if not old.is_file() or old.is_symlink():
                continue
            new = Path(staging) / old.relative_to(target)
            if not new.is_file() or new.is_symlink():
                continue
            left, right = old.stat(), new.stat()
            if (left.st_ino == right.st_ino and left.st_dev == right.st_dev):
                continue
            if (left.st_size, left.st_mtime_ns, left.st_mode) != (right.st_size, right.st_mtime_ns, right.st_mode):
                continue
            with old.open('rb') as a, new.open('rb') as b:
                equal = True
                while True:
                    block = a.read(4 * 1024 * 1024)
                    if block != b.read(4 * 1024 * 1024):
                        equal = False
                        break
                    if not block:
                        break
            if not equal:
                continue
            temporary = new.with_name(new.name + '.' + uuid4().hex + '.linking')
            try:
                os.link(old, temporary)
                temporary.replace(new)
            except OSError:
                # Sharing is optional (different disks/filesystem restrictions).
                pass
            finally:
                if temporary.exists():
                    temporary.unlink()


def init_sources(db):
    db.execute('CREATE TABLE IF NOT EXISTS revision_sources '
               '(digest TEXT PRIMARY KEY, payload BLOB NOT NULL)')


def pack_revision(db, project):
    """Deduplicate immutable source manifests, not labels or Undo history."""
    result = dict(project)
    refs = []
    for source in project.get('sources', []):
        raw = json.dumps(source, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        db.execute('INSERT OR IGNORE INTO revision_sources VALUES (?,?)',
                   (digest, zlib.compress(raw, 1)))
        refs.append(digest)
    result.pop('sources', None)
    result['_storage_sources_v1'] = refs
    return json.dumps(result, ensure_ascii=False, separators=(',', ':'))


def unpack_revision(db, payload):
    result = json.loads(payload)
    refs = result.pop('_storage_sources_v1', None)
    if refs is not None:
        sources = []
        for digest in refs:
            row = db.execute('SELECT payload FROM revision_sources WHERE digest=?',
                             (digest,)).fetchone()
            if row is None:
                raise ValueError('Missing revision source: ' + digest)
            raw = zlib.decompress(row[0])
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Corrupt revision source: ' + digest)
            sources.append(json.loads(raw))
        result['sources'] = sources
    return result
