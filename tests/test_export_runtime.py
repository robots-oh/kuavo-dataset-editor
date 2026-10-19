import hashlib
import queue
import threading
import time

import pytest

from kuavo_dataset_editor.export_runtime import ExportRuntime, prefetch_ordered
from kuavo_dataset_editor.integrations.streaming import BackpressureQueue, LosslessStreamingEncoder
from kuavo_dataset_editor.sources import fingerprint


def test_encoder_queue_waits_past_realtime_drop_timeout():
    raw = queue.Queue(maxsize=1)
    raw.put('first')
    checked = []
    wrapped = BackpressureQueue(raw, lambda: True, lambda: checked.append(True))
    producer = threading.Thread(target=lambda: wrapped.put('second', timeout=.1))
    producer.start()
    try:
        time.sleep(.22)  # Official producer drops a frame after .1 seconds.
        assert producer.is_alive()
        assert raw.get_nowait() == 'first'
        producer.join(timeout=2)
        assert not producer.is_alive() and raw.get_nowait() == 'second'
        assert len(checked) >= 2
    finally:
        if producer.is_alive():
            raw.get_nowait()
            producer.join(timeout=2)


def test_encoder_wait_checks_cancellation_and_dead_encoder():
    with pytest.raises(RuntimeError, match='종료'):
        BackpressureQueue(queue.Queue(1), lambda: False, lambda: None).put('frame')
    class Cancelled(Exception): pass
    def cancel(): raise Cancelled()
    with pytest.raises(Cancelled):
        BackpressureQueue(queue.Queue(1), lambda: True, cancel).put('frame')


def test_encoder_frame_count_mismatch_is_not_accepted(monkeypatch):
    from types import SimpleNamespace
    import av
    class Encoder:
        _frame_queues, _threads, _dropped_frames = {}, {}, {}
        def start_episode(self): pass
        def feed_frame(self, key, image): pass
        def finish_episode(self): return {'camera': ('fixture.mp4', None)}
    class Container:
        streams = SimpleNamespace(video=[SimpleNamespace(frames=1)])
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(av, 'open', lambda _: Container())
    encoder = LosslessStreamingEncoder(Encoder())
    encoder.submitted = {'camera': 2}
    with pytest.raises(RuntimeError, match='입력 2 / 인코딩 1'):
        encoder.finish_episode()


def test_prefetch_is_ordered_bounded_and_parallel():
    runtime = ExportRuntime(4, 2, 3, 1, 2)
    active, peak = 0, 0
    lock = threading.Lock()
    def read(index):
        nonlocal active, peak
        with lock:
            active += 1; peak = max(active, peak)
        time.sleep(.02 if index % 2 == 0 else .001)
        with lock: active -= 1
        return index * 2
    rows = list(prefetch_ordered(range(8), read, runtime))
    assert rows == [(i, i * 2) for i in range(8)]
    assert peak == 2 and active == 0


def test_runtime_bounds_memory_and_honors_explicit_settings(monkeypatch):
    monkeypatch.setenv('EDITOR_EXPORT_CPU_BUDGET', '1')
    monkeypatch.setenv('EDITOR_EXPORT_DECODE_WORKERS', '2')
    monkeypatch.setenv('EDITOR_EXPORT_PREFETCH_FRAMES', '32')
    runtime = ExportRuntime.choose({'camera': {'dtype': 'video', 'shape': [2160, 3840, 3]}})
    assert runtime.prefetch_frames == 2 and runtime.decode_workers == 2
    monkeypatch.setenv('EDITOR_EXPORT_PREFETCH_FRAMES', '0')
    with pytest.raises(ValueError): ExportRuntime.choose({})


def test_faster_fingerprint_preserves_existing_identity(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a/z').write_bytes(b'child')
    (tmp_path / 'a.txt').write_bytes(b'parent')
    (tmp_path / 'empty').mkdir()
    expected = hashlib.sha256()
    for path in sorted(p for p in tmp_path.rglob('*') if p.is_file()):
        stat = path.stat()
        expected.update(f'{path.relative_to(tmp_path)}:{stat.st_size}:{stat.st_mtime_ns}\n'.encode())
    assert fingerprint(tmp_path)['value'] == expected.hexdigest()
