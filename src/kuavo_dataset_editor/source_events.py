"""Decode provenance events only on demand, never duplicate the raw bag at import."""
import json
from pathlib import Path

from .storage import dataset_event_files, open_events


def source_events(adapter, intervals=None):
    """Yield (event, dataset_coordinates). Legacy JSON catalogs are unchanged.

    Deferred bags remain the lossless record of *all* observations. Intervals
    restrict exported provenance only, not whole-recording rack reconstruction.
    """
    from .sources import CatalogSource, fingerprint
    manifest = getattr(adapter, 'manifest', {})
    deferred = manifest.get('events_source') if isinstance(adapter, CatalogSource) else None
    if deferred:
        from .bag import plain
        from .ros1_reader import Ros1Reader
        path = Path(manifest['original_path'])
        if fingerprint(path) != manifest['fingerprint']:
            raise ValueError('원본 bag이 Import 이후 변경되었습니다. 이벤트를 다른 원본과 섞지 않습니다.')
        topics = set(deferred['topics'])
        if not topics:
            return
        with Ros1Reader(path) as reader:
            origin = int(manifest['origin_bag_time_ns'])
            start = origin + int(min(a for a, _ in intervals)*1e9) if intervals else None
            # Expand 1 ns to avoid float truncation at an exact frame boundary;
            # final inclusion uses the same float interval test as legacy JSON.
            stop = origin + int(max(b for _, b in intervals)*1e9) + 1 if intervals else None
            connections = [c for c in reader.connections if c.topic in topics]
            if not connections:
                return
            for connection, stamp, raw in reader.messages(connections=connections, start=start, stop=stop):
                t = (int(stamp)-origin)/1e9
                if intervals and not any(a <= t < b for a, b in intervals):
                    continue
                yield {'topic': connection.topic, 'bag_time_ns': int(stamp), 'source_time_s': t,
                       'message': plain(reader.deserialize(raw, connection.msgtype))}, False
        if fingerprint(path) != manifest['fingerprint']:
            raise ValueError('이벤트 처리 도중 원본 bag이 변경되었습니다.')
        return
    if isinstance(adapter, CatalogSource) and manifest.get('events'):
        paths, coordinates = [adapter.root / manifest['events']], False
    else:
        paths, coordinates = dataset_event_files(adapter.root / 'editor'), True
    for path in paths:
        with open_events(path) as stream:
            for line in stream:
                yield json.loads(line), coordinates
