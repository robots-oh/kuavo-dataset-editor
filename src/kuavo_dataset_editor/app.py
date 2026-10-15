"""Local browser API; all expensive work runs in isolated worker processes."""
import io
import json
import threading
from functools import lru_cache
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .core import EditError, compile_selection, label, new_project, split_clip, subtract
from .jobs import JobManager
from .sources import open_source
from .store import Store


def create_app(workspace, allowed_roots=()):
    workspace = Path(workspace).expanduser().resolve()
    store = Store(workspace)
    jobs = JobManager(workspace)
    roots = [workspace, *(Path(p).expanduser().resolve() for p in allowed_roots)]
    app = FastAPI(title="Kuavo Dataset Editor", version="0.1.0")
    # Native file dialogs may grant an explicitly selected directory in-process.
    # There is deliberately no HTTP endpoint that broadens filesystem access.
    app.state.allowed_roots = roots
    app.state.store, app.state.jobs = store, jobs
    assets = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=assets), name="static")
    api_lock = threading.RLock()

    def allowed(path):
        p = Path(path).expanduser().resolve()
        if not any(p.is_relative_to(r) for r in roots):
            raise EditError("접근 허용 경로 밖입니다. 서버 실행 시 --allow-root로 데이터 폴더를 추가하세요.")
        return p

    def project_source(pid, sid):
        project = store.get(pid)
        source = next((s for s in project["sources"] if s["source_id"] == sid), None)
        if source is None:
            raise KeyError(sid)
        allowed(source["path"])
        return source

    @lru_cache(maxsize=8)
    def adapter(path, kind):
        return open_source({"path": path, "kind": kind})

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "다른 origin에서 변경할 수 없습니다."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(EditError)
    async def edit_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "항목이 없습니다."}, status_code=404)

    @app.get("/")
    def index():
        return FileResponse(assets / "index.html")

    @app.get("/api/config")
    def config():
        profile = json.loads((Path(__file__).parent / "profiles/kuavo_5w.json").read_text())
        return {"workspace": str(workspace), "allowed_roots": [str(r) for r in roots], "bag_profile": profile}

    @app.get("/api/projects")
    def list_projects():
        return [{"project_id": p["project_id"], "name": p["name"], "revision": p["revision"]} for p in store.projects()]

    @app.post("/api/projects")
    def create(body: dict):
        return store.save(new_project(body.get("name", "새 프로젝트")), -1)

    @app.get("/api/projects/{pid}")
    def get(pid: str):
        return store.get(pid)

    @app.put("/api/projects/{pid}")
    def save(pid: str, body: dict):
        with api_lock:
            before = store.get(pid)
            after = body["project"]
            if after["project_id"] != pid or after["sources"] != before["sources"]:
                raise EditError("원본 등록 정보는 편집할 수 없습니다. import 기능을 사용하세요.")
            return store.save(after, body["expected_revision"])

    @app.post("/api/projects/{pid}/edit")
    def edit(pid: str, body: dict):
        with api_lock:
            p = store.get(pid)
            if p["revision"] != body["expected_revision"]:
                raise EditError("다른 창에서 수정했습니다. 다시 열어주세요.")
            op = body["operation"]
            if op == "label":
                p = label(p, body["source_id"], int(body["episode_id"]), body["start_frame"], body["end_frame"], body["task"], body.get("replace", False), body.get("new_episode", False))
            elif op == "exclude":
                p["clips"] = subtract(p["clips"], body["source_id"], int(body["episode_id"]), body["start_frame"], body["end_frame"])
            elif op == "split":
                p = split_clip(p, body["clip_id"], body["frame"])
            elif op == "remove_clip":
                p["clips"] = [c for c in p["clips"] if c["id"] != body["clip_id"]]
            elif op == "update_clip":
                c = next(c for c in p["clips"] if c["id"] == body["clip_id"])
                c.update(start_frame=body["start_frame"], end_frame=body["end_frame"], new_episode=bool(body.get("new_episode")))
                text = body.get("task", "").strip()
                if not text:
                    raise EditError("task를 입력하거나 구간 제외로 라벨을 제거하세요.")
                tid = next((k for k, v in p["tasks"].items() if v == text), uuid4().hex)
                p["tasks"][tid] = text
                c["task_id"] = tid
            elif op == "split_assignment":
                if body["split"] not in ("train", "validation", "test", "unspecified"):
                    raise EditError("알 수 없는 split")
                source = project_source(pid, body["source_id"])
                # All imports of the same recording share the assignment.
                for s in p["sources"]:
                    if s["fingerprint"] == source["fingerprint"]:
                        p["source_splits"][s["source_id"]] = body["split"]
            else:
                raise EditError("알 수 없는 편집 명령")
            return store.save(p, body["expected_revision"])

    @app.get("/api/projects/{pid}/plan")
    def plan(pid: str):
        return compile_selection(store.get(pid))

    @app.post("/api/projects/{pid}/imports")
    def start_import(pid: str, body: dict):
        store.get(pid)
        payload = {"source_id": uuid4().hex, "project_id": pid}
        if body.get("demo"):
            return jobs.start("demo", payload)
        path = allowed(body["path"])
        if not path.exists():
            raise EditError("데이터 경로가 없습니다. 경로는 브라우저 PC가 아닌 서버 기준입니다.")
        payload.update(path=str(path), profile=body.get("profile") or config()["bag_profile"])
        return jobs.start("import", payload)

    @app.post("/api/projects/{pid}/attach/{jid}")
    def attach(pid: str, jid: str, body: dict):
        with api_lock:
            job = jobs.get(jid)
            request = json.loads((jobs.root / jid / "request.json").read_text())
            if job["status"] != "completed" or job["kind"] not in ("demo", "import") or request["payload"]["project_id"] != pid:
                raise EditError("이 프로젝트의 완료된 import 작업이 아닙니다.")
            p = store.get(pid)
            source = job["result"]
            allowed(source["path"])
            if any(s["source_id"] == source["source_id"] for s in p["sources"]):
                return p
            p["sources"].append(source)
            return store.save(p, body["expected_revision"])

    @app.get("/api/projects/{pid}/frame/{sid}/{episode}/{frame}")
    def get_frame(pid: str, sid: str, episode: int, frame: int, camera: str):
        source = project_source(pid, sid)
        if camera not in source["cameras"]:
            raise HTTPException(404, "카메라가 없습니다.")
        try:
            row = adapter(source["path"], source["kind"]).row(episode, frame, camera_key=camera)
            img = Image.fromarray(row[camera]).convert("RGB")
            img.thumbnail((960, 540))
            output = io.BytesIO()
            img.save(output, "JPEG", quality=82)
            return Response(output.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/projects/{pid}/signals/{sid}/{episode}")
    def signals(pid: str, sid: str, episode: int):
        source = project_source(pid, sid)
        a = adapter(source["path"], source["kind"])
        n = next(e["num_frames"] for e in source["episodes"] if e["episode_id"] == episode)
        step = max(1, n//400)
        result = []
        for frame in range(0, n, step):
            try:
                row = a.row(episode, frame, cameras=False, validate=False)
                values = {k: [float(x) if np.isfinite(x) else None for x in np.asarray(v).reshape(-1)] for k, v in row.items() if np.asarray(v).dtype.kind in "fibu"}
                for key, mask_key in source.get("validity_masks", {}).items():
                    if not bool(np.asarray(row.get(mask_key, [False])).reshape(-1)[0]):
                        values[key] = [None]*len(values.get(key, []))
                result.append({"frame": frame, "values": values})
            except ValueError:
                result.append({"frame": frame, "values": {}})
        return result

    @app.post("/api/projects/{pid}/exports")
    def start_export(pid: str, body: dict):
        project = store.get(pid)
        target = allowed(body.get("target") or workspace / "exports" / f"{pid[:8]}-r{project['revision']}-{uuid4().hex[:6]}")
        return jobs.start("export", {"project": project, "target": str(target)})

    @app.get("/api/jobs")
    def list_jobs():
        result = []
        for job in jobs.all():
            request = json.loads((jobs.root / job["id"] / "request.json").read_text())
            payload = request["payload"]
            job["project_id"] = payload.get("project_id") or payload.get("project", {}).get("project_id")
            if job.get("result", {}).get("source_id"):
                # Keep polling light; the full catalog is loaded only when attached.
                job["result"] = {k: v for k, v in job["result"].items() if k in ("source_id", "path", "episodes", "report")}
            result.append(job)
        return result

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str):
        return jobs.cancel(jid)

    @app.get("/api/jobs/{jid}/log")
    def job_log(jid: str):
        jobs.get(jid)
        path = jobs.root / jid / "worker.log"
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size-16000))
            return Response(f.read(), media_type="text/plain")

    @app.get("/api/projects/{pid}/snapshot")
    def snapshot(pid: str):
        return Response(json.dumps(store.get(pid), ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="project-{pid[:8]}.json"'})

    return app
