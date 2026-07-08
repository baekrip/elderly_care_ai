from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from shared.protocol import SkeletonFrame


@dataclass(frozen=True)
class SkeletonWindow:
    camera_id: str
    track_id: int
    frames: list[SkeletonFrame]
    ready: bool


class SkeletonSequenceBuffer:
    def __init__(self, *, window_ms: int = 5_000, min_frames: int = 8) -> None:
        self.window_ms = int(window_ms)
        self.min_frames = int(min_frames)
        self._frames: dict[tuple[str, int], deque[SkeletonFrame]] = defaultdict(deque)

    def add(self, frame: SkeletonFrame) -> SkeletonWindow:
        key = (frame.camera_id, frame.track_id)
        bucket = self._frames[key]
        bucket.append(frame)
        self._trim(bucket, frame.timestamp_ms)
        return self.latest_window(frame.camera_id, frame.track_id)

    def latest_window(self, camera_id: str, track_id: int) -> SkeletonWindow:
        key = (camera_id, track_id)
        frames = list(self._frames.get(key, []))
        return SkeletonWindow(
            camera_id=camera_id,
            track_id=track_id,
            frames=frames,
            ready=len(frames) >= self.min_frames,
        )

    def _trim(self, bucket: deque[SkeletonFrame], now_ms: int) -> None:
        cutoff = int(now_ms) - self.window_ms
        while bucket and bucket[0].timestamp_ms < cutoff:
            bucket.popleft()
