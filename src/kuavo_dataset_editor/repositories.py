"""Local dataset identity, preflight and recoverable directory publication."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import time
from uuid import uuid4

from .core import EditError, compile_selection
from .sources import LeRobotSource, fingerprint, write_json


def task_names(root):
    import pyarrow.parquet as pq
    root = Path(root)
    if (root / "meta/tasks.parquet").is_file():
        rows = pq.read_table(root / "meta/tasks.parquet").to_pylist()
    elif (root / "meta/tasks.jsonl").is_file():
        rows = [json.loads(line) for line in (root / "meta/tasks.jsonl").read_text().splitlines() if line.strip()]
    else:
        raise EditError("기존 dataset의 task metadata가 없습니다.")
    result = {}
    for row in rows:
        text = row.get("task", row.get("__index_level_0__"))
        if not isinstance(text, str) or not text.strip():
            raise EditError("기존 task metadata 형식을 해석하지 못했습니다.")
        result[int(row["task_index"])] = text
    return result


def with_existing_labels(project, descriptor):
    """Import original per-frame labels; editing one clip must not drop untouched ones."""
    from .annotations import restore_exported_annotations
    restored=restore_exported_annotations(project,descriptor)
    if restored is not None:
        compile_selection(restored)
        return restored
    result = deepcopy(project)
    source = LeRobotSource(descriptor["path"])
    names = task_names(source.root)
    by_text = {text: key for key, text in result["tasks"].items()}
    for episode in source.episodes:
        previous = None
        for row in source.rows(episode["episode_index"]):
            text = names.get(int(row["task_index"]))
            if text is None:
                raise EditError("기존 frame이 알 수 없는 task_index를 참조합니다.")
            task_id = by_text.setdefault(text, uuid4().hex)
            result["tasks"][task_id] = text
            frame = int(row["frame_index"])
            if previous is not None and previous["task_id"] == task_id and previous["end_frame"] == frame:
                previous["end_frame"] = frame + 1
            else:
                previous = {"id": uuid4().hex, "source_id": descriptor["source_id"],
                            "episode_id": episode["episode_index"], "start_frame": frame,
                            "end_frame": frame + 1, "task_id": task_id, "new_episode": False}
                result["clips"].append(previous)
    compile_selection(result)
    return result


def existing_provenance(staging, base):
    import pyarrow.parquet as pq
    mapping = Path(staging) / "editor/frame_mapping.parquet"
    if mapping.is_file():
        rows = pq.read_table(mapping).to_pylist()
        if len(rows) != base["existing_frames"]:
            raise EditError("기존 frame_mapping과 dataset frame 수가 다릅니다.")
        return rows
    source = LeRobotSource(staging)
    names = task_names(staging)
    rows = []
    for episode in source.episodes:
        for row in source.rows(episode["episode_index"]):
            rows.append({"index": int(row["index"]), "episode_index": int(row["episode_index"]),
                         "frame_index": int(row["frame_index"]), "source_id": "existing-dataset",
                         "source_episode": int(row["episode_index"]), "source_frame": int(row["frame_index"]),
                         "source_time_s": float(row["timestamp"]), "task": names[int(row["task_index"])],
                         "split": "unspecified", "clip_id": None})
    return rows


def preserve_editor_history(target, staging, job_id):
    source = Path(target) / "editor"
    if not source.is_dir():
        return
    destination = Path(staging) / "editor"
    from .checkpoints import link_or_copy
    history = destination / "history"
    if not history.exists() and (source / "history").is_dir():
        shutil.copytree(source / "history", history, copy_function=link_or_copy)
    snapshot = history / job_id
    snapshot.mkdir(parents=True)
    # The published target is only ever replaced by rename, never rewritten, so its files can be linked.
    for item in source.iterdir():
        if item.name == "history":
            continue
        if item.is_dir():
            shutil.copytree(item, snapshot / item.name, copy_function=link_or_copy)
        else:
            link_or_copy(item, snapshot / item.name)


def repo_identity(name):
    name = (name or "").strip()
    parts = name.split("/")
    if len(parts) not in (1, 2) or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", p)
        or ".." in p or "--" in p or p.endswith((".", ".git")) for p in parts
    ):
        raise EditError("dataset 이름은 영문/숫자와 _, -, .을 사용하세요. 예: rack-box-v1 또는 team/rack-box-v1")
    return {"name": name, "repo_id": name if len(parts) == 2 else "local/" + name, "slug": parts[-1]}


def existing_identity(target):
    path = Path(target) / "editor/dataset.json"
    if path.exists():
        return repo_identity(json.loads(path.read_text())["name"])
    return repo_identity(Path(target).name)


def selected_project(project, target, mode,annotation_track='step',source_ids=None):
    """source_ids: export only these recordings of the project (None = all). Lets one project collect many
    recordings while 'append' adds just the new ones instead of re-adding everything already exported."""
    from .annotations import project_annotations
    from .export_scope import exclude_not_required, select_sources
    result = project_annotations(exclude_not_required(select_sources(project, source_ids)),annotation_track)
    if mode == "append":
        # Imported existing labels are for editing, not instructions to duplicate
        # the target's own episodes when appending new bags to that same dataset.
        base_ids = {s["source_id"] for s in result["sources"] if s["kind"] == "lerobot"
                    and (Path(s["path"]).resolve() == Path(target).resolve()
                         or s.get("repository_target") == str(Path(target).resolve()))}
        result["sources"] = [s for s in result["sources"] if s["source_id"] not in base_ids]
        result["clips"] = [c for c in result["clips"] if c["source_id"] not in base_ids]
        for key in ('goals','exclusions'):
            result[key]=[c for c in result.get(key,[]) if c['source_id'] not in base_ids]
        from .export_scope import skip_exported_frames
        result = skip_exported_frames(result, target)
    return result


def prepare_export(project, target, options=None):
    from .exporter import clean_features
    from .sources import open_source
    from .export_scope import normalize_export_options
    options = normalize_export_options(options)
    mode = options.get("mode", "new")
    if mode not in ("new", "append", "replace"):
        raise EditError("알 수 없는 dataset 저장 방식입니다.")
    target = Path(target).expanduser().resolve()
    selection = selected_project(project, target, mode,options.get('annotation_track','step'),options.get('source_ids'))
    from .quality import resolve_quality
    selection, quality = resolve_quality(selection, options.get('quality'))
    plan = compile_selection(selection)
    if not plan["runs"]:
        if selection.get('_export_removed_sources'):
            raise EditError('프로젝트에서 제거한 원본은 Export에서 제외됩니다. 제거한 원본 목록에서 복원하세요.')
        if selection.get('_export_duplicate_frames'):
            raise EditError('선택 구간은 이미 이 dataset에 Export되었습니다. 중복 추가하지 않습니다. '
                            '기존 라벨을 바꾸려면 기존 dataset을 불러와 수정하세요.')
        if selection.get('_export_not_required_sources'):
            raise EditError('라벨링 불필요로 표시한 원본은 Export에서 제외됩니다. '
                            '내보내려면 해당 원본의 라벨링 불필요 표시를 해제하세요.')
        if options.get('annotation_track')=='goal':
            raise EditError('Goal에 Export할 구간이 없습니다. Goal 범위를 지정하세요. 기본은 Step이 있는 부분만 포함하며, 필요하면 Goal의 미지정 Step 포함을 선택하세요.')
        raise EditError("저장할 라벨 구간이 없습니다. 추가 모드에서는 대상 dataset 자체의 구간은 제외합니다.")
    used = {run["source_id"] for run in plan["runs"]}
    from .wholebody import schema_name, schema_features, validate_source, contract
    schema = schema_name(options)
    schemas = []
    for source in selection['sources']:
        if source['source_id'] not in used:
            continue
        validate_source(source,schema)
        schemas.append(clean_features(schema_features(open_source(source).features,schema)))
    if any(schema != schemas[0] for schema in schemas[1:]):
        raise EditError("source feature schema가 다릅니다. 같은 profile끼리 export하세요.")
    for source in project["sources"]:
        original = Path(source.get("original_path", source["path"])).resolve()
        exact_dataset = source["kind"] == "lerobot" and Path(source["path"]).resolve() == target
        if target == original or target.is_relative_to(original) or original.is_relative_to(target):
            if not (mode != "new" and exact_dataset):
                raise EditError("출력 경로는 원본 경로와 겹칠 수 없습니다.")
    base_frames = base_episodes = 0
    baseline = None
    if mode == "new":
        identity = repo_identity(options.get("repo_name") or target.name)
        if target.exists():
            raise EditError("이미 존재하는 폴더입니다. 새 이름 또는 기존 dataset 저장 방식을 선택하세요.")
    else:
        if not (target / "meta/info.json").is_file():
            raise EditError("기존 LeRobot dataset 루트 폴더를 지정하세요.")
        try:
            base = LeRobotSource(target)
            identity = existing_identity(target)
        except (ValueError, KeyError, OSError) as exc:
            raise EditError(f"기존 dataset을 읽지 못했습니다: {exc}") from exc
        if mode == "append":
            if base.info.get("codebase_version") != "v3.0":
                raise EditError("추가는 LeRobot v3.0 dataset에 지원합니다. 이전 형식은 먼저 새 dataset으로 Export하세요.")
            if base.fps != plan["fps"] or clean_features(base.features) != schemas[0]:
                raise EditError("기존 dataset과 FPS/feature schema가 다릅니다. 자동 변환하거나 누락 feature를 채우지 않습니다.")
            base_frames = sum(ep["length"] for ep in base.episodes)
            base_episodes = len(base.episodes)
        if mode == "replace" and not any(
            s["kind"] == "lerobot" and Path(s["path"]).resolve() == target for s in project["sources"]
        ):
            raise EditError("수정할 dataset을 먼저 이 프로젝트의 원본으로 불러오세요.")
        baseline = fingerprint(target)
    return {**plan, **identity, "mode": mode, "target": str(target), "baseline": baseline, "quality": quality,
            'skipped_duplicate_frames':selection.get('_export_duplicate_frames',0),
            'annotation_track':options.get('annotation_track','step'),
            'schema':schema,'schema_contract':contract(schema) if schema!='legacy' else None,
            "existing_frames": base_frames, "existing_episodes": base_episodes,
            "output_frames": base_frames + plan["kept_frames"],
            "output_episodes": base_episodes + plan["num_episodes"]}


def retarget_project(project, old_path, backup, baseline):
    result = deepcopy(project)
    for source in result["sources"]:
        if source["kind"] == "lerobot" and Path(source["path"]).resolve() == Path(old_path).resolve():
            if source.get("fingerprint") != baseline:
                raise EditError("수정 대상 원본이 불러온 뒤 변경되었습니다. 다시 불러오세요.")
            source["path"] = str(backup)
            source["repository_target"] = str(old_path)
            if source.get("original_path") == str(old_path):
                source["original_path"] = str(backup)
    return result


class RepositoryTransaction:
    def __init__(self, project, target, job_root, options=None):
        self.project, self.job_root = project, Path(job_root).resolve()
        from .export_scope import normalize_export_options
        self.options = normalize_export_options(options)
        self.plan = prepare_export(project, target, self.options)
        if not self.plan['quality']['ready']:
            raise EditError('누락 데이터가 있습니다. 미리보기에서 끊긴 프레임 수와 시간을 확인하고 보정/제외를 선택하세요.')
        from .quality import selection_with_quality
        self.selection = selection_with_quality(selected_project(project, target, self.plan["mode"],self.options.get('annotation_track','step'),self.options.get('source_ids')), self.plan['quality'])
        self.target = Path(self.plan["target"])
        self.staging = self.target.parent / f".{self.target.name}.{self.job_root.name}.partial"
        self.backup = self.target.parent / f".{self.target.name}.backup-{self.job_root.name}"
        self.lock_path = self.target.parent / f".{self.target.name}.editor-write.lock"
        self.locked = False

    def __enter__(self):
        if self.plan["mode"] != "new" and self.options.get("confirm_existing") is not True:
            raise EditError("기존 dataset 변경 확인이 필요합니다. 복구본을 남기는 추가/수정을 명시적으로 확인하세요.")
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.job_root.mkdir(parents=True, exist_ok=True)
        from .dataset_lock import dataset_lock
        self._lock = dataset_lock(self.target, job=str(self.job_root), kind='export')
        self._lock.__enter__()
        self.locked = True
        try:
            from .checkpoints import checkpoint_state
            self.resuming = checkpoint_state(self.job_root) is not None
            if (self.staging.exists() and not self.resuming) or self.backup.exists():
                raise EditError("같은 작업의 미완료 출력/복구본이 있습니다. 새 작업으로 실행하세요.")
            if self.plan["mode"] != "new":
                if fingerprint(self.target) != self.plan["baseline"]:
                    raise EditError("기존 dataset이 준비 중 변경되었습니다. 다시 실행하세요.")
                self.snapshot = retarget_project(self.selection, self.target, self.backup, self.plan["baseline"])
            else:
                self.snapshot = deepcopy(self.selection)
            if self.plan["mode"] == "append" and not self.resuming:
                from .checkpoints import frozen, link_or_copy
                def copy(source, destination):
                    name = Path(source).relative_to(self.target).as_posix()
                    return (link_or_copy if frozen(name) else shutil.copy2)(source, destination)
                shutil.copytree(self.target, self.staging, copy_function=copy)
            # Preserve dataset cards, licenses, Git metadata and extra root assets on replacement.
            if self.plan["mode"] == "replace":
                self.extra_items = [p for p in self.target.iterdir() if p.name not in
                                    {"meta", "data", "videos", "images", "editor", "EDITOR_INCOMPLETE.json"}]
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def publish(self, result):
        mode = self.plan["mode"]
        if mode == "new":
            if self.target.exists():
                raise EditError("출력 폴더가 작업 중 생성됐습니다. 덮어쓰지 않습니다.")
        else:
            if fingerprint(self.target) != self.plan["baseline"]:
                raise EditError("기존 dataset이 작업 중 변경됐습니다. 결과는 partial에 보존했습니다.")
            from .storage import share_unchanged_media
            share_unchanged_media(self.target, self.staging)
            if mode == "replace":
                for item in self.extra_items:
                    destination = self.staging / item.name
                    if item.is_dir():
                        shutil.copytree(item, destination)
                    else:
                        shutil.copy2(item, destination)
            self.target.rename(self.backup)
        try:
            self.staging.rename(self.target)
        except BaseException:
            if mode != "new" and not self.target.exists():
                self.backup.rename(self.target)
            raise
        result.update(path=str(self.target), mode=mode, repo_id=self.plan["repo_id"],
                      published_at=time.time(), project_name=self.project.get('name'),
                      episodes_added=self.plan['num_episodes'],
                      skipped_duplicate_frames=self.plan.get('skipped_duplicate_frames',0),
                      dataset_name=self.plan["name"], frames_added=self.plan["kept_frames"],
                      backup=str(self.backup) if mode != "new" else None)
        if mode != "new":
            result["source_relocation"] = {"old_path": str(self.target), "backup": str(self.backup),
                                           "fingerprint": self.plan["baseline"]}
        write_json(self.job_root / "publication.json", result)
        return result

    def __exit__(self, *_):
        if self.locked:
            self._lock.__exit__(None, None, None)
            self.locked = False
