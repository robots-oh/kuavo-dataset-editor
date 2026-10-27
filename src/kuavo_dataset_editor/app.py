"""Local browser API; all expensive work runs in isolated worker processes."""
import io
import json
import socket
import threading
from copy import deepcopy
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
from .profile_pick import for_path, load_all
from .sources import open_source,file_stamp
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
        source = store.source(pid, sid)
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
        # Revalidate the UI and its imported modules after local source updates.
        # Camera frames keep their independent cache policy.
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
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
        return {"workspace": str(workspace), "allowed_roots": [str(r) for r in roots], "bag_profile": profile,
                "bag_profiles": load_all(),
                'annotation_tracks':['step','goal'], 'revision_history_restore':True,
                'source_completion':True, 'source_not_required':True, 'source_removal':True,
                'source_scoped_export':True, 'shared_project_annotations':True}

    @app.get("/api/projects")
    def list_projects():
        return [{"project_id": p["project_id"], "name": p["name"], "revision": p["revision"]} for p in store.projects()]

    @app.get("/api/files")
    def browse_files(path: str | None = None, query: str = "", offset: int = 0):
        from .file_browser import browse_local
        return browse_local(roots, path, query[:256], offset)

    @app.get('/api/folders')
    def browse_folders(path: str | None = None, query: str = '', offset: int = 0):
        from .file_browser import browse_local
        return browse_local(roots,path,query[:256],offset,directories_only=True)

    def transfer_settings():
        settings = workspace / 'ssh-transfer-settings.json'
        saved = json.loads(settings.read_text()) if settings.exists() else {}
        return {'download_dir':saved.get('download_dir',str(workspace/'downloads')),
                'default_download_dir':str(workspace/'downloads'),
                'server_hostname':socket.gethostname(), 'transport':saved.get('transport','auto')}

    def validate_transfer_settings(body):
        settings = transfer_settings()
        choice = body.get('download_dir',settings['download_dir'])
        if not isinstance(choice,str):
            raise EditError('다운로드 폴더는 경로 문자열로 지정하세요.')
        download_dir = allowed(choice.strip() or workspace/'downloads')
        custom = download_dir != workspace/'downloads'
        if custom and not download_dir.is_dir():
            raise EditError('다운로드 폴더가 없습니다. SSD를 연결하고 기존 폴더를 선택하세요.')
        if download_dir.exists() and not download_dir.is_dir():
            raise EditError('다운로드 경로는 파일이 아닌 폴더여야 합니다.')
        transport = body.get('transport',settings['transport'])
        if transport not in ('auto','rsync','stream'):
            raise EditError('지원하지 않는 SSH 전송 방식입니다.')
        return download_dir,custom,transport

    def save_transfer_settings(download_dir,transport):
        from .sources import write_json
        temporary = workspace / ('.ssh-transfer-'+uuid4().hex+'.tmp')
        write_json(temporary, {'download_dir':str(download_dir),'transport':transport})
        temporary.replace(workspace / 'ssh-transfer-settings.json')

    @app.post('/api/ssh/settings')
    def set_ssh_settings(body: dict):
        # Persist a choice without starting a download or changing existing jobs.
        with api_lock:
            download_dir,_,transport = validate_transfer_settings(body)
            save_transfer_settings(download_dir,transport)
            return transfer_settings()

    @app.get("/api/ssh/hosts")
    def ssh_hosts():
        from .ssh_sources import host_aliases
        return {'hosts':host_aliases(), **transfer_settings()}

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
        with api_lock:
            download_dir,custom_download_dir,transport = validate_transfer_settings(body)
            save_transfer_settings(download_dir,transport)
        return jobs.start("ssh-import", {"host": host, "project_id": pid,
            "items": [{"source_id": uuid4().hex, "path": p, "profile": for_path(p, body.get("profile"))} for p in dict.fromkeys(paths)],
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

    @app.get('/api/projects/{pid}/revision')
    def project_revision(pid: str):
        return {'revision': store.revision(pid)}

    @app.put("/api/projects/{pid}")
    def save(pid: str, body: dict):
        with api_lock:
            before = store.get(pid)
            if before['revision'] != body['expected_revision']:
                raise EditError('다른 창에서 변경했습니다. 프로젝트를 다시 열어주세요.')
            if 'restore_revision' in body:
                revision = body['restore_revision']
                if type(revision) is not int or not 0 <= revision <= before['revision']:
                    raise EditError('유효한 이전 편집 기록을 선택하세요.')
                previous = store.get(pid, revision)
                source_ids = {s['source_id'] for s in previous['sources']}
                if not source_ids <= {s['source_id'] for s in before['sources']}:
                    raise EditError('이전 편집 기록의 원본이 현재 프로젝트에 없습니다.')
                # Restore edits from server JSON, never browser-rounded bag timestamps.
                # Sources and annotations added by a later import remain registered.
                after = {**before, 'tasks':dict(previous['tasks'])}
                for key in ('clips','goals','exclusions'):
                    added = [c for c in before.get(key,[]) if c['source_id'] not in source_ids]
                    after[key] = previous.get(key,[]) + added
                    for clip in added:
                        if 'task_id' in clip:
                            after['tasks'][clip['task_id']] = before['tasks'][clip['task_id']]
                after['source_splits'] = {**before.get('source_splits',{}),
                    **{sid:previous.get('source_splits',{}).get(sid,'unspecified') for sid in source_ids}}
                after['source_completed'] = {**before.get('source_completed',{}),
                    **{sid:previous.get('source_completed',{}).get(sid,False) for sid in source_ids}}
                after['source_not_required'] = {**before.get('source_not_required',{}),
                    **{sid:previous.get('source_not_required',{}).get(sid,False) for sid in source_ids}}
                after['source_removed'] = {**before.get('source_removed',{}),
                    **{sid:previous.get('source_removed',{}).get(sid,False) for sid in source_ids}}
            else:
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
            annotation_keys = ('clips','goals','exclusions')
            before_annotations = {key:deepcopy(p.get(key,[])) for key in annotation_keys}
            op = body["operation"]
            if op in ('label','split','remove_clip','update_clip','remove_annotation'):
                from .annotations import edit_annotation
                p=edit_annotation(p,body)
            elif op == "exclude":
                p["clips"] = subtract(p["clips"], body["source_id"], int(body["episode_id"]), body["start_frame"], body["end_frame"])
                p.setdefault('exclusions',[]).append({key:body[key] for key in ('source_id','episode_id','start_frame','end_frame')} | {'id':uuid4().hex})
            elif op=='restore_exclusion':
                p['exclusions']=[item for item in p.get('exclusions',[]) if item['id']!=body['exclusion_id']]
            elif op=='source_removal':
                if not any(s['source_id']==body.get('source_id') for s in p['sources']):
                    raise EditError('현재 프로젝트에 없는 원본입니다.')
                if type(body.get('removed')) is not bool:
                    raise EditError('원본 제거 여부는 true/false로 지정하세요.')
                # Project-local soft removal. Keep descriptors/labels in place:
                # shared peers and revision history still reference the cache.
                p.setdefault('source_removed',{})[body['source_id']] = body['removed']
            elif op=='source_completion':
                if not any(s['source_id']==body['source_id'] for s in p['sources']):
                    raise EditError('현재 프로젝트에 없는 원본입니다.')
                if type(body.get('completed')) is not bool:
                    raise EditError('라벨링 완료 여부는 true/false로 지정하세요.')
                p.setdefault('source_completed',{})[body['source_id']] = body['completed']
                if body['completed']:
                    p.setdefault('source_not_required',{})[body['source_id']] = False
            elif op=='source_not_required':
                if not any(s['source_id']==body['source_id'] for s in p['sources']):
                    raise EditError('현재 프로젝트에 없는 원본입니다.')
                if type(body.get('not_required')) is not bool:
                    raise EditError('라벨링 불필요 여부는 true/false로 지정하세요.')
                p.setdefault('source_not_required',{})[body['source_id']] = body['not_required']
                if body['not_required']:
                    p.setdefault('source_completed',{})[body['source_id']] = False
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
            for sid,completed in p.get('source_completed',{}).items():
                if completed and any(
                    [c for c in before_annotations[key] if c['source_id']==sid]
                    != [c for c in p.get(key,[]) if c['source_id']==sid]
                    for key in annotation_keys):
                    p['source_completed'][sid] = False
            return store.save(p, body["expected_revision"])

    @app.get("/api/projects/{pid}/plan")
    def plan(pid: str):
        from .export_scope import exclude_not_required
        return compile_selection(exclude_not_required(store.get(pid)))

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
                "items": [{"source_id": uuid4().hex, "path": str(p), "profile": for_path(p, body.get("profile"))} for p in paths],
                "profile": body.get("profile") or config()["bag_profile"]})
        path = paths[0]
        payload.update(path=str(path), profile=for_path(path, body.get("profile")))
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

    @app.get('/api/projects/{pid}/activity/{sid}/{episode}')
    def activity(pid: str, sid: str, episode: int):
        from .activity import build_activity
        return build_activity(project_source(pid, sid), episode)

    def raw_path(pid,sid):
        source=project_source(pid,sid)
        path=allowed(source.get('original_path',source['path']))
        if path.suffix.lower()!='.bag' or not path.is_file():
            raise EditError('이 원본에 연결된 rosbag이 없습니다. bag에서 가져온 원본을 선택하세요.')
        return path

    @app.get('/api/projects/{pid}/torso/{sid}/{episode}/{frame}')
    def torso(pid: str, sid: str, episode: int, frame: int):
        from .activity import torso_window
        return torso_window(project_source(pid, sid), episode, frame, raw_path(pid, sid))

    @app.get('/api/projects/{pid}/gripper/{sid}/{episode}/{frame}')
    def gripper(pid: str, sid: str, episode: int, frame: int):
        from .activity import gripper_window
        return gripper_window(project_source(pid, sid), episode, frame, raw_path(pid, sid))

    @lru_cache(maxsize=8)
    def raw_inventory(path,stamp):
        from .raw_inspection import inventory
        return inventory(path)

    @app.get('/api/projects/{pid}/raw/{sid}')
    def inspect_raw(pid: str,sid: str):
        path=raw_path(pid,sid)
        return raw_inventory(str(path),file_stamp(path))

    @app.get('/api/projects/{pid}/raw/{sid}/messages')
    def raw_messages(pid: str,sid: str,topic: str,time_s: float=0,field: str='',limit: int=3,window_s: float=2):
        from .raw_inspection import samples
        return samples(raw_path(pid,sid),topic,time_s,limit,field,window_s)

    def export_request(pid, body,preview=False):
        from .repositories import prepare_export, repo_identity
        from .export_scope import normalize_export_options, select_sources
        project = store.get(pid)
        mode = body.get("mode", "new")
        scope = normalize_export_options(body)
        project = select_sources(project, scope.get('source_ids'))
        if body.get('join_across_gaps') is not None:
            # Drop short unlabeled gaps inside one recording without starting a new episode (core.compile_selection).
            if not isinstance(body['join_across_gaps'], bool):
                raise EditError('join_across_gaps는 true/false 여야 합니다.')
            gap_s = body.get('join_max_gap_s', 10.0)
            if isinstance(gap_s, bool) or not isinstance(gap_s, (int, float)) or not 0 < gap_s <= 120:
                raise EditError('join_max_gap_s는 0 초보다 크고 120 초 이하인 숫자여야 합니다.')
            project = {**project, 'export': {**(project.get('export') or {}),
                                             'join_across_gaps': body['join_across_gaps'], 'join_max_gap_s': float(gap_s)}}
        if mode == "new":
            identity = repo_identity(body.get("repo_name") or ('preview-'+pid if preview else ''))
            target = allowed(body.get("target") or (workspace/'.preview-plans'/pid if preview else workspace / "exports" / identity["slug"]))
        else:
            if not body.get("target"):
                raise EditError("기존 dataset 폴더를 지정하세요.")
            target = allowed(body["target"])
            allowed(target.parent)  # Backup/staging directories are siblings, not children.
        options = {"mode": mode, "repo_name": body.get("repo_name"),
                   'source_ids':scope.get('source_ids'),
                   'schema':body.get('schema','legacy'),
                   'annotation_track':body.get('annotation_track','step'),
                   'join_across_gaps': (project.get('export') or {}).get('join_across_gaps') is True,
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
        used = {run['source_id'] for run in plan['runs']}
        plan['source_names'] = [Path(s.get('original_path',s['path'])).name
                                for s in project['sources'] if s['source_id'] in used]
        return project, target, options, plan

    def preview_snapshot(pid,jid):
        job=jobs.get(jid)
        if job['kind']!='export-preview' or job['status']!='completed':
            raise EditError('계산이 완료된 Export 미리보기를 선택하세요.')
        root=jobs.root/jid
        request=json.loads((root/'request.json').read_text())['payload']
        if request['project']['project_id']!=pid: raise EditError('다른 프로젝트의 미리보기입니다.')
        saved=json.loads((root/'preview.json').read_text())
        for source in saved['project']['sources']:
            allowed(source['path'])
            if source.get('original_path'): allowed(source['original_path'])
        return saved,request,root

    @app.post('/api/projects/{pid}/preview-plan')
    def preview_plan(pid: str,body: dict):
        return export_request(pid,body,preview=True)[3]

    @app.post('/api/projects/{pid}/previews')
    def start_preview(pid: str,body: dict):
        project,target,options,plan=export_request(pid,body,preview=True)
        if body.get('expected_revision',project['revision'])!=project['revision']:
            raise EditError('미리보기 준비 후 편집 내용이 바뀌었습니다. 다시 계산하세요.')
        if not plan['quality']['ready']: raise EditError('기존 누락 구간의 보정/제외를 먼저 선택하세요.')
        for source in project['sources']:
            allowed(source['path']); allowed(source.get('original_path',source['path']))
        return jobs.start('export-preview',{'project':project,'target':str(target),'options':options})

    @app.get('/api/projects/{pid}/previews/{jid}')
    def get_preview(pid: str,jid: str):
        saved,request,_=preview_snapshot(pid,jid)
        return {**saved['public'],'preview_id':jid,
                'stale_revision':store.get(pid)['revision']!=saved['public']['project_revision'],
                'quality':request['options'].get('quality')}

    @app.get('/api/projects/{pid}/previews/{jid}/row')
    def get_preview_row(pid: str,jid: str,sid: str,episode: int,frame: int):
        from .data_preview import preview_row
        saved,_,_=preview_snapshot(pid,jid)
        try: return preview_row(saved,sid,episode,frame)
        except (ValueError,IndexError) as exc: raise EditError(str(exc)) from exc

    @app.get('/api/projects/{pid}/previews/{jid}/series')
    def get_preview_series(pid: str,jid: str,sid: str,episode: int,feature: str,index: int):
        from .data_preview import preview_series
        saved,_,_=preview_snapshot(pid,jid)
        return preview_series(saved,sid,episode,feature,index)

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
        if body.get('preview_id'):
            from .data_preview import preview_key
            saved,_,root=preview_snapshot(pid,body['preview_id'])
            if preview_key(project,target,options)!=saved['key']:
                raise EditError('편집 내용·원본·출력 구성이 미리보기와 달라졌습니다. 다시 미리보거나 미리보기 연결을 해제하세요.')
            options['_preview_cache_root']=str(root)
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
