"""Deterministic, dependency-free interval and export planning."""
from copy import deepcopy
from fractions import Fraction
from math import ceil
from uuid import uuid4


class EditError(ValueError):
    pass


def frame_at(seconds, fps):
    return ceil(Fraction(str(seconds)) * Fraction(str(fps)))


def new_project(name="새 프로젝트"):
    return {"format_version": 1, "project_id": uuid4().hex, "revision": 0,
            "name": name, "main_task": "", "sources": [], "tasks": {}, "clips": [], "goals": [], "exclusions": [],
            "source_splits": {}, "export": {"drop_unlabeled": True,
            "join_across_gaps": False, "episode_policy": "contiguous_labeled_runs",
            "overwrite": False, "push_to_hub": False}}


def source_key(clip):
    return clip["source_id"], clip.get("episode_id", 0)


def subtract(clips, source_id, episode_id, start, end):
    """Remove coverage, never source frames. Preserve IDs and explicit boundaries."""
    result = []
    for original in clips:
        c = deepcopy(original)
        if source_key(c) != (source_id, episode_id) or c["end_frame"] <= start or c["start_frame"] >= end:
            result.append(c)
            continue
        if c["start_frame"] < start:
            left = deepcopy(c)
            left["end_frame"] = start
            result.append(left)
        if c["end_frame"] > end:
            right = deepcopy(c)
            right.update(id=uuid4().hex, parent_id=c["id"], start_frame=end, new_episode=False)
            result.append(right)
    return result


def label(project, source_id, episode_id, start, end, text, replace=False, new_episode=False):
    p = deepcopy(project)
    if not text.strip():
        raise EditError("task 문장을 입력하세요. 라벨 제거는 구간 제외를 사용하세요.")
    overlap = any(source_key(c) == (source_id, episode_id) and c["start_frame"] < end
                  and c["end_frame"] > start for c in p["clips"])
    if overlap and not replace:
        raise EditError("겹치는 clip이 있습니다. 명시적으로 교체를 선택하세요.")
    if replace:
        p["clips"] = subtract(p["clips"], source_id, episode_id, start, end)
    task_id = next((k for k, v in p["tasks"].items() if v == text.strip()), uuid4().hex)
    p["tasks"][task_id] = text.strip()
    p["clips"].append({"id": uuid4().hex, "source_id": source_id, "episode_id": episode_id,
                       "start_frame": start, "end_frame": end, "task_id": task_id,
                       "new_episode": bool(new_episode)})
    compile_selection(p)
    return p


def split_clip(project, clip_id, frame):
    p = deepcopy(project)
    c = next((c for c in p["clips"] if c["id"] == clip_id), None)
    if c is None or not c["start_frame"] < frame < c["end_frame"]:
        raise EditError("clip 내부 프레임에서 분할하세요.")
    right = deepcopy(c)
    right.update(id=uuid4().hex, parent_id=c["id"], start_frame=frame, new_episode=False)
    c["end_frame"] = frame
    p["clips"].append(right)
    return p


def compile_selection(project):
    if project.get("format_version") != 1:
        raise EditError("지원하지 않는 프로젝트 버전입니다.")
    from .annotations import validate_extra_tracks
    validate_extra_tracks(project)
    sources = {s["source_id"]: s for s in project["sources"]}
    if len(sources) != len(project["sources"]):
        raise EditError("중복된 source ID입니다.")
    assignments = {}
    for s in sources.values():
        identity = str(s.get("fingerprint"))
        split = project.get("source_splits", {}).get(s["source_id"], "unspecified")
        if identity in assignments and assignments[identity] != split:
            raise EditError("같은 원본 recording은 서로 다른 학습 split으로 나눌 수 없습니다.")
        assignments[identity] = split
    order = {sid: i for i, sid in enumerate(sources)}
    runs, fps_values, identities = [], set(), set()
    clips = sorted(project["clips"], key=lambda c: (order.get(c["source_id"], -1), c.get("episode_id", 0), c["start_frame"]))
    previous = None
    for c in clips:
        if c["id"] in identities:
            raise EditError("중복된 clip ID입니다.")
        identities.add(c["id"])
        source = sources.get(c["source_id"])
        if source is None:
            raise EditError("존재하지 않는 source입니다.")
        episode = next((e for e in source["episodes"] if e["episode_id"] == c.get("episode_id", 0)), None)
        start, end = c["start_frame"], c["end_frame"]
        if episode is None or type(start) is not int or type(end) is not int or not 0 <= start < end <= episode["num_frames"]:
            raise EditError("clip 경계가 원본 프레임 범위를 벗어났습니다.")
        task = project["tasks"].get(c["task_id"], "").strip()
        if not task:
            raise EditError("task가 없는 clip입니다.")
        if previous and source_key(previous) == source_key(c) and previous["end_frame"] > start:
            raise EditError("clip이 겹칩니다. 교체하거나 경계를 조절하세요.")
        if not previous or source_key(previous) != source_key(c) or previous["end_frame"] != start or c.get("new_episode"):
            runs.append({"source_id": c["source_id"], "episode_id": c.get("episode_id", 0),
                         "start_frame": start, "end_frame": end, "clips": [],
                         "split": project.get("source_splits", {}).get(c["source_id"], "unspecified")})
        runs[-1]["end_frame"] = end
        runs[-1]["clips"].append({**deepcopy(c), "task": task})
        fps_values.add(source["fps"])
        previous = c
    if len(fps_values) > 1:
        raise EditError("한 export에는 같은 FPS의 source를 사용하세요. 자동 재샘플링하지 않습니다.")
    kept = sum(r["end_frame"] - r["start_frame"] for r in runs)
    total = sum(e["num_frames"] for s in sources.values() for e in s["episodes"])
    return {"runs": runs, "num_episodes": len(runs), "kept_frames": kept,
            "dropped_frames": total - kept, "fps": next(iter(fps_values), None),
            "project_revision": project["revision"]}
