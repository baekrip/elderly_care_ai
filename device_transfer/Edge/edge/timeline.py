from __future__ import annotations

from dataclasses import dataclass

from shared.protocol import TimelineSegment


@dataclass
class ActiveSegment:
    camera_id: str
    room_id: str | None
    track_id: int
    action_label: str
    action_confidence: float
    started_at_ms: int
    ended_at_ms: int
    summary_features: dict


class TimelineBuilder:
    def __init__(self, max_idle_ms: int = 3_000) -> None:
        self.max_idle_ms = max_idle_ms
        self._segments: dict[int, ActiveSegment] = {}

    def update(
        self,
        camera_id: str,
        room_id: str | None,
        track_id: int,
        action_label: str,
        action_confidence: float,
        timestamp_ms: int,
        summary_features: dict,
    ) -> TimelineSegment | None:
        current = self._segments.get(track_id)
        if current is None:
            self._segments[track_id] = ActiveSegment(
                camera_id=camera_id,
                room_id=room_id,
                track_id=track_id,
                action_label=action_label,
                action_confidence=action_confidence,
                started_at_ms=timestamp_ms,
                ended_at_ms=timestamp_ms,
                summary_features=summary_features,
            )
            return None

        if current.action_label == action_label:
            current.ended_at_ms = timestamp_ms
            current.action_confidence = max(current.action_confidence, action_confidence)
            current.summary_features = summary_features
            return None

        completed = TimelineSegment(
            camera_id=current.camera_id,
            room_id=current.room_id,
            track_id=current.track_id,
            action_label=current.action_label,
            action_confidence=current.action_confidence,
            started_at_ms=current.started_at_ms,
            ended_at_ms=current.ended_at_ms,
            duration_ms=current.ended_at_ms - current.started_at_ms,
            summary_features=current.summary_features,
        )
        self._segments[track_id] = ActiveSegment(
            camera_id=camera_id,
            room_id=room_id,
            track_id=track_id,
            action_label=action_label,
            action_confidence=action_confidence,
            started_at_ms=timestamp_ms,
            ended_at_ms=timestamp_ms,
            summary_features=summary_features,
        )
        return completed

    def flush_stale(self, timestamp_ms: int) -> list[TimelineSegment]:
        finished: list[TimelineSegment] = []
        stale_track_ids = [
            track_id
            for track_id, segment in self._segments.items()
            if timestamp_ms - segment.ended_at_ms > self.max_idle_ms
        ]
        for track_id in stale_track_ids:
            current = self._segments.pop(track_id)
            finished.append(
                TimelineSegment(
                    camera_id=current.camera_id,
                    room_id=current.room_id,
                    track_id=current.track_id,
                    action_label=current.action_label,
                    action_confidence=current.action_confidence,
                    started_at_ms=current.started_at_ms,
                    ended_at_ms=current.ended_at_ms,
                    duration_ms=current.ended_at_ms - current.started_at_ms,
                    summary_features=current.summary_features,
                )
            )
        return finished

    def flush_all(self) -> list[TimelineSegment]:
        finished: list[TimelineSegment] = []
        track_ids = list(self._segments.keys())
        for track_id in track_ids:
            current = self._segments.pop(track_id)
            finished.append(
                TimelineSegment(
                    camera_id=current.camera_id,
                    room_id=current.room_id,
                    track_id=current.track_id,
                    action_label=current.action_label,
                    action_confidence=current.action_confidence,
                    started_at_ms=current.started_at_ms,
                    ended_at_ms=current.ended_at_ms,
                    duration_ms=current.ended_at_ms - current.started_at_ms,
                    summary_features=current.summary_features,
                )
            )
        return finished
