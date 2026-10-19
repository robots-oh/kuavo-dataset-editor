"""Explicit Hub publication, isolated from offline editing and LeRobot internals.

Plans are kept locally; only canonical dataset files and a generated card leave
this computer. No token is accepted as a job parameter or written into a plan.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath

from .core import EditError
from .repositories import repo_identity


SUFFIXES = {
    "meta": {".json", ".jsonl", ".parquet"},
    "data": {".parquet"},
    "videos": {".mp4"},
    "images": {".png", ".jpg", ".jpeg"},
}


def managed_file(name):
    path = PurePosixPath(name)
    return (not path.is_absolute() and len(path.parts) > 1
            and all(not p.startswith(".") for p in path.parts)
            and path.parts[0] in SUFFIXES
            and path.suffix.lower() in SUFFIXES[path.parts[0]])


def local_snapshot(root, *, own_lock=False):
    root = Path(root).expanduser().resolve()
    if root.name.endswith(".partial") or (not own_lock and (root.parent / f".{root.name}.editor-write.lock").exists()):
        raise EditError("저장 중인 dataset은 업로드할 수 없습니다. Export 완료 후 다시 확인하세요.")
    try:
        info = json.loads((root / "meta/info.json").read_text())
        if info["total_frames"] <= 0 or info["total_episodes"] <= 0 or info["fps"] <= 0:
            raise ValueError("empty dataset")
        if info["codebase_version"] != "v3.0":
            raise ValueError("only LeRobot v3.0 publication is supported")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EditError("완료된 LeRobot v3.0 dataset 폴더를 지정하세요.") from exc
    files, omitted = [], []
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        # Do not traverse links outside the dataset or accidentally publish credentials.
        if path.is_symlink():
            raise EditError(f"심볼릭 링크는 업로드하지 않습니다: {name}")
        if not path.is_file():
            continue
        if managed_file(name):
            stat = path.stat()
            files.append({"name": name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        else:
            omitted.append(name)
    if not any(f["name"].startswith("data/") for f in files):
        raise EditError("dataset의 data/*.parquet 파일이 없습니다.")
    if any(f.get("dtype") == "video" for f in info.get("features", {}).values()):
        if not any(f["name"].startswith("videos/") for f in files):
            raise EditError("영상 feature가 있지만 videos/*.mp4 파일이 없습니다.")
    return {"root": str(root), "files": files, "omitted": omitted,
            "bytes": sum(f["size"] for f in files), "frames": info["total_frames"],
            "episodes": info["total_episodes"], "version": info["codebase_version"]}


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def online_api():
    # Called only inside a dedicated Hub worker, before importing the SDK.
    os.environ["HF_HUB_OFFLINE"] = "0"
    try:
        from huggingface_hub import HfApi, get_token
    except ImportError as exc:
        raise EditError("Hub 기능은 기본 설치에 포함됩니다. bash setup.sh로 환경을 갱신하세요.") from exc
    if not get_token():
        raise EditError("먼저 편집기 PC에서 .venv/bin/hf auth login을 실행하세요. UI에는 token을 입력하지 않습니다.")
    return HfApi()


def prepare_upload(root, repo_id, private=True, api=None):
    identity = repo_identity(repo_id)
    if identity["repo_id"] != repo_id or len(repo_id.split("/")) != 2:
        raise EditError("Hub 저장소는 owner/dataset-name 형식으로 입력하세요.")
    snapshot = local_snapshot(root)
    api = api or online_api()
    # A missing/inaccessible private repo is not silently reused: creation below
    # uses exist_ok=False, so lack of read access cannot cause an overwrite.
    from huggingface_hub.errors import RepositoryNotFoundError
    try:
        remote = api.repo_info(repo_id, repo_type="dataset", revision="main")
    except RepositoryNotFoundError:
        remote = None
    head = remote.sha if remote else None
    remote_files = api.list_repo_files(repo_id, repo_type="dataset", revision=head) if remote else []
    names = {f["name"] for f in snapshot["files"]}
    deletes = sorted(name for name in remote_files if managed_file(name) and name not in names)
    plan = {"schema": 1, "repo_id": repo_id, "private": bool(remote.private) if remote else bool(private),
            "exists": remote is not None, "head": head, "snapshot": snapshot, "delete": deletes,
            "create_card": "README.md" not in remote_files,
            "preserved_remote": sorted(n for n in remote_files if not managed_file(n)),
            "url": f"https://huggingface.co/datasets/{repo_id}"}
    plan["digest"] = signature(plan)
    return plan


def publish_upload(plan, *, confirmed=False, sync_existing=False, api=None, progress=lambda *a: None):
    if confirmed is not True:
        raise EditError("업로드 대상·공개 범위·파일 목록 확인이 필요합니다.")
    expected = dict(plan)
    digest = expected.pop("digest")
    if signature(expected) != digest:
        raise EditError("업로드 계획이 변경됐습니다. 미리보기를 다시 생성하세요.")
    if plan["exists"] and sync_existing is not True:
        raise EditError("기존 Hub dataset 동기화를 명시적으로 확인하세요. 원격 append가 아닙니다.")
    root = Path(plan["snapshot"]["root"])
    from .dataset_lock import dataset_lock
    with dataset_lock(root, kind='hub-upload'):
        return _publish_locked(plan, api=api, progress=progress)


def _publish_locked(plan, *, api, progress):
    if local_snapshot(plan["snapshot"]["root"], own_lock=True) != plan["snapshot"]:
        raise EditError("미리보기 후 로컬 dataset이 변경됐습니다. 다시 확인하세요.")
    api = api or online_api()
    from huggingface_hub import CommitOperationAdd, CommitOperationDelete
    repo_id = plan["repo_id"]
    progress(0.05, "Hub 상태 확인")
    if plan["exists"]:
        remote = api.repo_info(repo_id, repo_type="dataset", revision="main")
        if remote.sha != plan["head"] or bool(remote.private) != plan["private"]:
            raise EditError("미리보기 후 Hub dataset이 변경됐습니다. 다시 확인하세요.")
        head = remote.sha
    else:
        api.create_repo(repo_id, repo_type="dataset", private=plan["private"], exist_ok=False)
        head = api.repo_info(repo_id, repo_type="dataset", revision="main").sha
    operations = [CommitOperationAdd(path_in_repo=f["name"],
                  path_or_fileobj=str(Path(plan["snapshot"]["root"]) / f["name"]))
                  for f in plan["snapshot"]["files"]]
    operations.extend(CommitOperationDelete(path_in_repo=name) for name in plan["delete"])
    if plan["create_card"]:
        # Do not assert a license or copy editor provenance / local README paths.
        card = ("---\ntags:\n- lerobot\n- robotics\n---\n\n# " + repo_id.split("/")[1]
                + "\n\nLeRobot dataset exported with Kuavo Dataset Editor.\n\n"
                + "Load this snapshot with `LeRobotDataset(repo_id, revision=\"<commit SHA>\")` "
                + "or `revision=\"main\"`. Review data rights and add a license before redistribution.\n"
                + "\nLocal editor provenance and sparse event sidecars are not included.\n")
        operations.append(CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=card.encode()))
    progress(0.15, "파일 전송·commit 중 (대용량 파일은 로그 확인)")
    # One commit keeps metadata and videos coherent. parent_commit rejects
    # intervening remote writes. Never reset branches or move version tags.
    commit = api.create_commit(repo_id, repo_type="dataset", revision="main", parent_commit=head,
                               operations=operations, commit_message="Publish edited LeRobot dataset snapshot")
    # Do not raise cancellation after a successful remote commit: report success.
    return {"url": plan["url"], "repo_id": repo_id, "commit": commit.oid,
            "commit_url": str(commit.commit_url), "files": len(plan["snapshot"]["files"]),
            "deleted": len(plan["delete"]), "private": plan["private"],
            "loader_revision": commit.oid}
