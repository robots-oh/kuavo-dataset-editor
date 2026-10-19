"""Instance-local backpressure for the official real-time encoder.

Do not patch the installed package or replace its encoding/statistics logic.
The official producer queue becomes blocking, bounded, and cancellation-aware.
"""
import queue


class BackpressureQueue:
    def __init__(self, queue_, healthy, heartbeat):
        self.queue = queue_
        self.healthy, self.heartbeat = healthy, heartbeat

    def put(self, value, *args, **kwargs):
        # Ignore the real-time producer's timeout/drop policy. Keep bounded
        # storage, yielding periodically to cancellation and encoder liveness.
        while True:
            self.heartbeat()
            if not self.healthy():
                raise RuntimeError('영상 encoder가 종료되었습니다. 프레임을 버리거나 완료로 처리하지 않습니다.')
            try:
                return self.queue.put(value, timeout=0.1)
            except queue.Full:
                continue

    def __getattr__(self, name):
        return getattr(self.queue, name)


class LosslessStreamingEncoder:
    """Lossless here means no dropped frames, not a lossless RGB codec."""
    def __init__(self, encoder, heartbeat=lambda: None):
        required = ('start_episode', 'feed_frame', 'finish_episode', '_frame_queues', '_threads', '_dropped_frames')
        if any(not hasattr(encoder, key) for key in required):
            raise RuntimeError('공식 streaming encoder 계약이 바뀌었습니다. LeRobot 어댑터 갱신이 필요합니다.')
        self.encoder, self.heartbeat = encoder, heartbeat
        self.submitted, self.last_counts = {}, {}

    def start_episode(self, *args, **kwargs):
        self.encoder.start_episode(*args, **kwargs)
        self.submitted = {key: 0 for key in self.encoder._frame_queues}
        for key, queue_ in self.encoder._frame_queues.items():
            thread = self.encoder._threads[key]
            self.encoder._frame_queues[key] = BackpressureQueue(queue_, thread.is_alive, self.heartbeat)

    def feed_frame(self, key, image):
        self.encoder.feed_frame(key, image)
        self.submitted[key] += 1

    def finish_episode(self):
        if any(self.encoder._dropped_frames.values()):
            raise RuntimeError('encoder에서 프레임 누락이 감지됐습니다. 해당 episode는 checkpoint로 저장하지 않습니다.')
        threads = list(self.encoder._threads.values())
        result = self.encoder.finish_episode()
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError('영상 encoder 마감이 완료되지 않았습니다.')
        import av
        self.last_counts = {}
        for key, (path, _) in result.items():
            with av.open(str(path)) as container:
                count = container.streams.video[0].frames
                if not count:
                    count = sum(1 for _ in container.decode(video=0))
            if count != self.submitted[key]:
                raise RuntimeError(f'{key}: 입력 {self.submitted[key]} / 인코딩 {count} frames 불일치')
            self.last_counts[key] = count
        if set(result) != set(self.submitted):
            raise RuntimeError('일부 카메라의 encoder 결과가 없습니다.')
        return result

    def __getattr__(self, name):
        return getattr(self.encoder, name)

