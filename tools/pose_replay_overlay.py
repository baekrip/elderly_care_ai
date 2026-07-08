from __future__ import annotations

import cv2
import numpy as np


SKELETON_PAIRS: tuple[tuple[int, int], ...] = (
    (0, 1),
    (0, 2),
    (1, 3),
    (2, 4),
    (5, 6),
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
)


def draw_pose_overlay(
    frame: np.ndarray,
    *,
    bbox: list[int] | None,
    keypoints: list[list[float]],
    expected_label: str,
    action_label: str,
    risk_label: str,
    frame_index: int,
) -> np.ndarray:
    canvas = frame.copy()
    if bbox is not None:
        x1, y1, x2, y2 = bbox
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 210, 255), 2, cv2.LINE_AA)
    points = [(int(kp[0]), int(kp[1]), float(kp[2])) for kp in keypoints]
    for first, second in SKELETON_PAIRS:
        if first < len(points) and second < len(points):
            first_point = points[first]
            second_point = points[second]
            if first_point[2] >= 0.30 and second_point[2] >= 0.30:
                cv2.line(canvas, first_point[:2], second_point[:2], (0, 220, 0), 2, cv2.LINE_AA)
    for x_pos, y_pos, confidence in points:
        if confidence >= 0.30:
            cv2.circle(canvas, (x_pos, y_pos), 4, (0, 255, 0), -1, cv2.LINE_AA)
    text = f"{expected_label} action={action_label} risk={risk_label} frame={frame_index}"
    cv2.putText(canvas, text, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(canvas, text, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return canvas
