from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClipWindow:
    center_ms: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def pre_ms(self) -> int:
        return max(0, self.center_ms - self.start_ms)

    @property
    def post_ms(self) -> int:
        return max(0, self.end_ms - self.center_ms)


def resolve_clip_window(
    *,
    center_ts_ms: int,
    pre_ms: int,
    post_ms: int,
    explicit_start_ms: int | None = None,
    explicit_end_ms: int | None = None,
    max_duration_ms: int | None = None,
) -> ClipWindow:
    center = int(center_ts_ms)
    start = int(explicit_start_ms) if explicit_start_ms is not None else center - int(pre_ms)
    end = int(explicit_end_ms) if explicit_end_ms is not None else center + int(post_ms)
    if end < start:
        start, end = end, start

    if max_duration_ms is not None and max_duration_ms > 0 and end - start > int(max_duration_ms):
        half = int(max_duration_ms) // 2
        start = center - half
        end = start + int(max_duration_ms)

    return ClipWindow(center_ms=center, start_ms=start, end_ms=end)
