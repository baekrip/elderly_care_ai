from __future__ import annotations

import math
from typing import Any

import numpy as np


KEYPOINT_NAMES = [
    "nose",
    "left_eye",
    "right_eye",
    "left_ear",
    "right_ear",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
]

FEATURE_COLUMNS = (
    [f"{name}_x_norm" for name in KEYPOINT_NAMES]
    + [f"{name}_y_norm" for name in KEYPOINT_NAMES]
    + [
        "bbox_center_x_norm",
        "bbox_center_y_norm",
        "bbox_width_norm",
        "bbox_height_norm",
        "bbox_aspect_ratio",
        "pose_confidence_mean",
        "visible_joint_ratio",
        "torso_angle_deg",
        "shoulder_tilt_deg",
        "hip_tilt_deg",
        "left_knee_angle_deg",
        "right_knee_angle_deg",
        "left_hip_angle_deg",
        "right_hip_angle_deg",
        "center_velocity_px_s",
        "vertical_velocity_px_s",
        "head_hip_y_diff",
        "torso_angle_delta",
    ]
)


def _angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    ba = a - b
    bc = c - b
    denom = np.linalg.norm(ba) * np.linalg.norm(bc)
    if denom <= 1e-6:
        return 0.0
    cosine = np.clip(np.dot(ba, bc) / denom, -1.0, 1.0)
    return float(np.degrees(np.arccos(cosine)))


