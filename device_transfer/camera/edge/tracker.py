from __future__ import annotations

from dataclasses import dataclass
import math


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
    def __init__(
        self,
        max_age_ms: int = 1_500,
        iou_threshold: float = 0.25,
        max_center_shift_ratio: float = 0.75,
        max_area_ratio: float = 2.5,
    ) -> None:
        self.max_age_ms = max_age_ms
        self.iou_threshold = iou_threshold
        self.max_center_shift_ratio = max(0.0, float(max_center_shift_ratio))
        self.max_area_ratio = max(1.0, float(max_area_ratio))
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
                if score >= self.iou_threshold:
                    candidate_score = score
                elif self._plausible_continuation(box, track.bbox):
                    candidate_score = self.iou_threshold
                else:
                    continue
                if candidate_score > best_score:
                    best_score = candidate_score
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

    def _plausible_continuation(
        self,
        current_box: list[int],
        previous_box: list[int],
    ) -> bool:
        previous_width = max(1, previous_box[2] - previous_box[0])
        previous_height = max(1, previous_box[3] - previous_box[1])
        current_width = max(1, current_box[2] - current_box[0])
        current_height = max(1, current_box[3] - current_box[1])
        previous_center = (
            (previous_box[0] + previous_box[2]) / 2.0,
            (previous_box[1] + previous_box[3]) / 2.0,
        )
        current_center = (
            (current_box[0] + current_box[2]) / 2.0,
            (current_box[1] + current_box[3]) / 2.0,
        )
        center_distance = math.hypot(
            current_center[0] - previous_center[0],
            current_center[1] - previous_center[1],
        )
        reference_extent = max(
            previous_width,
            previous_height,
            current_width,
            current_height,
        )
        if center_distance > self.max_center_shift_ratio * reference_extent:
            return False
        previous_area = previous_width * previous_height
        current_area = current_width * current_height
        area_ratio = max(previous_area, current_area) / max(
            min(previous_area, current_area),
            1,
        )
        return area_ratio <= self.max_area_ratio

    def _prune(self, timestamp_ms: int) -> None:
        expired = [track_id for track_id, state in self._tracks.items() if timestamp_ms - state.timestamp_ms > self.max_age_ms]
        for track_id in expired:
            del self._tracks[track_id]
