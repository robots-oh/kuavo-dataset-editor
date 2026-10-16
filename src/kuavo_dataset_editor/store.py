"""SQLite project revisions and durable job records; payloads remain portable JSON."""
import json
import sqlite3
import threading
from copy import deepcopy
from pathlib import Path
from .core import EditError, compile_selection


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
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
            return json.loads(row[0])

    def save(self, project, expected):
        compile_selection(project)
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                pid = project["project_id"]
                row = self.db.execute("SELECT revision FROM projects WHERE id=?", (pid,)).fetchone()
                if row and row[0] != expected:
                    raise EditError("다른 창에서 변경했습니다. 프로젝트를 다시 열어주세요.")
                if not row and expected != -1:
                    raise EditError("프로젝트가 없습니다.")
                p = deepcopy(project)
                p["revision"] = row[0] + 1 if row else 0
                payload = json.dumps(p, ensure_ascii=False)
                self.db.execute("INSERT OR REPLACE INTO projects VALUES (?, ?, ?)", (pid, p["revision"], payload))
                self.db.execute("INSERT INTO revisions VALUES (?, ?, ?)", (pid, p["revision"], payload))
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
