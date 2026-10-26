"""ROS-free ROS1 bag adapter using recorded message definitions via rosbags."""
import io
import json
import math
from dataclasses import fields, is_dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from .ros1_reader import Ros1Reader

from .sources import CatalogSource, fingerprint, write_json
from .storage import open_events


def attr(message, path):
    for part in path.split("."):
        message = getattr(message, part)
    return message


def plain(value):
    if is_dataclass(value):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value) if not f.name.startswith("__")}
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def inspect_bag(path):
    with Ros1Reader(path) as reader:
        return {"start_ns": reader.start_time, "end_ns": reader.end_time,
                "duration_s": (reader.end_time-reader.start_time)/1e9,
                "topics": [{"topic": c.topic, "type": c.msgtype, "count": c.msgcount} for c in reader.connections]}


def image_from_message(msg):
    if hasattr(msg, "format"):
        return Image.open(io.BytesIO(bytes(msg.data))).convert("RGB")
    encoding = msg.encoding.lower()
    if encoding not in ("rgb8", "bgr8", "rgba8", "bgra8", "mono8"):
        raise ValueError(f"RGB preview 미지원 encoding: {encoding}")
    channels = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4, "mono8": 1}[encoding]
    a = np.asarray(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)[:, :msg.width*channels]
    a = a.reshape(msg.height, msg.width, channels)
    if encoding.startswith("bgr"):
        a = a[:, :, [2, 1, 0] + ([3] if channels == 4 else [])]
    return Image.fromarray(a.squeeze(-1) if channels == 1 else a).convert("RGB")


def signal_value(message, spec):
    if "fields" in spec:
        value = [attr(message, f) for f in spec["fields"]]
    else:
        value = attr(message, spec["field"])
    value = np.asarray(value, dtype=np.float32)
    if "names_field" in spec:
        names = list(attr(message, spec["names_field"]))
        if names:
            value = value[[names.index(n) for n in spec["order"]]]
        # Empty names in firmware means the documented left/right order.
    if "slice" in spec:
        value = value[slice(*spec["slice"])]
    if value.shape != (spec["size"],):
        raise ValueError(f"{spec['topic']}: 예상 shape {(spec['size'],)}, 실제 {value.shape}")
    return value * spec.get("scale", 1.0)


def matched_indices(times, targets, method, tolerance_ns):
    times = np.asarray(times, dtype=np.int64)
    if not len(times):
        return np.full(len(targets), -1, dtype=np.int64)
    idx = np.searchsorted(times, targets, side="right") - 1
    if method == "nearest":
        left = np.clip(idx, 0, len(times)-1)
        right = np.clip(idx+1, 0, len(times)-1)
        idx = np.where(abs(times[right]-targets) < abs(times[left]-targets), right, left)
    valid = idx >= 0
    idx = np.clip(idx, 0, len(times)-1)
    valid &= abs(times[idx]-targets) <= tolerance_ns
    return np.where(valid, idx, -1)


