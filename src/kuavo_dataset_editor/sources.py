"""Read-only adapters. Opening sources does not write to their directories."""
import hashlib
import io
import json
import os
import threading
from functools import lru_cache
from pathlib import Path

import av
import numpy as np
from PIL import Image

SYSTEM_KEYS = {"timestamp", "frame_index", "episode_index", "index", "task_index", "task"}


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def file_stamp(path):
    stat = Path(path).stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=16)
def _catalog_json(path, stamp):
    return json.loads(Path(path).read_text())


def catalog_json(root):
    path = Path(root) / 'catalog.json'
    return _catalog_json(str(path), file_stamp(path))


def fingerprint(path):
    """Manifest identity: sorted relative file name, size and mtime, not a content checksum."""
    p = Path(path)
    h = hashlib.sha256()
    if p.is_file():
        st = p.stat()
        h.update(f'{p.name}:{st.st_size}:{st.st_mtime_ns}\n'.encode())
    else:
        entries = []
        def scan(directory, prefix=''):
            with os.scandir(directory) as items:
                for item in items:
                    name = prefix + item.name
                    if item.is_dir(follow_symlinks=False):
                        scan(item.path, name + '/')
                    elif item.is_file():
                        entries.append((name, item.stat()))
        scan(p)
        for name, st in sorted(entries, key=lambda entry: tuple(entry[0].split('/'))):
            h.update(f'{name}:{st.st_size}:{st.st_mtime_ns}\n'.encode())
    return {"method": "manifest-size-mtime-sha256", "value": h.hexdigest()}


def _safe_child(root, relative):
    p = (root / relative).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError("source 밖의 파일은 읽을 수 없습니다.")
    return p


@lru_cache(maxsize=8)
def decode_video(path, timestamp):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        container.seek(max(0, int(timestamp / stream.time_base)), stream=stream, backward=True)
        best, distance = None, float("inf")
        for frame in container.decode(stream):
            t = float(frame.pts * stream.time_base) if frame.pts is not None else None
            if t is None:
                continue
            d = abs(t - timestamp)
            if d < distance:
                best, distance = frame, d
            if t >= timestamp:
                break
        if best is None or distance > max(0.05, 0.6 / float(stream.average_rate or 30)):
            raise ValueError(f"영상 프레임 누락: {path.name} t={timestamp:.3f}")
        return best.to_ndarray(format="rgb24")


