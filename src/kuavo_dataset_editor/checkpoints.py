"""Episode checkpoints: copy small mutable metadata, reuse finalized media files.

Every episode uses a fresh official resume writer, so existing data/video chunks
are immutable. Metadata is journaled before that writer opens; uncommitted new
files are quarantined on recovery, never mistaken for completed episodes.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .core import EditError
from .sources import write_json


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def frozen(name):
    """Editor files that are never rewritten once written: previous exports' history snapshots.

    events.jsonl is appended only after the episode loop, which first detaches it (see detach)."""
    return name.startswith('editor/history/') or name == 'editor/events.jsonl'


def link_or_copy(source, destination):
    """Hard link an immutable file (same disk: no bytes copied); copy when linking is impossible."""
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def detach(path):
    """Give `path` its own inode before writing to it in place, so linked checkpoints/history keep their bytes."""
    path = Path(path)
    if path.is_file() and path.stat().st_nlink > 1:
        temporary = path.with_name(path.name + '.' + uuid4().hex + '.detach')
        shutil.copy2(path, temporary)
        temporary.replace(path)


def checkpoint_state(job_root):
    path = Path(job_root) / 'export-checkpoint.json'
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class EpisodeCheckpoint:
    def __init__(self, staging, job_root, identity):
        self.staging, self.root = Path(staging), Path(job_root)
        self.identity = signature(identity)

    def save(self, completed, frames, rows, truncations, timings):
        previous = checkpoint_state(self.root)
        previous_root = None
        if (previous and previous.get('identity') == self.identity
                and previous.get('staging') == str(self.staging)):
            candidate = self.root / previous['directory']
            if candidate.parent == self.root and candidate.is_dir() and not candidate.is_symlink():
                previous_root = candidate
        folder = self.root / ('checkpoint-' + uuid4().hex)
        folder.mkdir()
        inventory = {}
        if self.staging.exists():
            for path in self.staging.rglob('*'):
                if path.is_symlink():
                    raise EditError('checkpoint 출력에는 심볼릭 링크를 사용할 수 없습니다.')
                if not path.is_file():
                    continue
                name = path.relative_to(self.staging).as_posix()
                stat = path.stat()
                inventory[name] = [stat.st_size, stat.st_mtime_ns]
                if name.split('/')[0] in ('meta', 'editor'):
                    dest = folder / 'mutable' / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    # Large frozen provenance (GBs, grows with every export) is linked, not copied per episode.
                    if frozen(name):
                        link_or_copy(path, dest)
                        continue
                    saved = previous_root / 'mutable' / name if previous_root else None
                    # Link only immutable checkpoint copies to each other. Never
                    # link the live staging file: the writer may overwrite it.
                    if (saved and previous['files'].get(name) == inventory[name]
                            and saved.is_file() and not saved.is_symlink()
                            and [saved.stat().st_size, saved.stat().st_mtime_ns] == inventory[name]):
                        try:
                            os.link(saved, dest)
                            continue
                        except OSError:
                            pass  # Filesystems without hard links retain copy semantics.
                    shutil.copy2(path, dest)
        write_json(folder / 'rows.json', rows)
        write_json(folder / 'truncations.json', truncations)
        state = dict(version=1, identity=self.identity, directory=folder.name,
                     staging=str(self.staging), completed_episodes=completed,
                     completed_frames=frames, files=inventory, timings=timings)
        atomic_json(folder / 'state.json', state)
        # Only this pointer advertises completion, after all sidecars are ready.
        atomic_json(self.root / 'export-checkpoint.json', state)
        return state

    def restore(self):
        state = checkpoint_state(self.root)
        if state is None:
            return None
        if (state.get('version') != 1 or state.get('identity') != self.identity
                or state.get('staging') != str(self.staging)):
            raise EditError('재개 snapshot/원본/환경이 변경됐습니다. 새 Export를 시작하세요. 기존 checkpoint는 보존됩니다.')
        if self.staging.is_symlink():
            raise EditError('checkpoint staging이 심볼릭 링크로 변경됐습니다.')
        folder = self.root / state['directory']
        if folder.parent != self.root or not folder.is_dir():
            raise EditError('checkpoint metadata 폴더가 없습니다.')
        inventory = state['files']
        # Check every completed, immutable file before touching any partial work.
        for name, expected in inventory.items():
            path = self.staging / name
            if not path.resolve().is_relative_to(self.staging.resolve()):
                raise EditError('잘못된 checkpoint 경로입니다.')
            if name.split('/')[0] not in ('meta', 'editor'):
                stat = path.stat() if path.is_file() else None
                if stat is None or [stat.st_size, stat.st_mtime_ns] != expected:
                    raise EditError(f'완료 episode 파일이 변경/누락됐습니다: {name}. checkpoint를 덮어쓰지 않습니다.')
        # Preserve all interrupted work, including replaced mutable metadata.
        quarantine = self.root / ('interrupted-' + uuid4().hex)
        for path in list(self.staging.rglob('*')) if self.staging.exists() else []:
            if not path.is_file() and not path.is_symlink():
                continue
            name = path.relative_to(self.staging).as_posix()
            if name not in inventory or name.split('/')[0] in ('meta', 'editor'):
                dest = quarantine / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), dest)
        if not inventory:
            if self.staging.exists():
                quarantine.mkdir(parents=True, exist_ok=True)
                shutil.move(str(self.staging), quarantine / 'empty-staging')
            return {**state, 'rows': json.loads((folder / 'rows.json').read_text()),
                    'truncations': json.loads((folder / 'truncations.json').read_text())}
        self.staging.mkdir(exist_ok=True)
        mutable = folder / 'mutable'
        if mutable.exists():
            shutil.copytree(mutable, self.staging, dirs_exist_ok=True, copy_function=shutil.copy2)
        return {**state, 'rows': json.loads((folder / 'rows.json').read_text()),
                'truncations': json.loads((folder / 'truncations.json').read_text())}
