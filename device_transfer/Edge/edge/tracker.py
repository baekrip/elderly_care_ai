from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrackState:
    track_id: int
    bbox: list[int]
    timestamp_ms: int


def _iou(box_a: list[int], box_b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    inter = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1, (bx2 - bx1) * (by2 - by1))
    return inter / max(area_a + area_b - inter, 1)


class SimpleTracker:
    def __init__(self, max_age_ms: int = 1_500, iou_threshold: float = 0.25) -> None:
        self.max_age_ms = max_age_ms
        self.iou_threshold = iou_threshold
        self._next_id = 1
        self._tracks: dict[int, TrackState] = {}

    def update(self, boxes: list[list[int]], timestamp_ms: int) -> list[int]:
        self._prune(timestamp_ms)
        assignments: list[int] = []
        used: set[int] = set()

        for box in boxes:
            best_track = None
            best_score = 0.0
            for track_id, track in self._tracks.items():
                if track_id in used:
                    continue
                score = _iou(box, track.bbox)
                if score > best_score:
                    best_score = score
                    best_track = track_id

            if best_track is not None and best_score >= self.iou_threshold:
                self._tracks[best_track] = TrackState(best_track, box, timestamp_ms)
                assignments.append(best_track)
                used.add(best_track)
                continue

            track_id = self._next_id
            self._next_id += 1
            self._tracks[track_id] = TrackState(track_id, box, timestamp_ms)
            assignments.append(track_id)
            used.add(track_id)
        return assignments

    def _prune(self, timestamp_ms: int) -> None:
        expired = [track_id for track_id, state in self._tracks.items() if timestamp_ms - state.timestamp_ms > self.max_age_ms]
        for track_id in expired:
            del self._tracks[track_id]
