from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def _head_roi_from_pose(
    frame_shape: tuple[int, int, int],
    keypoints: list[list[float]],
    bbox: list[int] | None,
) -> tuple[int, int, int, int]:
    height, width = frame_shape[:2]
    confident = [(float(kp[0]), float(kp[1])) for kp in keypoints[:5] if len(kp) >= 3 and float(kp[2]) > 0.2]
    if confident:
        xs = [point[0] for point in confident]
        ys = [point[1] for point in confident]
        pad = max(12, int((max(xs) - min(xs) + max(ys) - min(ys) + 1) * 0.75))
        x1 = int(max(min(xs) - pad, 0))
        y1 = int(max(min(ys) - pad, 0))
        x2 = int(min(max(xs) + pad, width))
        y2 = int(min(max(ys) + pad, height))
        return x1, y1, x2, y2
    if bbox is not None and len(bbox) >= 4:
        x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
        head_h = max(1, int((y2 - y1) * 0.3))
        return max(x1, 0), max(y1, 0), min(x2, width), min(y1 + head_h, height)
    return 0, 0, min(width, 1), min(height, 1)


def blur_frame_head_roi(
    frame: np.ndarray,
    keypoints: list[list[float]],
    bbox: list[int] | None = None,
    *,
    kernel_size: int = 31,
) -> np.ndarray:
    output = frame.copy()
    x1, y1, x2, y2 = _head_roi_from_pose(output.shape, keypoints, bbox)
    if x2 <= x1 or y2 <= y1:
        return output
    kernel = max(3, int(kernel_size))
    if kernel % 2 == 0:
        kernel += 1
    roi = output[y1:y2, x1:x2]
    output[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (kernel, kernel), 0)
    return output


def blur_video_file(
    input_path: Path,
    output_path: Path,
    *,
    keypoints_by_frame: list[list[list[float]]] | None = None,
    bbox_by_frame: list[list[int]] | None = None,
) -> Path:
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open clip for blur: {input_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 10.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    frame_idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            kps = keypoints_by_frame[frame_idx] if keypoints_by_frame and frame_idx < len(keypoints_by_frame) else []
            bbox = bbox_by_frame[frame_idx] if bbox_by_frame and frame_idx < len(bbox_by_frame) else None
            writer.write(blur_frame_head_roi(frame, kps, bbox))
            frame_idx += 1
    finally:
        cap.release()
        writer.release()
    return output_path
