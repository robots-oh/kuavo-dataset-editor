"""Isolated worker processes with durable request, status, log and cancellation."""
import json
import fcntl
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4

from .sources import LeRobotSource, demo_source, write_json
from .core import EditError


class Cancelled(Exception):
    pass


def import_one(path, directory, source_id, profile, progress):
    path = Path(path).expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".bag":
        from .bag import import_bag
        return import_bag(path, directory, source_id, profile, progress)
    return LeRobotSource(path).descriptor(source_id)


def import_many(payload, directory, progress, checkpoint):
    """Local imports are sequential; SSH downloads run one file ahead."""
    if payload.get('host'):
        return import_ssh_many(payload, directory, progress, checkpoint)
    result = {"sources": [], "failures": [], "downloads": []}
    for index, item in enumerate(payload["items"]):
        count = len(payload["items"])
        progress(index / count, f"{index+1}/{count} · {Path(item['path']).name}")
        try:
            path = item["path"]
            source = import_one(path, directory / item["source_id"], item["source_id"], payload["profile"],
                lambda f, m: progress((index+f)/count, f"{index+1}/{count} · {m}"))
            result["sources"].append(source)
        except Cancelled:
            raise
        except Exception as exc:
            traceback.print_exc()
            result["failures"].append({"path": item["path"], "error": str(exc)})
        checkpoint(result)
    return result


def import_ssh_many(payload, directory, progress, checkpoint):
    """One transfer ahead of one import; bounded disk/CPU concurrency."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    from .ssh_sources import download_bag
    items = payload['items']
    count = len(items)
    result = {'sources': [], 'failures': [], 'downloads': []}
    cache = Path(payload.get('download_dir') or Path(directory).parent.parent / 'downloads')
    stop = threading.Event()
    lock = threading.RLock()
    fractions = {'download': [0.0]*count, 'import': [0.0]*count}
    messages = {'download': '대기', 'import': '대기'}

    def update(phase, index, fraction, message):
        if stop.is_set():
            raise Cancelled()
        with lock:
            fractions[phase][index] = max(fractions[phase][index], fraction)
            messages[phase] = f'{index+1}/{count} · {message}'
            overall = (0.65*sum(fractions['download']) + 0.35*sum(fractions['import']))/count
            progress(overall, '다운로드 '+messages['download']+' | 가져오기 '+messages['import'])

    def receive(index):
        device = payload.get('download_device')
        if device is not None:
            anchor = cache if cache.is_dir() else cache.parent
            if ((payload.get('download_root_required') and not cache.is_dir())
                    or not anchor.is_dir() or anchor.stat().st_dev != device):
                raise EditError('선택한 SSD/다운로드 폴더가 연결되어 있지 않습니다. 내부 디스크에 대신 받지 않습니다.')
        return download_bag(payload['host'], items[index]['path'], cache,
                            lambda f,m: update('download',index,f,m),
                            transport=payload.get('transport','auto'))

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bag-transfer')
    future = executor.submit(receive, 0)
    try:
        for index, item in enumerate(items):
            download, failure = None, None
            while True:
                try:
                    download = future.result(timeout=0.2)
                    break
                except FutureTimeout:
                    if future.done():
                        exception = future.exception()
                        if exception is None:
                            download = future.result()
                        else:
                            failure = str(exception)
                        break
                    with lock:
                        progress((0.65*sum(fractions['download'])+0.35*sum(fractions['import']))/count,
                                 '다운로드 '+messages['download']+' | 가져오기 '+messages['import'])
                except Cancelled:
                    raise
                except Exception as exc:
                    traceback.print_exc()
                    failure = str(exc)
                    break
            if index+1 < count:
                future = executor.submit(receive, index+1)
            if failure is None:
                result['downloads'].append(download)
                try:
                    update('import', index, 0, Path(item['path']).name)
                    source = import_one(download['path'], Path(directory)/item['source_id'],
                                        item['source_id'], payload['profile'],
                                        lambda f,m: update('import',index,f,m))
                    result['sources'].append(source)
                except Cancelled:
                    raise
                except Exception as exc:
                    traceback.print_exc()
                    failure = str(exc)
            if failure is not None:
                result['failures'].append({'path': item['path'], 'error': failure})
            with lock:
                update('download',index,1,'완료' if download else '실패')
                update('import',index,1,'완료' if failure is None else '실패')
                checkpoint(result)
    finally:
        stop.set()
        executor.shutdown(wait=True, cancel_futures=True)
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
        self._spawn(jid, directory)
        return self.get(jid)

    def _spawn(self, jid, directory):
        with (directory / 'worker.log').open('a') as log:
            process = subprocess.Popen([sys.executable, '-m', 'kuavo_dataset_editor', '_worker', str(directory)],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        write_json(directory / "process.json", {"pid": process.pid})
        self.processes[jid] = process

    def resume_export(self, jid):
        from .checkpoints import checkpoint_state
        self.get(jid)  # Validate identifier before accessing a path.
        directory = self.root / jid
        with (directory / 'launch.lock').open('a+') as guard:
            try:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise EditError('이 작업의 재개 요청을 이미 처리 중입니다.') from exc
            status = self.get(jid)
            if status['kind'] != 'export' or status['status'] not in ('failed', 'cancelled'):
                raise EditError('중단/실패한 Export만 이어갈 수 있습니다. 실행 중인 작업은 중복 실행하지 않습니다.')
            if checkpoint_state(directory) is None:
                raise EditError('이전 버전 작업에는 episode checkpoint가 없습니다. annotation은 유지되며 새 Export가 필요합니다.')
            if (directory / 'publication.json').exists():
                raise EditError('이미 게시된 결과가 있습니다. 완료 dataset과 로그를 먼저 확인하세요.')
            if (directory / 'cancel').exists():
                (directory / 'cancel').rename(directory / ('cancel-' + uuid4().hex))
            status.update(status='queued', message='완료 episode부터 이어가기 대기', resumed=time.time())
            status.pop('error', None)
            status.pop('pid', None)
            atomic_json(directory / 'status.json', status)
            self.processes.pop(jid, None)
            self._spawn(jid, directory)
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
        if status['kind'] == 'export':
            from .checkpoints import checkpoint_state
            checkpoint = checkpoint_state(directory)
            status['checkpoint'] = ({key: checkpoint[key] for key in ('completed_episodes', 'completed_frames')}
                                    if checkpoint else None)
            status['resumable'] = bool(checkpoint and status['status'] in ('failed', 'cancelled')
                                       and not (directory / 'publication.json').exists())
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
        elif request['kind']=='export-preview':
            from .data_preview import build_preview
            result=build_preview(payload['project'],payload['target'],payload.get('options') or {},directory,progress)
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
                     message={"export": "export 완료", 'export-preview':'Export 데이터 미리보기 준비됨 (변환 파일 생성 전)', "hub-plan": "업로드 미리보기 준비됨 — 아직 전송하지 않았습니다.",
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
