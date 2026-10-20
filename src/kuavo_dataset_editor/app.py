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

    @app.get("/api/files")
    def browse_files(path: str | None = None, query: str = "", offset: int = 0):
        from .file_browser import browse_local
        return browse_local(roots, path, query[:256], offset)

    @app.get("/api/ssh/hosts")
    def ssh_hosts():
        from .ssh_sources import host_aliases
        settings = workspace / 'ssh-transfer-settings.json'
        saved = json.loads(settings.read_text()) if settings.exists() else {}
        return {'hosts': host_aliases(), 'download_dir': saved.get('download_dir',str(workspace/'downloads')),
                'transport': saved.get('transport','auto')}

    @app.post("/api/ssh/browse")
    def ssh_browse(body: dict):
        from .ssh_sources import browse_remote
        return browse_remote(body.get("host"), body.get("path", "~"), body.get("query", "")[:256], body.get("offset", 0))

    @app.post("/api/projects/{pid}/ssh-imports")
    def start_ssh_import(pid: str, body: dict):
        from .ssh_sources import validate_host
        store.get(pid)
        host = validate_host(body.get("host"))
        paths = body.get("paths")
        if not isinstance(paths, list) or not 1 <= len(paths) <= 100 or any(not isinstance(p, str) or not p.startswith("/") or not p.lower().endswith(".bag") for p in paths):
            raise EditError("원격 .bag 파일을 1–100개 선택하세요.")
        settings = ssh_hosts()
        choice = body.get('download_dir',settings['download_dir'])
        download_dir = allowed(choice or workspace/'downloads')
        custom_download_dir = download_dir != workspace/'downloads'
        if custom_download_dir and not download_dir.is_dir():
            raise EditError('다운로드 폴더가 없습니다. SSD를 연결하고 기존 폴더를 선택하세요.')
        transport = body.get('transport', settings['transport'])
        if transport not in ('auto','rsync','stream'):
            raise EditError('지원하지 않는 SSH 전송 방식입니다.')
        from .sources import write_json
        temporary = workspace / ('.ssh-transfer-'+uuid4().hex+'.tmp')
        write_json(temporary, {'download_dir':str(download_dir),'transport':transport})
        temporary.replace(workspace / 'ssh-transfer-settings.json')
        return jobs.start("ssh-import", {"host": host, "project_id": pid,
            "items": [{"source_id": uuid4().hex, "path": p} for p in dict.fromkeys(paths)],
            'download_dir':str(download_dir),'transport':transport,
            'download_device':(download_dir if download_dir.is_dir() else workspace).stat().st_dev,
            'download_root_required':custom_download_dir,
            "profile": body.get("profile") or config()["bag_profile"]})

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
        paths = body.get("paths", [body.get("path")])
        if not isinstance(paths, list) or not 1 <= len(paths) <= 100 or any(not isinstance(p, str) or not p.strip() for p in paths):
            raise EditError("파일 또는 dataset 폴더를 1–100개 선택하세요.")
        paths = list(dict.fromkeys(allowed(p) for p in paths))
        for path in paths:
            if not (path.is_file() and path.suffix.lower() == ".bag" or path.is_dir() and (path / "meta/info.json").is_file()):
                raise EditError(".bag 파일 또는 LeRobot 루트 폴더를 선택하세요. 경로는 편집기 서버 PC 기준입니다.")
        if len(paths) > 1:
            return jobs.start("import-batch", {"project_id": pid,
                "items": [{"source_id": uuid4().hex, "path": str(p)} for p in paths],
                "profile": body.get("profile") or config()["bag_profile"]})
        path = paths[0]
        payload.update(path=str(path), profile=body.get("profile") or config()["bag_profile"])
        return jobs.start("import", payload)

    @app.post("/api/projects/{pid}/attach/{jid}")
    def attach(pid: str, jid: str, body: dict):
        with api_lock:
            job = jobs.get(jid)
            request = json.loads((jobs.root / jid / "request.json").read_text())
            if job["status"] not in ("completed", "cancelled", "failed") or job["kind"] not in ("demo", "import", "import-batch", "ssh-import") or request["payload"]["project_id"] != pid:
                raise EditError("이 프로젝트의 완료된 import 작업이 아닙니다.")
            p = store.get(pid)
            result = job.get("result", {})
            sources = result.get("sources", [result] if result.get("source_id") else [])
            if not sources:
                raise EditError("아직 프로젝트에 추가할 수 있는 완료된 원본이 없습니다.")
            changed = False
            for source in sources:
                allowed(source["path"])
                if any(s["source_id"] == source["source_id"] for s in p["sources"]):
                    continue
                p["sources"].append(source)
                if source["kind"] == "lerobot":
                    from .repositories import with_existing_labels
                    p = with_existing_labels(p, source)
                changed = True
            if not changed:
                return p
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
                from .masking import visible_values
                values = visible_values(row,a.features,source.get('validity_masks'))
                result.append({"frame": frame, "values": values})
            except ValueError:
                result.append({"frame": frame, "values": {}})
        return result

    def export_request(pid, body):
        from .repositories import prepare_export, repo_identity
        project = store.get(pid)
        mode = body.get("mode", "new")
        if mode == "new":
            identity = repo_identity(body.get("repo_name"))
            target = allowed(body.get("target") or workspace / "exports" / identity["slug"])
        else:
            if not body.get("target"):
                raise EditError("기존 dataset 폴더를 지정하세요.")
            target = allowed(body["target"])
            allowed(target.parent)  # Backup/staging directories are siblings, not children.
        options = {"mode": mode, "repo_name": body.get("repo_name"),
                   'schema':body.get('schema','legacy'),
                   "confirm_existing": body.get("confirm_existing") is True}
        if body.get('rack_calibration') is not None:
            if not isinstance(body['rack_calibration'],dict):
                raise EditError('rack_calibration은 rack_tags 배열을 포함한 JSON 객체여야 합니다.')
            options['rack_calibration'] = body['rack_calibration']
        if body.get('quality') is not None:
            approval = body['quality']
            if not isinstance(approval, dict):
                raise EditError('누락 구간 선택 형식이 잘못됐습니다.')
            options['quality'] = {key: approval.get(key) for key in ('token', 'decisions')}
        plan = prepare_export(project, target, options)
        return project, target, options, plan

    @app.post("/api/projects/{pid}/export-plan")
    def export_plan(pid: str, body: dict):
        return export_request(pid, body)[3]

    @app.post("/api/projects/{pid}/exports")
    def start_export(pid: str, body: dict):
        project, target, options, plan = export_request(pid, body)
        if body.get("expected_revision", project["revision"]) != project["revision"]:
            raise EditError("미리보기 후 프로젝트가 변경됐습니다. 다시 확인하세요.")
        if body.get("expected_baseline", plan["baseline"]) != plan["baseline"]:
            raise EditError("미리보기 후 기존 dataset이 변경됐습니다. 다시 확인하세요.")
        if options["mode"] != "new" and not options["confirm_existing"]:
            raise EditError("기존 dataset 추가/수정 확인에 체크하세요.")
        if not plan['quality']['ready']:
            raise EditError('먼저 누락 점검에서 각 구간의 보정/제외 여부를 선택하세요. annotation은 변경되지 않았습니다.')
        return jobs.start("export", {"project": project, "target": str(target), "options": options})

    @app.get("/api/jobs")
    def list_jobs():
        result = []
        for job in jobs.all():
            request = json.loads((jobs.root / job["id"] / "request.json").read_text())
            payload = request["payload"]
            completed = {s["source_id"] for s in job.get("result", {}).get("sources", [])}
            job["retryable"] = (job["kind"] in ("import-batch", "ssh-import")
                and job["status"] in ("completed", "cancelled", "failed")
                and any(item["source_id"] not in completed for item in payload.get("items", [])))
            job["project_id"] = payload.get("project_id") or payload.get("project", {}).get("project_id")
            if job.get("result", {}).get("source_id"):
                # Keep polling light; the full catalog is loaded only when attached.
                job["result"] = {k: v for k, v in job["result"].items() if k in ("source_id", "path", "episodes", "report")}
            elif "sources" in job.get("result", {}):
                job["result"]["sources"] = [{k: v for k, v in s.items() if k in ("source_id", "path", "episodes", "report")} for s in job["result"]["sources"]]
            result.append(job)
        return result

    @app.post("/api/jobs/{jid}/retry-import")
    def retry_import(jid: str):
        job = jobs.get(jid)
        if job["kind"] not in ("import-batch", "ssh-import") or job["status"] not in ("completed", "cancelled", "failed"):
            raise EditError("중단/실패한 여러 파일 가져오기 작업만 이어갈 수 있습니다.")
        request = json.loads((jobs.root / jid / "request.json").read_text())
        payload = request["payload"]
        store.get(payload["project_id"])
        completed = {s["source_id"] for s in job.get("result", {}).get("sources", [])}
        items = [item for item in payload["items"] if item["source_id"] not in completed]
        if not items:
            raise EditError("모든 파일의 가져오기가 완료됐습니다.")
        if job["kind"] == "ssh-import":
            from .ssh_sources import validate_host
            validate_host(payload["host"])
            allowed(payload.get('download_dir') or workspace/'downloads')
        else:
            for item in items:
                allowed(item["path"])
        payload["items"] = [{"path": item["path"], "source_id": uuid4().hex} for item in items]
        return jobs.start(job["kind"], payload)

    @app.post('/api/jobs/{jid}/resume-export')
    def resume_export(jid: str):
        with api_lock:
            jobs.get(jid)
            request = json.loads((jobs.root / jid / 'request.json').read_text())
            payload = request['payload']
            if request['kind'] != 'export':
                raise EditError('Export 작업만 이어갈 수 있습니다.')
            allowed(payload['target'])
            allowed(Path(payload['target']).parent)
            for source in payload['project']['sources']:
                allowed(source['path'])
                allowed(source.get('original_path', source['path']))
            return jobs.resume_export(jid)

    @app.post("/api/hub/plans")
    def hub_plan(body: dict):
        from .hub import local_snapshot
        from .repositories import repo_identity
        path = allowed(body["path"])
        allowed(path.parent)  # Shared sibling lock with local dataset publication.
        local_snapshot(path)
        repo_id = body.get("repo_id", "").strip()
        if len(repo_id.split("/")) != 2:
            raise EditError("Hub repo는 owner/dataset-name 형식입니다.")
        repo_identity(repo_id)
        # Only these fields are persisted; never accept token / arbitrary payload.
        return jobs.start("hub-plan", {"path": str(path), "repo_id": repo_id,
                                     "private": body.get("private", True) is not False})

    @app.post("/api/hub/plans/{jid}/upload")
    def hub_upload(jid: str, body: dict):
        with api_lock:
            job = jobs.get(jid)
            if job["kind"] != "hub-plan" or job["status"] != "completed":
                raise EditError("완료된 Hub 미리보기를 선택하세요.")
            plan = job["result"]
            allowed(plan["snapshot"]["root"])
            if body.get("confirmed") is not True or body.get("digest") != plan["digest"]:
                raise EditError("대상과 공개 범위를 확인한 뒤 업로드하세요.")
            if plan["exists"] and body.get("sync_existing") is not True:
                raise EditError("기존 Hub dataset 동기화를 확인하세요.")
            return jobs.start("hub-upload", {"plan": plan, "confirmed": True,
                                           "sync_existing": body.get("sync_existing") is True})

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
