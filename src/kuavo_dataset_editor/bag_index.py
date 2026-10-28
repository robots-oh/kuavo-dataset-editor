"""Bounded RAM-only metadata snapshots for the pinned rosbags 0.11 reader.

Snapshots contain no open handles/chunk payloads. Each reader owns its file and
mutable containers; only immutable index entries are shared. File identity changes
invalidate snapshots, and message-definition/MD5 validation still runs per reader.
"""
from bisect import bisect_left
from collections import OrderedDict
import os
import threading


_cache, _lock = OrderedDict(), threading.RLock()
_fields = ('connections', 'indexes', 'chunk_infos', 'chunks', 'index_data_header_offsets')


def stamp(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def clear_index_cache():
    with _lock:
        _cache.clear()


def open_index(reader):
    try:
        budget = max(0, min(1024, int(os.environ.get('EDITOR_BAG_INDEX_CACHE_MB', '256'))))*1024**2
    except ValueError:
        budget = 256*1024**2
    if not budget:
        clear_index_cache()
        reader.open()
        return
    if not all(hasattr(reader, name) for name in _fields):
        reader.open()
        return
    key = str(reader.path.resolve()), stamp(reader.path.stat())
    with _lock:
        while _cache and sum(v[0] for v in _cache.values()) > budget:
            _cache.popitem(last=False)
        saved = _cache.get(key)
        if saved:
            reader.bio = reader.path.open('rb')
            if stamp(os.fstat(reader.bio.fileno())) == key[1]:
                values = saved[1]
                reader.connections = list(values[0])
                reader.indexes = dict(values[1])
                reader.chunk_infos = list(values[2])
                reader.chunks = dict(values[3])
                reader.index_data_header_offsets = values[4]
                _cache.move_to_end(key)
                return
            reader.bio.close()
            reader.bio = None
        reader.open()
        if stamp(reader.path.stat()) != key[1]:
            return
        # Conservative estimate includes Python integer/tuple/list overhead.
        size = sum(len(v) for v in reader.indexes.values())*160 + len(reader.chunks)*512
        if size > budget:
            return
        # Old versions of this exact pathname are not useful after replacement.
        for old in list(_cache):
            if old[0] == key[0]:
                del _cache[old]
        while _cache and (len(_cache) >= 2 or sum(v[0] for v in _cache.values())+size > budget):
            _cache.popitem(last=False)
        _cache[key] = (size, (list(reader.connections), dict(reader.indexes),
                             list(reader.chunk_infos), dict(reader.chunks), reader.index_data_header_offsets))


class IndexWindow:
    """O(log N) seek without copying or merging all messages before a cursor."""
    def __init__(self, entries, start, stop):
        self.entries = entries
        self.start = bisect_left(entries, start, key=lambda e: e.time) if start is not None else 0
        self.stop = bisect_left(entries, stop, key=lambda e: e.time) if stop is not None else len(entries)

    def __len__(self):
        return max(0, self.stop-self.start)

    def __iter__(self):
        for i in range(self.start, self.stop):
            yield self.entries[i]

    def __getitem__(self, index):
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self.entries[self.start+index]
