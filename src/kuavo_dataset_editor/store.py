"""SQLite project revisions and durable job records; payloads remain portable JSON."""
import json
import sqlite3
import threading
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from .core import EditError, compile_selection, normalize_step_annotations, merge_adjacent_clips
from .shared_annotations import source_annotations, copy_source_annotations


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._source_cache = OrderedDict()
        self.db = sqlite3.connect(self.root / "editor.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, revision INTEGER, payload TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS revisions (id TEXT, revision INTEGER, payload TEXT, PRIMARY KEY(id, revision))")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT)")
        self.db.commit()
        # A killed process cannot own a running job after restart.
        for job in self.jobs():
            if job["status"] in ("queued", "running", "cancelling"):
                self.put_job({**job, "status": "failed", "error": "서버 재시작으로 중단됨. 새 작업으로 재시도하세요."})

    def projects(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM projects ORDER BY rowid DESC")]

    def get(self, pid, revision=None):
        with self.lock:
            row = self.db.execute("SELECT payload FROM projects WHERE id=?", (pid,)).fetchone() if revision is None else self.db.execute("SELECT payload FROM revisions WHERE id=? AND revision=?", (pid, revision)).fetchone()
            if not row:
                raise KeyError(pid)
            project = json.loads(row[0])
            if revision is None and project.get('_annotation_track') != 'goal':
                merged_clips = merge_adjacent_clips(project['clips'], project['tasks'])
                if merged_clips != project['clips']:
                    # One recoverable migration revision. Old open tabs must
                    # reload rather than edit a fragment of a now-merged clip.
                    return self.save({**project, 'clips': merged_clips}, project["revision"])
            return project

    def revision(self, pid):
        with self.lock:
            row = self.db.execute('SELECT revision FROM projects WHERE id=?', (pid,)).fetchone()
            if not row:
                raise KeyError(pid)
            return row[0]

    def source(self, pid, sid):
        """Read-only descriptors for playback; do not parse all labels per camera.

        Check the durable revision on every call, including changes made by other
        Store instances. Keep at most two project indexes and load a cold index
        once under the lock so concurrent camera requests share it.
        """
        with self.lock:
            revision = self.revision(pid)
            cached = self._source_cache.get(pid)
            if cached is None or cached[0] != revision:
                row = self.db.execute('SELECT revision,payload FROM projects WHERE id=?', (pid,)).fetchone()
                if not row:
                    raise KeyError(pid)
                project = json.loads(row[1])
                cached = (row[0], {s['source_id']: s for s in project['sources']})
                self._source_cache[pid] = cached
                if len(self._source_cache) > 2:
                    self._source_cache.popitem(last=False)
            self._source_cache.move_to_end(pid)
            if sid not in cached[1]:
                raise KeyError(sid)
            return cached[1][sid]

    def _write_revision(self, project, revision):
        project['revision'] = revision
        payload = json.dumps(project, ensure_ascii=False)
        self.db.execute('INSERT OR REPLACE INTO projects VALUES (?,?,?)',
                        (project['project_id'], revision, payload))
        self.db.execute('INSERT INTO revisions VALUES (?,?,?)',
                        (project['project_id'], revision, payload))

    def save(self, project, expected):
        compile_selection(project)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                pid = project["project_id"]
                row = self.db.execute("SELECT revision,payload FROM projects WHERE id=?", (pid,)).fetchone()
                if row and row[0] != expected:
                    raise EditError("다른 창에서 변경했습니다. 프로젝트를 다시 열어주세요.")
                if not row and expected != -1:
                    raise EditError("프로젝트가 없습니다.")
                before = json.loads(row[1]) if row else None
                group = project.get('annotation_group')
                if before and group != before.get('annotation_group'):
                    raise EditError('프로젝트의 annotation 연결은 일반 편집으로 변경할 수 없습니다.')
                p = normalize_step_annotations(project)
                if group:
                    changed = {s['source_id'] for s in p['sources']
                               if source_annotations(before, s['source_id'])
                               != source_annotations(p, s['source_id'])}
                    peers = self.db.execute(
                        "SELECT revision,payload FROM projects WHERE id<>? "
                        "AND json_extract(payload,'$.annotation_group')=?", (pid, group)).fetchall()
                    for revision, payload in peers:
                        peer = json.loads(payload)
                        shared = changed & {s['source_id'] for s in peer['sources']}
                        shared = {sid for sid in shared if source_annotations(p, sid)
                                  != source_annotations(peer, sid)}
                        if shared:
                            updated = normalize_step_annotations(copy_source_annotations(p, peer, shared))
                            compile_selection(updated)
                            self._write_revision(updated, revision + 1)
                self._write_revision(p, row[0] + 1 if row else 0)
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            return p

    def put_job(self, job):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job, ensure_ascii=False)))

    def relocate_sources(self, old_path, backup, expected_fingerprint):
        """Keep open projects tied to immutable inputs after a dataset directory swap.

        Read latest revisions under a cross-process SQLite write lock; never replace
        a user's newer clips/tasks with the older export snapshot.
        """
        old_path, backup = str(Path(old_path).resolve()), str(Path(backup).resolve())
        changed = []
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                rows = self.db.execute("SELECT id,revision,payload FROM projects").fetchall()
                for pid, revision, payload in rows:
                    project = json.loads(payload)
                    moved = False
                    for source in project["sources"]:
                        if (source["kind"] == "lerobot" and str(Path(source["path"]).resolve()) == old_path
                                and source.get("fingerprint") == expected_fingerprint):
                            source["path"] = backup
                            source["repository_target"] = old_path
                            if source.get("original_path") == old_path:
                                source["original_path"] = backup
                            moved = True
                    if not moved:
                        continue
                    project["revision"] = revision + 1
                    encoded = json.dumps(project, ensure_ascii=False)
                    self.db.execute("UPDATE projects SET revision=?,payload=? WHERE id=?", (revision + 1, encoded, pid))
                    self.db.execute("INSERT INTO revisions VALUES (?,?,?)", (pid, revision + 1, encoded))
                    changed.append(pid)
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
        return changed

    def jobs(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM jobs ORDER BY rowid DESC")]

    def job(self, jid):
        with self.lock:
            row = self.db.execute("SELECT payload FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row:
                raise KeyError(jid)
            return json.loads(row[0])
