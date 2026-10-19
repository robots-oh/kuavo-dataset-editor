"""Isolated worker processes with durable request, status, log and cancellation."""
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4

from .sources import LeRobotSource, demo_source, write_json


class Cancelled(Exception):
    pass


def import_one(path, directory, source_id, profile, progress):
    path = Path(path).expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".bag":
        from .bag import import_bag
        return import_bag(path, directory, source_id, profile, progress)
    return LeRobotSource(path).descriptor(source_id)


def import_many(payload, directory, progress, checkpoint):
    """Sequential imports/downloads; completed sources survive cancellation."""
    result = {"sources": [], "failures": [], "downloads": []}
    for index, item in enumerate(payload["items"]):
        count = len(payload["items"])
        progress(index / count, f"{index+1}/{count} · {Path(item['path']).name}")
        try:
            path = item["path"]
            if payload.get("host"):
                from .ssh_sources import download_bag
                download = download_bag(payload["host"], path, directory.parent.parent / "downloads",
                    lambda f, m: progress((index+0.65*f)/count, f"{index+1}/{count} · {m}"))
                result["downloads"].append(download)
                path = download["path"]
            start = 0.65 if payload.get("host") else 0
            source = import_one(path, directory / item["source_id"], item["source_id"], payload["profile"],
                lambda f, m: progress((index+start+(1-start)*f)/count, f"{index+1}/{count} · {m}"))
            result["sources"].append(source)
        except Cancelled:
            raise
        except Exception as exc:
            traceback.print_exc()
            result["failures"].append({"path": item["path"], "error": str(exc)})
        checkpoint(result)
    return result


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    write_json(temporary, value)
    temporary.replace(path)


class JobManager:
    def __init__(self, root):
        self.root = Path(root) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.processes = {}
        self.lock = threading.Lock()

    def start(self, kind, payload):
        jid = uuid4().hex
        directory = self.root / jid
        directory.mkdir()
        write_json(directory / "request.json", {"kind": kind, "payload": payload})
        atomic_json(directory / "status.json", {"id": jid, "kind": kind, "status": "queued", "progress": 0, "created": time.time()})
        with (directory / "worker.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-m", "kuavo_dataset_editor", "_worker", str(directory)], stdout=log, stderr=subprocess.STDOUT)
        write_json(directory / "process.json", {"pid": process.pid})
        self.processes[jid] = process
        return self.get(jid)

    def get(self, jid):
        if len(jid) != 32 or not all(c in "0123456789abcdef" for c in jid):
            raise KeyError(jid)
        directory = self.root / jid
        if not directory.exists():
            raise KeyError(jid)
        status = json.loads((directory / "status.json").read_text())
        process = self.processes.get(jid)
        if process:
            exit_code = process.poll()
            alive = exit_code is None
        else:
            process_file = directory / "process.json"
            pid = status.get("pid") or (json.loads(process_file.read_text())["pid"] if process_file.exists() else None)
            # Reconnected workers must identify this exact job in their live command line.
            try:
                alive = bool(pid and str(directory).encode() in Path(f"/proc/{pid}/cmdline").read_bytes())
            except OSError:
                alive = False
        if status["status"] in ("running", "queued") and not alive:
            status.update(status="failed", error="worker가 종료되었습니다. worker.log를 확인하세요.")
            atomic_json(directory / "status.json", status)
        return status

    def all(self):
        return sorted([self.get(p.name) for p in self.root.iterdir() if p.is_dir() and (p / "status.json").exists()], key=lambda s: s.get("created", 0), reverse=True)

    def cancel(self, jid):
        status = self.get(jid)
        if status["status"] in ("running", "queued"):
            (self.root / jid / "cancel").touch()
        return status


def worker(directory):
    directory = Path(directory).resolve()
    request = json.loads((directory / "request.json").read_text())
    state = json.loads((directory / "status.json").read_text())
    state.update(status="running", pid=os.getpid())
    atomic_json(directory / "status.json", state)
    last_update = 0

    def progress(fraction, message):
        nonlocal last_update
        if (directory / "cancel").exists():
            raise Cancelled()
        if time.monotonic()-last_update > 0.4 or fraction >= 1:
            state.update(progress=fraction, message=message)
            atomic_json(directory / "status.json", state)
            last_update = time.monotonic()

    try:
        progress(0, "작업 시작")
        payload = request["payload"]
        if request["kind"] == "demo":
            result = demo_source(directory / "source", payload["source_id"])
        elif request["kind"] == "import":
            result = import_one(payload["path"], directory / "source", payload["source_id"], payload["profile"], progress)
        elif request["kind"] in ("import-batch", "ssh-import"):
            def checkpoint(result):
                state["result"] = result
                atomic_json(directory / "status.json", state)
            result = import_many(payload, directory, progress, checkpoint)
        elif request["kind"] == "export":
            from .exporter import export_dataset
            result = export_dataset(payload["project"], payload["target"], directory, progress, payload.get("options"))
            relocation = result.get("source_relocation")
            if relocation:
                from .store import Store
                store = Store(directory.parent.parent)
                result["relocated_projects"] = store.relocate_sources(
                    relocation["old_path"], relocation["backup"], relocation["fingerprint"])
        elif request["kind"] == "hub-plan":
            from .hub import prepare_upload
            result = prepare_upload(payload["path"], payload["repo_id"], payload["private"])
        elif request["kind"] == "hub-upload":
            from .hub import publish_upload
            result = publish_upload(payload["plan"], confirmed=payload["confirmed"],
                                    sync_existing=payload["sync_existing"], progress=progress)
        else:
            raise ValueError("알 수 없는 작업")
        state.update(status="completed", progress=1, result=result,
                     message={"export": "export 완료", "hub-plan": "업로드 미리보기 준비됨 — 아직 전송하지 않았습니다.",
                              "hub-upload": "Hub 업로드 완료"}.get(request["kind"], "가져오기 완료"))
        if request["kind"] in ("import-batch", "ssh-import"):
            state["message"] = f"가져오기 처리 완료 · 성공 {len(result['sources'])} / 실패 {len(result['failures'])}"
    except Cancelled:
        state.update(status="cancelled", message="취소됨. 로컬 원본은 변경하지 않았습니다."
                     + (" Hub 신규 repo가 만들어졌다면 빈 repo가 남을 수 있습니다." if request["kind"] == "hub-upload" else ""))
    except BaseException as exc:
        traceback.print_exc()
        state.update(status="failed", error=str(exc))
    atomic_json(directory / "status.json", state)