def import_bag(path, root, sid, profile, progress):
    """Preserve compressed RGB bytes; raw images use PNG. Arrays use mmap."""
    path, root = Path(path).resolve(), Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    if profile.get("clock") != "bag_receipt_time":
        raise ValueError("현재 clock은 bag_receipt_time만 지원합니다.")
    fps = profile["fps"]
    if type(fps) is not int or not 1 <= fps <= 120:
        raise ValueError("FPS는 1–120 사이의 정수로 명시하세요.")
    cameras = profile["cameras"]
    signals = profile["signals"]
    wanted = set(cameras.values()) | {s["topic"] for s in signals} | set(profile.get("event_topics", []))
    samples = {topic: [] for topic in wanted}
    image_paths, dimensions = {key: [] for key in cameras}, {}
    topic_to_key = {v: k for k, v in cameras.items()}
    errors, counts = [], {}
    initial_fingerprint = fingerprint(path)
    # Original sparse events, including tag detections, are preserved as messages.
    with open_events(root / "events.jsonl.gz", "wt") as events, Ros1Reader(path) as reader:
        origin, last = int(reader.start_time), int(reader.end_time)-1
        connections = [c for c in reader.connections if c.topic in wanted]
        available = {c.topic for c in connections}
        for key, topic in cameras.items():
            if topic not in available:
                raise ValueError(f"카메라 topic이 없습니다: {topic}. profile에서 사용할 카메라를 지정하세요.")
        for spec in signals:
            if spec.get("required") and spec["topic"] not in available:
                raise ValueError(f"필수 데이터 topic이 없습니다: {spec['topic']}")
        for count, (connection, stamp, raw) in enumerate(reader.messages(connections=connections)):
            if count % 100 == 0:
                progress(min(0.74, (stamp-origin)/max(1, last-origin)*0.74), "bag 읽기·카메라 추출")
            msg = reader.deserialize(raw, connection.msgtype)
            topic = connection.topic
            counts[topic] = counts.get(topic, 0)+1
            if topic in topic_to_key:
                key = topic_to_key[topic]
                camera_id = list(cameras).index(key)
                folder = root / f"camera_{camera_id}"
                folder.mkdir(exist_ok=True)
                if hasattr(msg, "format"):
                    compressed = bytes(msg.data)
                    img = Image.open(io.BytesIO(compressed))
                    suffix = "jpg" if img.format == "JPEG" else "png" if img.format == "PNG" else None
                else:
                    compressed, suffix = None, None
                    img = image_from_message(msg)
                relative = f"camera_{camera_id}/{len(samples[topic]):08d}.{suffix or 'png'}"
                shape = [img.height, img.width, 3]
                if key in dimensions and dimensions[key] != shape:
                    raise ValueError(f"{topic}: 영상 크기가 중간에 변경되었습니다.")
                dimensions[key] = shape
                if compressed is not None and suffix:
                    (root / relative).write_bytes(compressed)
                else:
                    img.convert("RGB").save(root / relative, compress_level=1)
                samples[topic].append((int(stamp), relative))
            else:
                for spec in (s for s in signals if s["topic"] == topic):
                    try:
                        value = signal_value(msg, spec)
                    except (AttributeError, ValueError, IndexError) as exc:
                        raise ValueError(f"{topic} field 매핑을 확인하세요: {exc}") from exc
                    # Per-feature storage handles multiple fields from one topic.
                    samples.setdefault("feature:"+spec["key"], []).append((int(stamp), value))
                if topic in profile.get("event_topics", []):
                    events.write(json.dumps({"topic": topic, "bag_time_ns": int(stamp),
                                              "source_time_s": (stamp-origin)/1e9,
                                              "message": plain(msg)}, ensure_ascii=False)+"\n")
    n = max(1, math.floor((last-origin)*fps/1e9)+1)
    targets = origin + (np.arange(n, dtype=np.int64)*1_000_000_000 // fps)
    features, arrays, validity_masks, mapping = {}, {}, {}, {"bag_time_ns": targets}
    report = {"topics": counts, "missing_samples": {}, "max_alignment_error_s": {},
              "omitted_optional_features": [], "clock": "bag_receipt_time"}
    for key, topic in cameras.items():
        entries = samples[topic]
        if not entries:
            raise ValueError(f"카메라 메시지가 없습니다: {topic}")
        times = np.array([s[0] for s in entries], dtype=np.int64)
        idx = matched_indices(times, targets, "nearest", int(profile["camera_tolerance_s"]*1e9))
        image_paths[key] = [entries[i][1] if i >= 0 else None for i in idx]
        mapping[key+"/message_ns"] = np.where(idx >= 0, times[np.maximum(idx, 0)], -1)
        features[key] = {"dtype": "video", "shape": dimensions[key], "names": ["height", "width", "channels"]}
        report["missing_samples"][key] = int((idx < 0).sum())
        report["max_alignment_error_s"][key] = float(np.max(abs(times[idx[idx>=0]]-targets[idx>=0]))/1e9) if (idx>=0).any() else None
    for number, spec in enumerate(signals):
        key = spec["key"]
        entries = samples.get("feature:"+key, [])
        if not entries:
            if spec.get("required"):
                raise ValueError(f"메시지가 없습니다: {spec['topic']}")
            report["omitted_optional_features"].append(key)
            continue
        times = np.array([e[0] for e in entries], dtype=np.int64)
        tolerance = profile["command_max_age_s"] if spec["method"] == "previous" else profile["state_tolerance_s"]
        idx = matched_indices(times, targets, spec["method"], int(tolerance*1e9))
        data = np.full((n, spec["size"]), np.nan, dtype=np.float32)
        valid = idx >= 0
        data[valid] = np.stack([entries[i][1] for i in idx[valid]])
        if not spec.get("required"):
            # Explicit invalid placeholders retain A-off/idle frames without claiming a measurement.
            # Consumers must use the corresponding mask; zero is NOT a measured value here.
            data[~valid] = 0
            mask_key, mask_file = "validity."+key, f"signal_{number}_valid.npy"
            np.save(root / mask_file, valid.reshape(-1, 1))
            arrays[mask_key] = mask_file
            features[mask_key] = {"dtype": "bool", "shape": [1], "names": ["valid"]}
            validity_masks[key] = mask_key
        filename = f"signal_{number}.npy"
        np.save(root / filename, data)
        arrays[key] = filename
        features[key] = {"dtype": "float32", "shape": [spec["size"]],
                         "names": spec.get("order", [f"{key}_{i}" for i in range(spec["size"])])}
        mapping[key+"/message_ns"] = np.where(valid, times[np.maximum(idx, 0)], -1)
        report["missing_samples"][key] = int((~valid).sum())
        report["max_alignment_error_s"][key] = float(np.max(abs(times[idx[valid]]-targets[valid]))/1e9) if valid.any() else None
    np.savez(root / "time_mapping.npz", **mapping)
    progress(0.92, "catalog 저장")
    manifest = {"source_id": sid, "kind": "catalog", "path": str(root), "original_path": str(path),
                "fingerprint": initial_fingerprint, "fps": fps, "robot_type": "kuavo_5w",
                "features": features, "cameras": list(cameras), "arrays": arrays, "images": image_paths,
                "episodes": [{"episode_id": 0, "num_frames": n}], "origin_bag_time_ns": origin,
                "profile": profile, "report": report, "events": "events.jsonl.gz", "time_mapping": "time_mapping.npz",
                "validity_masks": validity_masks, "invalid_placeholder": "zero_only_where_validity_is_false"}
    write_json(root / "catalog.json", manifest)
    write_json(root / "import_report.json", report)
    return CatalogSource(root).descriptor(sid)