class LeRobotSource:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.info = json.loads((self.root / "meta/info.json").read_text())
        self.fps = self.info["fps"]
        if not self.fps or self.fps <= 0:
            raise ValueError("유효하지 않은 dataset FPS")
        self.features = {k: v for k, v in self.info["features"].items() if k not in SYSTEM_KEYS}
        self.cameras = [k for k, v in self.features.items() if v["dtype"] in ("image", "video")]
        if any(self.features[k].get("info", {}).get("is_depth_map") for k in self.cameras):
            raise ValueError("현재 편집기는 RGB 영상만 지원합니다. Depth를 RGB로 변환하지 않습니다.")
        import pyarrow.parquet as pq
        paths = sorted((self.root / "meta/episodes").rglob("*.parquet"))
        if paths:
            self.episodes = [r for p in paths for r in pq.read_table(p).to_pylist()]
        else:
            path = self.root / "meta/episodes.jsonl"
            if not path.exists():
                raise ValueError("meta/episodes metadata가 없습니다.")
            self.episodes = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        self.episodes.sort(key=lambda e: e["episode_index"])
        self.lock = threading.RLock()

    def descriptor(self, sid):
        return {"source_id": sid, "kind": "lerobot", "path": str(self.root),
                "fps": self.fps, "features": self.features, "cameras": self.cameras,
                "robot_type": self.info.get("robot_type"), "fingerprint": fingerprint(self.root),
                "episodes": [{"episode_id": e["episode_index"], "num_frames": e["length"]} for e in self.episodes]}

    @lru_cache(maxsize=1)
    def rows(self, episode_id):
        import pyarrow.dataset as ds
        table = ds.dataset(str(self.root / "data"), format="parquet").to_table(filter=ds.field("episode_index") == episode_id)
        rows = sorted(table.to_pylist(), key=lambda r: r["frame_index"])
        ep = next(e for e in self.episodes if e["episode_index"] == episode_id)
        if len(rows) != ep["length"] or any(r["frame_index"] != i for i, r in enumerate(rows)):
            raise ValueError("episode metadata와 데이터 frame index가 다릅니다.")
        return rows

    def row(self, episode_id, frame, cameras=True, camera_key=None, validate=True):
        with self.lock:
            rows = self.rows(episode_id)
            if not 0 <= frame < len(rows):
                raise ValueError("프레임 범위 밖입니다.")
            row = rows[frame]
            ep = next(e for e in self.episodes if e["episode_index"] == episode_id)
            result = {}
            for key, ft in self.features.items():
                if camera_key is not None and key != camera_key:
                    continue
                if key in self.cameras:
                    if not cameras:
                        continue
                    if ft["dtype"] == "video":
                        template = self.info["video_path"]
                        chunk = ep.get(f"videos/{key}/chunk_index", episode_id // self.info.get("chunks_size", 1000))
                        file = ep.get(f"videos/{key}/file_index", episode_id)
                        relative = template.format(video_key=key, chunk_index=chunk, file_index=file,
                                                   episode_index=episode_id, episode_chunk=chunk)
                        offset = ep.get(f"videos/{key}/from_timestamp", 0)
                        result[key] = decode_video(_safe_child(self.root, relative), float(offset) + float(row["timestamp"]))
                    else:
                        image = row[key]
                        if image.get("bytes") is not None:
                            result[key] = np.array(Image.open(io.BytesIO(image["bytes"])).convert("RGB"))
                        else:
                            result[key] = np.array(Image.open(_safe_child(self.root, image["path"])).convert("RGB"))
                elif ft["dtype"] == "string":
                    result[key] = row[key]
                else:
                    result[key] = np.asarray(row[key], dtype=ft["dtype"]).reshape(ft["shape"])
            return result


class CatalogSource:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = catalog_json(self.root)
        self.features = self.manifest["features"]
        self.cameras = self.manifest["cameras"]
        self.arrays = {k: np.load(self.root / v, mmap_mode="r", allow_pickle=False)
                       for k, v in self.manifest.get("arrays", {}).items()}

    def descriptor(self, sid):
        from copy import deepcopy
        # Per-frame references belong in the catalog, not every job/project poll.
        return {**deepcopy({k: v for k, v in self.manifest.items() if k != 'images'}),
                "source_id": sid, "kind": "catalog", "path": str(self.root)}

    def row(self, episode_id, frame, cameras=True, camera_key=None, validate=True):
        if episode_id != 0 or not 0 <= frame < self.manifest["episodes"][0]["num_frames"]:
            raise ValueError("프레임 범위 밖입니다.")
        result = {}
        for key, ft in self.features.items():
            if camera_key is not None and key != camera_key:
                continue
            if key in self.cameras:
                if cameras:
                    relative = self.manifest["images"][key][frame]
                    if relative is None:
                        raise ValueError(f"{key} frame {frame}: 영상 누락. 해당 구간 제외 또는 입력 profile을 확인하세요.")
                    from .image_store import open_image
                    with open_image(self.root, relative) as image:
                        result[key] = np.array(image.convert('RGB'))
            else:
                value = np.asarray(self.arrays[key][frame]).reshape(ft["shape"])
                if validate and np.issubdtype(value.dtype, np.number) and not np.isfinite(value).all():
                    raise ValueError(f"{key} frame {frame}: 데이터 누락. 해당 구간을 제외하세요.")
                result[key] = value
        return result


def open_source(descriptor):
    if descriptor.get('export_derivation'):
        from .wholebody import WholeBodySource
        return WholeBodySource(descriptor)
    return LeRobotSource(descriptor["path"]) if descriptor["kind"] == "lerobot" else CatalogSource(descriptor["path"])


def demo_source(root, sid):
    """Small deterministic RGB/state/action fixture, not robot data."""
    from PIL import ImageDraw
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    fps, n = 10, 120
    cameras = ["observation.images.head", "observation.images.left_wrist", "observation.images.right_wrist"]
    images = {k: [] for k in cameras}
    for camera, key in enumerate(cameras):
        (root / str(camera)).mkdir()
        for frame in range(n):
            img = Image.new("RGB", (320, 180), [(24, 46, 64), (42, 39, 65), (25, 56, 49)][camera])
            draw = ImageDraw.Draw(img)
            x = 20 + int(frame / n * 240)
            draw.rectangle((x, 70, x+40, 110), fill=(242, 179, 76))
            draw.text((14, 14), f"DEMO camera {camera} | frame {frame:03d} | {frame/fps:.1f}s", fill="white")
            path = f"{camera}/{frame:06d}.jpg"
            img.save(root / path)
            images[key].append(path)
    states = np.stack([np.arange(n)/n, np.sin(np.arange(n)/20)], axis=1).astype("float32")
    np.save(root / "state.npy", states)
    np.save(root / "action.npy", states + 0.1)
    features = {"observation.state": {"dtype": "float32", "shape": [2], "names": ["x", "y"]},
                "action": {"dtype": "float32", "shape": [2], "names": ["x", "y"]}}
    features.update({k: {"dtype": "video", "shape": [180, 320, 3], "names": ["height", "width", "channels"]} for k in cameras})
    manifest = {"source_id": sid, "kind": "catalog", "path": str(root.resolve()), "fps": fps,
                "features": features, "cameras": cameras, "images": images,
                "arrays": {"observation.state": "state.npy", "action": "action.npy"},
                "episodes": [{"episode_id": 0, "num_frames": n}], "robot_type": "synthetic_demo",
                "fingerprint": {"method": "fixture", "value": "kuavo-editor-demo-v1"}}
    write_json(root / "catalog.json", manifest)
    return manifest