class FeatureExtractor:
    def __init__(self, room_rois: dict[str, Any] | None = None) -> None:
        self.room_rois = dict(room_rois or {})
        self._previous_centers: dict[int, tuple[float, float, int]] = {}
        self._previous_hip_y: dict[int, tuple[float, int]] = {}
        self._previous_torso_angle: dict[int, float] = {}
        self._previous_velocity: dict[int, tuple[float, int]] = {}
        self._previous_acceleration: dict[int, float] = {}
        self._previous_keypoints: dict[int, tuple[np.ndarray, int]] = {}
        self._previous_bbox_area: dict[int, float] = {}
        self._static_since_ms: dict[int, int] = {}

    def extract(
        self,
        track_id: int,
        bbox: list[int],
        keypoints: list[list[float]],
        frame_shape: tuple[int, int, int],
        timestamp_ms: int,
    ) -> tuple[dict[str, Any], list[float]]:
        frame_h, frame_w = frame_shape[:2]
        points = np.asarray(keypoints, dtype=np.float32)
        coords = points[:, :2]
        confs = points[:, 2]

        features: dict[str, Any] = {}
        vector: list[float] = []

        for index, name in enumerate(KEYPOINT_NAMES):
            x_norm = float(coords[index][0] / max(frame_w, 1))
            y_norm = float(coords[index][1] / max(frame_h, 1))
            features[f"{name}_x_norm"] = x_norm
            features[f"{name}_y_norm"] = y_norm
            vector.extend([x_norm, y_norm])

        x1, y1, x2, y2 = bbox
        bbox_w = max(1, x2 - x1)
        bbox_h = max(1, y2 - y1)
        center_x = x1 + bbox_w / 2
        center_y = y1 + bbox_h / 2

        features.update(
            {
                "bbox_center_x_norm": center_x / max(frame_w, 1),
                "bbox_center_y_norm": center_y / max(frame_h, 1),
                "bbox_width_norm": bbox_w / max(frame_w, 1),
                "bbox_height_norm": bbox_h / max(frame_h, 1),
                "bbox_aspect_ratio": bbox_w / max(bbox_h, 1),
                "pose_confidence_mean": float(np.mean(confs)),
                "visible_joint_ratio": float(np.mean(confs > 0.3)),
                "visibility_ratio": float(np.mean(confs > 0.3)),
            }
        )
        features.update(_roi_overlap_features(bbox, frame_shape, self.room_rois))

        left_shoulder = coords[5]
        right_shoulder = coords[6]
        left_hip = coords[11]
        right_hip = coords[12]
        left_knee = coords[13]
        right_knee = coords[14]
        left_ankle = coords[15]
        right_ankle = coords[16]

        shoulder_mid = (left_shoulder + right_shoulder) / 2
        hip_mid = (left_hip + right_hip) / 2
        torso_vector = shoulder_mid - hip_mid

        features.update(
            {
                "torso_angle_deg": float(math.degrees(math.atan2(torso_vector[0], torso_vector[1] + 1e-6))),
                "shoulder_tilt_deg": float(math.degrees(math.atan2(right_shoulder[1] - left_shoulder[1], right_shoulder[0] - left_shoulder[0] + 1e-6))),
                "hip_tilt_deg": float(math.degrees(math.atan2(right_hip[1] - left_hip[1], right_hip[0] - left_hip[0] + 1e-6))),
                "left_knee_angle_deg": _angle(left_hip, left_knee, left_ankle),
                "right_knee_angle_deg": _angle(right_hip, right_knee, right_ankle),
                "left_hip_angle_deg": _angle(left_shoulder, left_hip, left_knee),
                "right_hip_angle_deg": _angle(right_shoulder, right_hip, right_knee),
            }
        )

        center_velocity_px_s = 0.0
        previous = self._previous_centers.get(track_id)
        if previous:
            prev_x, prev_y, prev_ts = previous
            dt_s = max((timestamp_ms - prev_ts) / 1000.0, 1e-3)
            center_velocity_px_s = float(np.linalg.norm([center_x - prev_x, center_y - prev_y]) / dt_s)
        self._previous_centers[track_id] = (center_x, center_y, timestamp_ms)
        features["center_velocity_px_s"] = center_velocity_px_s

        center_accel_px_s2 = 0.0
        prev_velocity = self._previous_velocity.get(track_id)
        if prev_velocity:
            prev_velocity_px_s, prev_ts = prev_velocity
            dt_s = max((timestamp_ms - prev_ts) / 1000.0, 1e-3)
            center_accel_px_s2 = float((center_velocity_px_s - prev_velocity_px_s) / dt_s)
        self._previous_velocity[track_id] = (center_velocity_px_s, timestamp_ms)
        features["center_accel_px_s2"] = center_accel_px_s2

        jerk_score = center_accel_px_s2 - self._previous_acceleration.get(track_id, center_accel_px_s2)
        self._previous_acceleration[track_id] = center_accel_px_s2
        features["jerk_score"] = float(abs(jerk_score))

        # Phase 5: vertical_velocity_px_s (hip center y descent speed)
        hip_y = float(hip_mid[1])
        vertical_velocity_px_s = 0.0
        prev_hip = self._previous_hip_y.get(track_id)
        if prev_hip:
            prev_hip_y, prev_ts = prev_hip
            dt_s = max((timestamp_ms - prev_ts) / 1000.0, 1e-3)
            vertical_velocity_px_s = float((hip_y - prev_hip_y) / dt_s)  # positive = descending
        self._previous_hip_y[track_id] = (hip_y, timestamp_ms)
        features["vertical_velocity_px_s"] = vertical_velocity_px_s

        # Phase 5: head_hip_y_diff (nose_y - hip_y; positive = head below hip)
        nose_y = float(coords[0][1])  # nose keypoint
        head_hip_y_diff = nose_y - hip_y
        features["head_hip_y_diff"] = head_hip_y_diff

        # Phase 5: torso_angle_delta (frame-to-frame torso change)
        current_torso = features["torso_angle_deg"]
        prev_torso = self._previous_torso_angle.get(track_id, current_torso)
        torso_angle_delta = current_torso - prev_torso
        self._previous_torso_angle[track_id] = current_torso
        features["torso_angle_delta"] = torso_angle_delta

        pose_delta_mean = 0.0
        motion_energy = 0.0
        instability_score = 0.0
        previous_keypoints = self._previous_keypoints.get(track_id)
        if previous_keypoints:
            prev_coords, prev_ts = previous_keypoints
            dt_s = max((timestamp_ms - prev_ts) / 1000.0, 1e-3)
            deltas = np.linalg.norm(coords - prev_coords, axis=1)
            pose_delta_mean = float(np.mean(deltas))
            motion_energy = float(np.mean(deltas ** 2) / dt_s)
            instability_score = float(np.std(deltas))
        self._previous_keypoints[track_id] = (coords.copy(), timestamp_ms)
        features["pose_delta_mean"] = pose_delta_mean
        features["motion_energy"] = motion_energy
        features["instability_score"] = instability_score

        bbox_area = float(bbox_w * bbox_h)
        prev_bbox_area = self._previous_bbox_area.get(track_id, bbox_area)
        features["bbox_area_change"] = float((bbox_area - prev_bbox_area) / max(prev_bbox_area, 1.0))
        self._previous_bbox_area[track_id] = bbox_area

        static_motion_threshold = 2.0
        if center_velocity_px_s <= static_motion_threshold and pose_delta_mean <= static_motion_threshold:
            self._static_since_ms.setdefault(track_id, timestamp_ms)
        else:
            self._static_since_ms[track_id] = timestamp_ms
        features["static_duration_ms"] = float(timestamp_ms - self._static_since_ms.get(track_id, timestamp_ms))

        vector.extend(
            [
                features["bbox_center_x_norm"],
                features["bbox_center_y_norm"],
                features["bbox_width_norm"],
                features["bbox_height_norm"],
                features["bbox_aspect_ratio"],
                features["pose_confidence_mean"],
                features["visible_joint_ratio"],
                features["torso_angle_deg"],
                features["shoulder_tilt_deg"],
                features["hip_tilt_deg"],
                features["left_knee_angle_deg"],
                features["right_knee_angle_deg"],
                features["left_hip_angle_deg"],
                features["right_hip_angle_deg"],
                center_velocity_px_s,
                vertical_velocity_px_s,
                head_hip_y_diff,
                torso_angle_delta,
            ]
        )

        return features, vector

    def cleanup_track(self, track_id: int) -> None:
        """Remove state for an expired track_id to prevent memory leak."""
        self._previous_centers.pop(track_id, None)
        self._previous_hip_y.pop(track_id, None)
        self._previous_torso_angle.pop(track_id, None)
        self._previous_velocity.pop(track_id, None)
        self._previous_acceleration.pop(track_id, None)
        self._previous_keypoints.pop(track_id, None)
        self._previous_bbox_area.pop(track_id, None)
        self._static_since_ms.pop(track_id, None)


