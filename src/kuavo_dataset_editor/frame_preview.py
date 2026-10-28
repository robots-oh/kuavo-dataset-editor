"""Fast display-only JPEG responses with a bounded memory cache, never disk."""
from collections import OrderedDict
import io
import threading

from PIL import Image

from .image_store import child, open_image, unpack
from .sources import CatalogSource, file_stamp


class PreviewCache:
    def __init__(self, budget=64*1024**2):
        self.budget, self.size = budget, 0
        self.values, self.lock = OrderedDict(), threading.RLock()

    def get(self, key):
        with self.lock:
            if key in self.values:
                self.values.move_to_end(key)
                return self.values[key]

    def put(self, key, value):
        with self.lock:
            old = self.values.pop(key, None)
            if old is not None:
                self.size -= len(old)
            if len(value) > self.budget:
                return value
            while self.values and (self.size+len(value) > self.budget or len(self.values) >= 512):
                self.size -= len(self.values.popitem(last=False)[1])
            self.values[key] = value
            self.size += len(value)
        return value


_cache = PreviewCache()


def jpeg_preview(adapter, episode, frame, camera, playback=False):
    bounds = (640, 360) if playback else (960, 540)
    byte_limit = 40*1024 if playback else 80*1024
    key = None
    if isinstance(adapter, CatalogSource):
        if episode != 0 or not 0 <= frame < adapter.manifest['episodes'][0]['num_frames']:
            raise ValueError('프레임 범위 밖입니다.')
        ref = adapter.manifest['images'][camera][frame]
        if ref is None:
            raise ValueError(f'{camera} frame {frame}: 영상 누락')
        packed = unpack(ref)
        artifact = child(adapter.root, packed[0] if packed else ref)
        artifact_stamp = file_stamp(artifact)
        key = (str(adapter.root), file_stamp(adapter.root/'catalog.json'), ref, artifact_stamp, playback)
        saved = _cache.get(key)
        if saved is not None:
            return saved
        with open_image(adapter.root, ref) as image:
            size = packed[2] if packed else artifact_stamp[2]
            if (image.format == 'JPEG' and image.mode == 'RGB' and image.width <= bounds[0] and image.height <= bounds[1]
                    and size <= byte_limit
                    and image.getexif().get(274, 1) == 1):
                image.fp.seek(0)
                return _cache.put(key, image.fp.read())
            image = image.convert('RGB')
    else:
        image = Image.fromarray(adapter.row(episode, frame, camera_key=camera)[camera]).convert('RGB')
    with image:
        image.thumbnail(bounds)
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=75 if playback else 82)
    data = output.getvalue()
    return _cache.put(key, data) if key is not None else data
