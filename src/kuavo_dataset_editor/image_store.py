"""Lossless, append-only compressed frame packs; legacy image paths still work."""
import io
from pathlib import Path

from PIL import Image


PREFIX = 'packed:'


def child(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError('image path escapes catalog')
    return path


def unpack(reference):
    if not isinstance(reference, str) or not reference.startswith(PREFIX):
        return None
    _, name, offset, length = reference.split(':')
    offset, length = int(offset), int(length)
    if offset < 0 or not 0 < length <= 512 * 1024 * 1024:
        raise ValueError('invalid packed frame bounds')
    return name, offset, length


def image_present(root, reference, sizes=None):
    if reference is None:
        return False
    try:
        packed = unpack(reference)
        name = packed[0] if packed else reference
        if sizes is None:
            path = child(root, name)
            size = path.stat().st_size if path.is_file() else -1
        else:
            if name not in sizes:
                path = child(root, name)
                sizes[name] = path.stat().st_size if path.is_file() else -1
            size = sizes[name]
        return size >= packed[1] + packed[2] if packed else size > 0
    except (OSError, ValueError, TypeError):
        return False


def open_image(root, reference):
    packed = unpack(reference)
    if packed is None:
        return Image.open(child(root, reference))
    name, offset, length = packed
    with child(root, name).open('rb') as stream:
        stream.seek(offset)
        data = stream.read(length)
    if len(data) != length:
        raise ValueError('packed image is truncated')
    return Image.open(io.BytesIO(data))


class FramePacks:
    def __init__(self, root):
        self.root, self.streams = Path(root), {}
        self.frames = 0
        self.bytes = 0

    def __enter__(self):
        return self

    def append(self, camera_id, data):
        name = f'camera_{camera_id}.frames'
        if name not in self.streams:
            self.streams[name] = (self.root / name).open('xb', buffering=4*1024*1024)
        stream = self.streams[name]
        offset = stream.tell()
        stream.write(data)
        self.frames += 1
        self.bytes += len(data)
        return f'{PREFIX}{name}:{offset}:{len(data)}'

    def __exit__(self, *_):
        for stream in self.streams.values():
            stream.close()
