"""Rebuild edited datasets with the official LeRobot writer, never in-place."""
import json
import os
from copy import deepcopy
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .core import EditError, compile_selection
from .integrations.lerobot import LeRobotBackend
from .sources import CatalogSource, fingerprint, open_source, write_json


def clean_features(features):
    result = {}
    for key, ft in features.items():
        if ft["dtype"] not in {"video", "image", "string", "bool", "float32", "float64", "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"}:
            raise EditError(f"지원하지 않는 feature dtype: {key}={ft['dtype']}")
        result[key] = {"dtype": "video" if ft["dtype"] == "image" else ft["dtype"],
                       "shape": tuple(ft["shape"]), "names": deepcopy(ft.get("names"))}
    return result


def export_dataset(project, target, job_root, progress):
    cache = Path(job_root).resolve() / "hf_cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache)
    os.environ["HF_DATASETS_CACHE"] = str(cache / "datasets")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    backend = LeRobotBackend()
    plan = compile_selection(project)
    if not plan["runs"]:
        raise EditError("라벨이 있는 구간이 없습니다.")
    target, job_root = Path(target).resolve(), Path(job_root).resolve()
    sources = {s["source_id"]: s for s in project["sources"]}
    for source in sources.values():
        original = Path(source.get("original_path", source["path"])).resolve()
        if target == original or target.is_relative_to(original) or original.is_relative_to(target):
            raise EditError("출력은 원본 경로와 겹칠 수 없습니다.")
    if target.exists():
        raise EditError("기존 출력은 덮어쓰지 않습니다. 새 폴더 이름을 지정하세요.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Same parent filesystem permits atomic publication without copying large videos.
    staging = target.parent / f".{target.name}.{job_root.name}.partial"
    if staging.exists():
        raise EditError(f"미완료 출력이 있습니다. 다른 출력 이름을 사용하세요: {staging}")
    used = {r["source_id"] for r in plan["runs"]}
    adapters = {sid: open_source(sources[sid]) for sid in used}
    time_maps = {sid: np.load(adapter.root / adapter.manifest["time_mapping"], allow_pickle=False)
                 for sid, adapter in adapters.items() if isinstance(adapter, CatalogSource) and adapter.manifest.get("time_mapping")}
    features = clean_features(next(iter(adapters.values())).features)
    for sid, adapter in adapters.items():
        if clean_features(adapter.features) != features:
            raise EditError("source feature schema가 다릅니다. 같은 profile끼리 export하세요.")
        expected = sources[sid].get("fingerprint")
        original = Path(sources[sid].get("original_path", sources[sid]["path"]))
        if expected and expected["method"] == "manifest-size-mtime-sha256" and fingerprint(original) != expected:
            raise EditError(f"원본 파일 목록/크기가 변경되었습니다: {sid}. source를 다시 등록하세요.")
    writer = backend.create_writer(fps=plan["fps"], features=features, root=staging,
                                   robot_type=next(iter(sources.values())).get("robot_type"))
    output_rows, truncations, finished = [], [], 0
    try:
        for out_episode, run in enumerate(plan["runs"]):
            source = sources[run["source_id"]]
            adapter = adapters[run["source_id"]]
            original_episode = next(e for e in source["episodes"] if e["episode_id"] == run["episode_id"])
            truncations.append({"episode_index": out_episode,
                                "start_trimmed": run["start_frame"] != 0,
                                "end_trimmed": run["end_frame"] != original_episode["num_frames"],
                                "terminal_features": "preserved_without_inventing_success"})
            for clip in run["clips"]:
                for source_frame in range(clip["start_frame"], clip["end_frame"]):
                    progress(finished / plan["kept_frames"]*0.9, f"episode {out_episode+1}/{plan['num_episodes']} export")
                    frame = adapter.row(run["episode_id"], source_frame)
                    frame["task"] = clip["task"]
                    writer.add_frame(frame)
                    out_frame = source_frame-run["start_frame"]
                    output_rows.append({"episode_index": out_episode, "frame_index": out_frame,
                                        "index": finished, "source_id": run["source_id"],
                                        "source_episode": run["episode_id"], "source_frame": source_frame,
                                        "source_time_s": source_frame/plan["fps"], "clip_id": clip["id"],
                                        "task": clip["task"], "split": run["split"]})
                    if run["source_id"] in time_maps:
                        times = time_maps[run["source_id"]]
                        output_rows[-1]["source_bag_time_ns"] = int(times["bag_time_ns"][source_frame])
                        for key in times.files:
                            if key != "bag_time_ns":
                                output_rows[-1]["source/"+key] = int(times[key][source_frame])
                    finished += 1
            backend.save_episode(writer)
        writer.finalize()
    except BaseException:
        # Partial output is intentionally recoverable, never confused with a complete dataset.
        write_json(staging / "EDITOR_INCOMPLETE.json", {"job": job_root.name, "frames_added": finished})
        raise
    provenance = staging / "editor"
    provenance.mkdir()
    write_json(provenance / "project.json", project)
    write_json(provenance / "export_plan.json", plan)
    write_json(provenance / "truncation.json", truncations)
    pq.write_table(pa.Table.from_pylist(output_rows), provenance / "frame_mapping.parquet")
    # Events remain sparse; an event is copied only where its source time is retained.
    with (provenance / "events.jsonl").open("w") as output_events:
        for sid, adapter in adapters.items():
            if not isinstance(adapter, CatalogSource) or not adapter.manifest.get("events"):
                continue
            event_file = adapter.root / adapter.manifest["events"]
            with event_file.open() as event_stream:
                for line in event_stream:
                    event = json.loads(line)
                    t = event["source_time_s"]
                    for index, run in enumerate(plan["runs"]):
                        if run["source_id"] == sid and run["start_frame"]/plan["fps"] <= t < run["end_frame"]/plan["fps"]:
                            mapped = {**event, "source_id": sid, "episode_index": index,
                                      "output_time_s": t-run["start_frame"]/plan["fps"]}
                            output_events.write(json.dumps(mapped, ensure_ascii=False)+"\n")
    progress(0.94, "작은 round-trip 확인")
    loaded = backend.load_dataset(staging)
    if len(loaded) != plan["kept_frames"] or loaded.meta.total_episodes != plan["num_episodes"]:
        raise RuntimeError("출력 frame/episode 수가 export 계획과 다릅니다.")
    # Sample only first/last, not a second full decoding pass.
    for index in sorted({0, len(loaded)-1}):
        _ = loaded[index]
    write_json(provenance / "report.json", {"status": "complete", "codebase_version": backend.codebase_version,
                                             "backend": backend.describe(),
                                             "frames": finished, "episodes": plan["num_episodes"],
                                             "round_trip": "official_loader_first_last",
                                             "unlabeled_removed": plan["dropped_frames"],
                                             "originals_preserved": True})
    if target.exists():
        raise EditError("출력 폴더가 작업 중 생성되었습니다. 기존 파일은 덮어쓰지 않습니다.")
    progress(0.99, "완료 결과 게시")
    staging.rename(target)
    return {"path": str(target), "frames": finished, "episodes": plan["num_episodes"]}
