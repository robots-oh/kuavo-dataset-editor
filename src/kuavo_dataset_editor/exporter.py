"""Official writer with staged new/append/replace publication."""
import json
import os
import time
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


def _write_dataset(project, staging, job_root, progress, transaction):
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
    staging, job_root = Path(staging).resolve(), Path(job_root).resolve()
    settings = transaction.plan
    append = settings["mode"] == "append"
    base_frames, base_episodes = settings["existing_frames"], settings["existing_episodes"]
    sources = {s["source_id"]: s for s in project["sources"]}
    used = {r["source_id"] for r in plan["runs"]}
    adapters = {sid: open_source(sources[sid]) for sid in used}
    time_maps = {}
    for sid, adapter in adapters.items():
        if isinstance(adapter, CatalogSource) and adapter.manifest.get('time_mapping'):
            # NPZ indexing decompresses an entire column, not just one element.
            with np.load(adapter.root / adapter.manifest['time_mapping'], allow_pickle=False) as mapping:
                time_maps[sid] = {key: mapping[key] for key in mapping.files}
    features = clean_features(next(iter(adapters.values())).features)
    for sid, adapter in adapters.items():
        if clean_features(adapter.features) != features:
            raise EditError("source feature schema가 다릅니다. 같은 profile끼리 export하세요.")
        expected = sources[sid].get("fingerprint")
        original = Path(sources[sid].get("original_path", sources[sid]["path"]))
        if expected and expected["method"] == "manifest-size-mtime-sha256" and fingerprint(original) != expected:
            raise EditError(f"원본 파일 목록/크기가 변경되었습니다: {sid}. source를 다시 등록하세요.")
    from .repositories import existing_provenance, preserve_editor_history
    from .checkpoints import EpisodeCheckpoint
    from .export_runtime import ExportRuntime, prefetch_ordered
    runtime = ExportRuntime.choose(features)
    backend.runtime = runtime
    checkpoint = EpisodeCheckpoint(staging, job_root, {
        'pipeline': 2,
        'project': transaction.project, 'settings': settings, 'backend': backend.describe(),
        'sources': {sid: fingerprint(adapter.root) for sid, adapter in adapters.items()}})
    restored = checkpoint.restore()
    output_rows = restored['rows'] if restored else (existing_provenance(staging, settings) if append else [])
    truncation_file = staging / "editor/truncation.json"
    truncations = restored['truncations'] if restored else (json.loads(truncation_file.read_text()) if append and truncation_file.exists() else [])
    finished = restored['completed_frames'] if restored else 0
    completed = restored['completed_episodes'] if restored else 0
    timings = restored['timings'] if restored else []
    if not restored:
        checkpoint.save(0, 0, output_rows, truncations, timings)
    progress(finished / plan['kept_frames'] * 0.9,
             f"완료 episode {completed}/{plan['num_episodes']} 재사용; 다음 episode부터 처리")
    from .quality import corrected_row, repair_index
    repairs = repair_index(settings['quality'])
    active_message = '프레임 준비/encoder 처리 중 (누락 없이 대기)'
    def heartbeat():
        progress(finished / plan['kept_frames'] * 0.9, active_message)
    backend.heartbeat = heartbeat
    writer = None
    try:
        for out_episode, run in enumerate(plan["runs"], start=base_episodes):
            if out_episode < base_episodes + completed:
                continue
            episode_start = time.monotonic()
            # A new official resume writer starts new data/video chunks. A crash
            # can change metadata, but never rewrites a completed episode's media.
            writer = (backend.resume_writer(staging, settings['repo_id']) if append or completed else
                      backend.create_writer(fps=plan['fps'], features=features, root=staging,
                                            robot_type=sources[run['source_id']].get('robot_type'),
                                            repo_id=settings['repo_id']))
            source = sources[run["source_id"]]
            adapter = adapters[run["source_id"]]
            active_message = f"추가/선택 episode {out_episode-base_episodes+1}/{plan['num_episodes']} export"
            original_episode = next(e for e in source["episodes"] if e["episode_id"] == run["episode_id"])
            truncations.append({"episode_index": out_episode,
                                "start_trimmed": run["start_frame"] != 0,
                                "end_trimmed": run["end_frame"] != original_episode["num_frames"],
                                "terminal_features": "preserved_without_inventing_success"})
            requests = ((clip, source_frame) for clip in run['clips']
                        for source_frame in range(clip['start_frame'], clip['end_frame']))
            def read_frame(request):
                _, source_frame = request
                fixes = repairs.get((run['source_id'], run['episode_id'], source_frame), [])
                return corrected_row(adapter, run['episode_id'], source_frame, fixes,
                                     time_maps.get(run['source_id']))
            from contextlib import closing
            with closing(prefetch_ordered(requests, read_frame, runtime, heartbeat)) as decoded:
                for (clip, source_frame), frame in decoded:
                    heartbeat()
                    frame_repairs = repairs.get((run['source_id'], run['episode_id'], source_frame), [])
                    frame["task"] = clip["task"]
                    writer.add_frame(frame)
                    out_frame = source_frame-run["start_frame"]
                    output_rows.append({"episode_index": out_episode, "frame_index": out_frame,
                                        "index": base_frames + finished, "source_id": run["source_id"],
                                        "source_episode": run["episode_id"], "source_frame": source_frame,
                                        "source_time_s": source_frame/plan["fps"], "clip_id": clip["id"],
                                        "task": clip["task"], "split": run["split"]})
                    if frame_repairs:
                        output_rows[-1]['quality/imputed_features'] = [item['feature'] for item in frame_repairs]
                    if run["source_id"] in time_maps:
                        times = time_maps[run["source_id"]]
                        output_rows[-1]["source_bag_time_ns"] = int(times["bag_time_ns"][source_frame])
                        for key in times:
                            if key != "bag_time_ns":
                                output_rows[-1]["source/"+key] = int(times[key][source_frame])
                    finished += 1
            frames_done = time.monotonic()
            active_message = f"episode {out_episode-base_episodes+1}/{plan['num_episodes']} 영상/metadata 마감 중"
            progress(finished / plan['kept_frames'] * 0.9,
                     active_message)
            backend.save_episode(writer)
            writer.finalize()
            completed += 1
            finalized = time.monotonic()
            timings.append({'episode': out_episode, 'frames_seconds': frames_done-episode_start,
                            'save_seconds': finalized-frames_done,
                            'video_frames': backend.video_counts(writer)})
            checkpoint.save(completed, finished, output_rows, truncations, timings)
            progress(finished / plan['kept_frames'] * 0.9,
                     f"완료 episode {completed}/{plan['num_episodes']} checkpoint 저장됨")
    except BaseException:
        # Stop streaming threads and close partial metadata before a retry can
        # restore the journal. Never leave an old writer alive in this process.
        if writer is not None:
            from contextlib import suppress
            with suppress(Exception):
                internal = getattr(writer, 'writer', None)
                cancel = getattr(internal, 'cancel_pending_videos', None)
                if callable(cancel):
                    cancel()
                writer.finalize()
        # Partial output is intentionally recoverable, never confused with a complete dataset.
        if staging.exists():
            write_json(staging / "EDITOR_INCOMPLETE.json", {"job": job_root.name, "frames_added": finished})
        raise
    (staging / 'EDITOR_INCOMPLETE.json').unlink(missing_ok=True)
    provenance = staging / "editor"
    provenance.mkdir(exist_ok=True)
    if settings["mode"] != "new":
        preserve_editor_history(transaction.target, staging, job_root.name)
    write_json(provenance / "project.json", transaction.snapshot)
    write_json(provenance / "dataset.json", {"name": settings["name"], "repo_id": settings["repo_id"],
                                            "mode": settings["mode"], "local_only": True})
    write_json(provenance / "operation.json", {"mode": settings["mode"], "target": str(transaction.target),
                                               "backup": str(transaction.backup) if settings["mode"] != "new" else None,
                                               "baseline": settings["baseline"], "job": str(job_root)})
    write_json(provenance / "export_plan.json", plan)
    write_json(provenance / "quality.json", settings['quality'])
    if transaction.options.get('source_quality'):
        write_json(provenance/'source_quality.json',transaction.options['source_quality'])
    from .wholebody import contract
    schema = settings.get('schema','legacy')
    if schema!='legacy':
        write_json(staging/'meta/kuavo_schema.json',contract(schema))
    else:
        # Re-editing to a NEW folder also preserves the portable schema contract.
        candidates = [adapter.root/'meta/kuavo_schema.json' for adapter in adapters.values()]
        if append or settings['mode']=='replace':
            candidates.insert(0,transaction.target/'meta/kuavo_schema.json')
        for original_contract in candidates:
            if original_contract.is_file():
                write_json(staging/'meta/kuavo_schema.json',json.loads(original_contract.read_text()))
                break
    for sid,adapter in adapters.items():
        path = getattr(adapter,'manifest',{}).get('wholebody_report')
        if path:
            destination = provenance/'reconstruction'/job_root.name
            destination.mkdir(parents=True,exist_ok=True)
            write_json(destination/(sid+'.json'),json.loads(Path(path).read_text()))
        else:
            history = adapter.root/'editor/reconstruction'
            if history.is_dir():
                for item in history.rglob('*.json'):
                    if not item.resolve().is_relative_to(adapter.root):
                        raise EditError('reconstruction 보고서가 원본 경로 밖을 가리킵니다.')
                    destination = provenance/'reconstruction'/job_root.name/sid/item.relative_to(history)
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    write_json(destination,json.loads(item.read_text()))
    write_json(provenance / "truncation.json", truncations)
    columns = sorted({key for row in output_rows for key in row})
    pq.write_table(pa.table({key: [row.get(key) for row in output_rows] for key in columns}), provenance / "frame_mapping.parquet")
    # Events remain sparse; an event is copied only where its source time is retained.
    with (provenance / "events.jsonl").open("a" if append else "w") as output_events:
        for sid, adapter in adapters.items():
            if isinstance(adapter, CatalogSource) and adapter.manifest.get("events"):
                event_file = adapter.root / adapter.manifest["events"]
                dataset_coordinates = False
            else:
                event_file = adapter.root / "editor/events.jsonl"
                dataset_coordinates = True
                if not event_file.is_file():
                    continue
            with event_file.open() as event_stream:
                for line in event_stream:
                    event = json.loads(line)
                    t = event["output_time_s"] if dataset_coordinates else event["source_time_s"]
                    for index, run in enumerate(plan["runs"], start=base_episodes):
                        if (run["source_id"] == sid and
                            (not dataset_coordinates or event["episode_index"] == run["episode_id"]) and
                            run["start_frame"]/plan["fps"] <= t < run["end_frame"]/plan["fps"]):
                            mapped = {**event, "source_id": sid, "episode_index": index,
                                      "output_time_s": t-run["start_frame"]/plan["fps"]}
                            output_events.write(json.dumps(mapped, ensure_ascii=False)+"\n")
    from .masking import finalize_mask_metadata
    progress(0.93,'유효한 값만으로 정규화 통계·누락 요약 저장 (영상 재디코딩 없음)')
    mask_summary = finalize_mask_metadata(staging)
    progress(0.94, "작은 round-trip 확인")
    loaded = backend.load_dataset(staging, settings["repo_id"])
    if len(loaded) != settings["output_frames"] or loaded.meta.total_episodes != settings["output_episodes"]:
        raise RuntimeError("출력 frame/episode 수가 export 계획과 다릅니다.")
    # Sample only first/last, not a second full decoding pass.
    for index in sorted({0, len(loaded)-1, max(0, base_frames-1), base_frames}):
        _ = loaded[index]
    write_json(provenance / "report.json", {"status": "complete", "codebase_version": backend.codebase_version,
                                             "backend": backend.describe(),
                                             "frames": base_frames + finished, "episodes": settings["output_episodes"],
                                             "frames_added": finished, "mode": settings["mode"],
                                             "episode_timings": timings,
                                             "export_runtime": runtime.describe(),
                                             "frame_drop_policy": "block_until_encoded",
                                             "round_trip": "official_loader_first_last",
                                             "unlabeled_removed": plan["dropped_frames"],
                                             "quality_issues": len(settings['quality']['issues']),
                                             "originals_preserved": True})
    return {"frames": base_frames + finished, "episodes": settings["output_episodes"],
            'schema':schema,'mask_summary':mask_summary}


def export_dataset(project, target, job_root, progress, options=None):
    from .repositories import RepositoryTransaction, prepare_export
    from .wholebody import prepare_project, schema_name
    options = dict(options or {})
    if schema_name(options)!='legacy':
        preview = prepare_export(project,target,options)
        if not preview['quality']['ready']:
            raise EditError('먼저 기존 누락 구간의 보정/제외를 선택하세요. annotation은 유지됩니다.')
        options['_target'] = str(target)
        project,options = prepare_project(project,job_root,options,progress)
    with RepositoryTransaction(project, target, job_root, options) as transaction:
        result = _write_dataset(transaction.selection, transaction.staging, job_root, progress, transaction)
        # The last cancellation point is before the short directory swap.
        progress(0.99, "완료 결과 게시 (기존 dataset은 복구본으로 보존)")
        return transaction.publish(result)
