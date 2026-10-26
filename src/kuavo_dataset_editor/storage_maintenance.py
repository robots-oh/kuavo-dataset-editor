"""Explicit, offline, lossless compaction. Never instantiate Store here.

Run with editor servers/workers stopped. Originals, images and annotations are
never removed. Event compression is verified byte-for-byte before unlinking.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from uuid import uuid4

from .storage import init_sources, pack_revision, unpack_revision


def emit(**value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def digest(path, compressed=False):
    result = hashlib.sha256()
    with (gzip.open(path, 'rb') if compressed else Path(path).open('rb')) as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.digest()


def compact_db(path):
    before = path.stat().st_size
    db = sqlite3.connect(path, timeout=30)
    try:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        backup = path.with_name(path.name + '.pre-storage-v1.gz')
        if not backup.exists():
            temporary = backup.with_name(backup.name + '.' + uuid4().hex + '.pending')
            before_hash = hashlib.sha256()
            emit(phase='database_backup_start', path=str(path))
            with path.open('rb') as source, gzip.open(temporary, 'wb', compresslevel=1) as dest:
                os.fchmod(dest.fileno(), path.stat().st_mode & 0o777)
                for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                    before_hash.update(block)
                    dest.write(block)
            if digest(temporary, compressed=True) != before_hash.digest():
                raise ValueError('Database backup verification failed')
            with temporary.open('rb') as stream:
                os.fsync(stream.fileno())
            temporary.replace(backup)
            sync_directory(backup.parent)
            emit(phase='database_backup_verified', path=str(backup), bytes=backup.stat().st_size)
        init_sources(db)
        db.commit()
        count = 0
        # A single revision commits atomically with its source objects. A crash
        # leaves a readable mix of old and new revisions and can be rerun.
        keys = db.execute('SELECT id, revision FROM revisions').fetchall()
        for pid, revision in keys:
            payload = db.execute('SELECT payload FROM revisions WHERE id=? AND revision=?',
                                 (pid, revision)).fetchone()[0]
            if '_storage_sources_v1' in json.loads(payload):
                continue
            original = json.loads(payload)
            with db:
                packed = pack_revision(db, original)
                if unpack_revision(db, packed) != original:
                    raise ValueError('Revision round-trip mismatch')
                db.execute('UPDATE revisions SET payload=? WHERE id=? AND revision=?',
                           (packed, pid, revision))
            count += 1
            if count % 25 == 0:
                emit(phase='revisions', database=str(path), converted=count, total=len(keys))
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed')
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('VACUUM')
        emit(phase='database_done', path=str(path), before=before, after=path.stat().st_size,
             revisions=len(keys), converted=count)
    finally:
        db.close()


def paths_under(roots):
    """Do not follow symlinks, including directories, or traverse other mounts."""
    for root in roots:
        device = root.stat().st_dev
        for directory, dirs, names in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirs[:] = [name for name in dirs if not (parent / name).is_symlink()
                       and (parent / name).stat().st_dev == device
                       and not name.startswith(('checkpoint-', 'interrupted-'))
                       and name not in ('.git',)]
            if 'catalog.json' in names:
                # Catalog camera trees can contain millions of JPEGs. Nothing
                # below them is a maintenance target; events are at catalog root.
                dirs[:] = []
            for name in names:
                path = parent / name
                if not path.is_symlink():
                    yield path


def compress_events(paths):
    # One compressed inode per original inode; preserve existing hardlink sharing.
    groups = {}
    for path in paths:
        if path.name == 'events.jsonl':
            stat = path.stat()
            groups.setdefault((stat.st_dev, stat.st_ino), []).append(path)
    saved = 0
    for aliases in groups.values():
        first = aliases[0]
        before = first.stat()
        destinations = [p.with_name(p.name + '.gz') for p in aliases]
        if any(p.exists() for p in destinations):
            emit(phase='skip_existing_compressed', path=str(first))
            continue
        temporary = first.with_name(first.name + '.' + uuid4().hex + '.compressing')
        try:
            original_hash = hashlib.sha256()
            with first.open('rb') as source, temporary.open('xb') as raw:
                os.fchmod(raw.fileno(), before.st_mode & 0o777)
                with gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=1, mtime=0) as dest:
                    for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                        original_hash.update(block)
                        dest.write(block)
                raw.flush()
                os.fsync(raw.fileno())
            if digest(temporary, compressed=True) != original_hash.digest():
                raise ValueError('Event compression verification failed: ' + str(first))
            for path in aliases:
                stat = path.stat()
                if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
                    raise ValueError('Event log changed during maintenance: ' + str(path))
            os.chmod(temporary, before.st_mode & 0o777)
            os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
            temporary.replace(destinations[0])
            for dest in destinations[1:]:
                os.link(destinations[0], dest)
            for parent in {dest.parent for dest in destinations}:
                sync_directory(parent)
            for path in aliases:
                path.unlink()
            # nlink may include checkpoints outside the scan; do not overclaim reclaimed bytes.
            reclaimed = before.st_blocks * 512 if before.st_nlink == len(aliases) else 0
            reclaimed -= destinations[0].stat().st_blocks * 512
            saved += reclaimed
            emit(phase='events_done', path=str(first), aliases=len(aliases),
                 before=before.st_size, after=destinations[0].stat().st_size,
                 reclaimed_estimate=reclaimed)
        finally:
            if temporary.exists():
                temporary.unlink()
    return saved


def deduplicate_videos(paths):
    candidates = {}
    for path in paths:
        if path.suffix == '.mp4' and 'videos' in path.parts:
            stat = path.stat()
            # Fingerprints include mtime. Equal bytes with differing timestamps
            # must not be relinked behind an imported dataset's identity.
            candidates.setdefault((stat.st_dev, stat.st_size, stat.st_mtime_ns,
                                    stat.st_mode & 0o777), []).append(path)
    saved = 0
    for group in candidates.values():
        if len(group) < 2:
            continue
        by_hash, seen = {}, set()
        for path in group:
            stat = path.stat()
            key = (stat.st_dev, stat.st_ino)
            if key in seen:
                continue
            seen.add(key)
            checksum = digest(path)
            previous = by_hash.get(checksum)
            if previous is None:
                by_hash[checksum] = path
                continue
            # Separate files become hardlinks only after full content verification.
            temporary = path.with_name(path.name + '.' + uuid4().hex + '.linking')
            try:
                os.link(previous, temporary)
                temporary.replace(path)
            finally:
                if temporary.exists():
                    temporary.unlink()
            reclaimed = stat.st_blocks * 512 if stat.st_nlink == 1 else 0
            saved += reclaimed
            emit(phase='video_shared', path=str(path), retained=str(previous), reclaimed=reclaimed)
    return saved


def prune_completed_checkpoints(roots):
    """Only completed, published jobs; never imports or failed resumable exports."""
    for root in roots:
        jobs = root / 'jobs'
        if not jobs.is_dir():
            continue
        for publication in jobs.glob('*/publication.json'):
            job = publication.parent
            try:
                output = Path(json.loads(publication.read_text())['path'])
                info = json.loads((output / 'meta/info.json').read_text())
                if not info.get('total_frames') or (output / 'EDITOR_INCOMPLETE.json').exists():
                    continue
                # publication is written only after a successful directory swap.
                pointer = job / 'export-checkpoint.json'
                for folder in job.glob('checkpoint-*'):
                    if folder.is_dir() and not folder.is_symlink() and (folder / 'state.json').is_file():
                        shutil.rmtree(folder)
                        emit(phase='completed_checkpoint_removed', path=str(folder))
                if pointer.is_file():
                    pointer.unlink()
            except (OSError, KeyError, ValueError) as exc:
                emit(phase='checkpoint_skipped', path=str(job), reason=str(exc))


def require_offline(roots):
    if not Path('/proc').is_dir():
        raise RuntimeError('Offline maintenance currently requires Linux /proc')
    for proc in Path('/proc').glob('[0-9]*'):
        if proc.name == str(os.getpid()):
            continue
        try:
            args = (proc / 'cmdline').read_bytes().split(b'\0')
            args = [a.decode(errors='replace') for a in args if a]
        except (OSError, PermissionError):
            continue
        if 'kuavo_dataset_editor' in args and any(a in args for a in ('serve', '_worker', 'desktop')):
            raise RuntimeError(f'먼저 editor 서버/worker를 종료하세요 (PID {proc.name})')


def protected_datasets(roots):
    """Keep directory fingerprints of imported datasets and resumable requests."""
    protected = set()
    def sources(value):
        if isinstance(value, dict):
            if value.get('kind') == 'lerobot' and value.get('path'):
                protected.add(Path(value['path']).resolve())
            for key in ('sources', 'project', 'payload'):
                if key in value:
                    sources(value[key])
        elif isinstance(value, list):
            for child in value:
                sources(child)
    for root in roots:
        database = root / 'editor.sqlite'
        if database.is_file():
            db = sqlite3.connect(database)
            try:
                for row in db.execute('SELECT payload FROM projects'):
                    sources(json.loads(row[0]))
                refs = set()
                for row in db.execute('SELECT payload FROM revisions'):
                    value = json.loads(row[0])
                    if '_storage_sources_v1' in value:
                        refs.update(value['_storage_sources_v1'])
                    else:
                        sources(value)
                import zlib
                for ref in refs:
                    row = db.execute('SELECT payload FROM revision_sources WHERE digest=?', (ref,)).fetchone()
                    sources(json.loads(zlib.decompress(row[0])))
            finally:
                db.close()
        for request in (root / 'jobs').glob('*/request.json'):
            sources(json.loads(request.read_text()))
    return protected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', action='append', required=True,
                        help='Explicit editor workspace/cache root, not a rosbag or home directory')
    parser.add_argument('--apply', action='store_true', help='Write verified compaction; default is plan only')
    args = parser.parse_args()
    roots = [Path(p).resolve(strict=True) for p in args.root]
    for root in roots:
        if root == Path.home() or root == Path('/') or not root.is_dir():
            parser.error('Use explicit editor workspace/cache directories')
        if not ((root / 'editor.sqlite').is_file() or (root / 'jobs').is_dir()):
            parser.error('Not an editor workspace/cache: ' + str(root))
    emit(phase='plan', roots=list(map(str, roots)), apply=args.apply,
         preserves=['original bags', 'preview images', 'all annotations', 'all revision history', 'all event messages'])
    if not args.apply:
        return
    require_offline(roots)
    started = time.monotonic()
    for root in roots:
        if (root / 'editor.sqlite').is_file():
            compact_db(root / 'editor.sqlite')
    prune_completed_checkpoints(roots)
    protected = protected_datasets(roots)
    emit(phase='protected_datasets', paths=sorted(map(str, protected)))
    paths = [p for p in paths_under(roots)
             if ((p.name == 'events.jsonl' and not any(p.is_relative_to(d) for d in protected))
                 or (p.suffix == '.mp4' and 'videos' in p.parts))]
    compressed = compress_events(paths)
    shared = deduplicate_videos(paths)
    emit(phase='done', seconds=time.monotonic() - started,
         estimated_events_reclaimed=compressed, videos_reclaimed=shared)


if __name__ == '__main__':
    main()
