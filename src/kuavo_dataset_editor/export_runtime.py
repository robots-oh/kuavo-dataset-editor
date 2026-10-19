"""Bounded, ordered decode prefetch; only the caller writes LeRobot metadata."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import asdict, dataclass
import os
import math


def _setting(name, default, maximum):
    value = int(os.environ.get(name, default))
    if not 1 <= value <= maximum:
        raise ValueError(f'{name}은 1~{maximum} 범위여야 합니다.')
    return value


@dataclass(frozen=True)
class ExportRuntime:
    cpu_budget: int
    decode_workers: int
    prefetch_frames: int
    encoder_threads: int
    encoder_queue_frames: int

    @classmethod
    def choose(cls, features):
        try:
            cpus = len(os.sched_getaffinity(0))
        except AttributeError:
            cpus = os.cpu_count() or 1
        budget = _setting('EDITOR_EXPORT_CPU_BUDGET', max(1, min(8, cpus // 2)), max(1, cpus))
        cameras = max(1, sum(ft['dtype'] in ('image', 'video') for ft in features.values()))
        workers = _setting('EDITOR_EXPORT_DECODE_WORKERS', max(1, min(4, budget // 3)), max(1, cpus))
        # Bound decoded RGB, not just the count of futures, to roughly 64 MiB.
        frame_bytes = sum(max(1, math.prod(ft['shape'])) for ft in features.values()
                          if ft['dtype'] in ('image', 'video')) or 1
        window = max(1, min(8, 64 * 1024**2 // frame_bytes))
        prefetch = min(window, _setting('EDITOR_EXPORT_PREFETCH_FRAMES', min(window, workers * 2), 32))
        threads = _setting('EDITOR_EXPORT_ENCODER_THREADS', max(1, min(2, (budget-workers)//cameras)), 16)
        # Each camera queue retains RGB copies too; about 48 MiB total by default.
        queue_size = max(1, min(8, 48 * 1024**2 // frame_bytes))
        queue_size = _setting('EDITOR_EXPORT_ENCODER_QUEUE_FRAMES', queue_size, 32)
        return cls(budget, min(workers, prefetch), prefetch, threads, queue_size)

    def describe(self):
        return asdict(self)


def prefetch_ordered(items, read, runtime, heartbeat=lambda: None):
    """Never drop/reorder an item. At most `prefetch_frames` decoded rows live here."""
    iterator, pending = iter(items), deque()
    pool = ThreadPoolExecutor(max_workers=runtime.decode_workers, thread_name_prefix='editor-decode')
    try:
        for _ in range(runtime.prefetch_frames):
            item = next(iterator, None)
            if item is None:
                break
            pending.append((item, pool.submit(read, item)))
        while pending:
            item, future = pending.popleft()
            while True:
                heartbeat()
                try:
                    result = future.result(timeout=0.1)
                    break
                except TimeoutError:
                    if future.done():
                        result = future.result()  # Propagate decoder errors; allow a completion race.
                        break
            yield item, result
            del result
            following = next(iterator, None)
            if following is not None:
                pending.append((following, pool.submit(read, following)))
    finally:
        for _, future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