def _roi_overlap_features(
    bbox: list[int],
    frame_shape: tuple[int, int, int],
    room_rois: dict[str, Any],
) -> dict[str, Any]:
    defaults = {
        "bed_roi_overlap": 0.0,
        "chair_roi_overlap": 0.0,
        "floor_roi_overlap": 0.0,
        "table_roi_overlap": 0.0,
        "bathroom_roi_overlap": 0.0,
        "dominant_roi": "",
    }
    if not room_rois:
        return defaults

    frame_h, frame_w = frame_shape[:2]
    overlaps: dict[str, float] = {}
    for name, raw_roi in room_rois.items():
        roi = _resolve_roi(raw_roi, frame_w, frame_h)
        if roi is None:
            continue
        overlap = _bbox_overlap_ratio(bbox, roi)
        overlaps[str(name)] = overlap
        defaults[f"{name}_roi_overlap"] = overlap
    if overlaps:
        dominant_name, dominant_value = max(overlaps.items(), key=lambda item: item[1])
        defaults["dominant_roi"] = dominant_name if dominant_value > 0.0 else ""
    return defaults


def _resolve_roi(raw_roi: Any, frame_w: int, frame_h: int) -> tuple[float, float, float, float] | None:
    if isinstance(raw_roi, dict):
        values = [raw_roi.get("x1"), raw_roi.get("y1"), raw_roi.get("x2"), raw_roi.get("y2")]
    elif isinstance(raw_roi, (list, tuple)) and len(raw_roi) == 4:
        values = list(raw_roi)
    else:
        return None
    try:
        x1, y1, x2, y2 = [float(value) for value in values]
    except (TypeError, ValueError):
        return None
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.0:
        x1 *= frame_w
        x2 *= frame_w
        y1 *= frame_h
        y2 *= frame_h
    left = max(0.0, min(x1, x2))
    top = max(0.0, min(y1, y2))
    right = min(float(frame_w), max(x1, x2))
    bottom = min(float(frame_h), max(y1, y2))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _bbox_overlap_ratio(bbox: list[int], roi: tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = [float(value) for value in bbox]
    bbox_w = max(0.0, x2 - x1)
    bbox_h = max(0.0, y2 - y1)
    bbox_area = bbox_w * bbox_h
    if bbox_area <= 0.0:
        return 0.0
    rx1, ry1, rx2, ry2 = roi
    inter_w = max(0.0, min(x2, rx2) - max(x1, rx1))
    inter_h = max(0.0, min(y2, ry2) - max(y1, ry1))
    return float((inter_w * inter_h) / bbox_area)
