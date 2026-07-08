from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

import numpy as np
from ultralytics import YOLO

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover
    ort = None


@dataclass
class PoseDetection:
    bbox: list[int]
    bbox_confidence: float
    keypoints: list[list[float]]
    pose_confidence_mean: float


def build_onnx_session_options(options: dict[str, Any] | None) -> Any | None:
    if not options:
        return None
    thread_count = options.get("intra_op_num_threads")
    if thread_count is not None and int(thread_count) not in {1, 2, 4}:
        raise ValueError("intra_op_num_threads must be one of 1, 2, or 4")
    allow_spinning = options.get("session.intra_op.allow_spinning")
    if allow_spinning is not None and str(allow_spinning) not in {"0", "1"}:
        raise ValueError("session.intra_op.allow_spinning must be '0' or '1'")
    if ort is None:
        raise RuntimeError("onnxruntime backend requested but package is not installed")

    session_options = ort.SessionOptions()
    if thread_count is not None:
        session_options.intra_op_num_threads = int(thread_count)
    graph_level = options.get("graph_optimization_level")
    if graph_level is not None:
        if str(graph_level) != "ORT_ENABLE_ALL":
            raise ValueError("graph_optimization_level must be ORT_ENABLE_ALL")
        session_options.graph_optimization_level = ort.ORT_ENABLE_ALL
    execution_mode = options.get("execution_mode")
    if execution_mode is not None:
        if str(execution_mode) != "ORT_SEQUENTIAL":
            raise ValueError("execution_mode must be ORT_SEQUENTIAL")
        session_options.execution_mode = ort.ORT_SEQUENTIAL
    if allow_spinning is not None:
        session_options.add_session_config_entry("session.intra_op.allow_spinning", str(allow_spinning))
    if bool(options.get("enable_profiling", False)):
        session_options.enable_profiling = True
    return session_options


def decode_yolo_pose_onnx_output(
    output: Any,
    *,
    frame_shape: tuple[int, int, int],
    imgsz: int,
    conf_threshold: float,
    max_people: int,
    min_pose_confidence: float = 0.0,
    nms_iou_threshold: float = 0.65,
    min_bbox_area_ratio: float = 0.0,
) -> list[PoseDetection]:
    array = np.asarray(output)
    if array.ndim == 3:
        array = array[0]
    if array.ndim != 2:
        return []
    if array.shape[0] in (56, 57) and array.shape[1] not in (56, 57):
        array = array.T
    if array.shape[1] < 56:
        return []

    frame_h, frame_w = frame_shape[:2]
    x_scale = float(frame_w) / float(max(imgsz, 1))
    y_scale = float(frame_h) / float(max(imgsz, 1))
    detections: list[PoseDetection] = []

    rows = sorted(array, key=lambda row: float(row[4]), reverse=True)
    for row in rows:
        confidence = float(row[4])
        if confidence < conf_threshold:
            continue

        if array.shape[1] >= 57:
            x1 = _clamp_int(round(float(row[0]) * x_scale), 0, frame_w)
            y1 = _clamp_int(round(float(row[1]) * y_scale), 0, frame_h)
            x2 = _clamp_int(round(float(row[2]) * x_scale), 0, frame_w)
            y2 = _clamp_int(round(float(row[3]) * y_scale), 0, frame_h)
            keypoint_base = 6
        else:
            cx = float(row[0]) * x_scale
            cy = float(row[1]) * y_scale
            width = float(row[2]) * x_scale
            height = float(row[3]) * y_scale
            x1 = _clamp_int(round(cx - width / 2.0), 0, frame_w)
            y1 = _clamp_int(round(cy - height / 2.0), 0, frame_h)
            x2 = _clamp_int(round(cx + width / 2.0), 0, frame_w)
            y2 = _clamp_int(round(cy + height / 2.0), 0, frame_h)
            keypoint_base = 5
        if x2 <= x1 or y2 <= y1:
            continue
        bbox_area_ratio = float((x2 - x1) * (y2 - y1)) / float(max(frame_w * frame_h, 1))
        if bbox_area_ratio < float(min_bbox_area_ratio):
            continue

        keypoints: list[list[float]] = []
        for index in range(17):
            base = keypoint_base + index * 3
            x = float(row[base]) * x_scale
            y = float(row[base + 1]) * y_scale
            kp_confidence = float(row[base + 2])
            keypoints.append([x, y, kp_confidence])

        valid_confidences = [kp[2] for kp in keypoints if kp[2] > 0]
        pose_confidence_mean = float(sum(valid_confidences) / max(len(valid_confidences), 1))
        if pose_confidence_mean < float(min_pose_confidence):
            continue
        if any(_bbox_iou([x1, y1, x2, y2], detection.bbox) > nms_iou_threshold for detection in detections):
            continue
        detections.append(
            PoseDetection(
                bbox=[x1, y1, x2, y2],
                bbox_confidence=confidence,
                keypoints=keypoints,
                pose_confidence_mean=pose_confidence_mean,
            )
        )
        if max_people > 0 and len(detections) >= max_people:
            break

    return detections


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


def _bbox_iou(box_a: list[int], box_b: list[int]) -> float:
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = max(0, box_a[2] - box_a[0]) * max(0, box_a[3] - box_a[1])
    area_b = max(0, box_b[2] - box_b[0]) * max(0, box_b[3] - box_b[1])
    union = area_a + area_b - intersection
    if union <= 0:
        return 0.0
    return float(intersection / union)


class YoloPoseEstimator:
    def __init__(self, config: dict[str, Any]) -> None:
        model_cfg = config["model"]
        self.model_name = model_cfg.get("model_name", "yolo26s-pose")
        self.model_path = Path(model_cfg["model_path"])
        requested_backend = str(model_cfg.get("backend", "auto")).lower()
        if requested_backend == "auto":
            requested_backend = "onnxruntime" if self.model_path.suffix.lower() == ".onnx" else "ultralytics"
        self.backend = requested_backend
        self.model = None
        self._onnx_session: Any | None = None
        self._onnx_input_name: str | None = None
        self.imgsz = int(config["model"].get("imgsz", 640))
        self.conf = float(config["model"].get("conf_threshold", 0.35))
        self.device = config["model"].get("device")
        self.max_people = int(config["model"].get("max_people", 1))
        self.min_pose_confidence = float(config["model"].get("min_pose_confidence", 0.0))
        self.nms_iou_threshold = float(config["model"].get("nms_iou_threshold", 0.65))
        self.min_bbox_area_ratio = float(config["model"].get("min_bbox_area_ratio", 0.0))
        self.last_timing_ms: dict[str, float] = {}
        if self.backend == "onnxruntime":
            if ort is None:
                raise RuntimeError("onnxruntime backend requested but package is not installed")
            providers = model_cfg.get("providers") or ["CPUExecutionProvider"]
            session_options = build_onnx_session_options(model_cfg.get("onnx_options"))
            if session_options is None:
                self._onnx_session = ort.InferenceSession(str(self.model_path), providers=providers)
            else:
                self._onnx_session = ort.InferenceSession(
                    str(self.model_path),
                    sess_options=session_options,
                    providers=providers,
                )
            self._onnx_input_name = self._onnx_session.get_inputs()[0].name
        else:
            self.model = YOLO(str(self.model_path))

    def predict(self, frame: np.ndarray) -> list[PoseDetection]:
        if self.backend == "onnxruntime":
            return self._predict_onnx(frame)

        if self.model is None:
            return []
        results = self.model.predict(
            frame,
            imgsz=self.imgsz,
            conf=self.conf,
            classes=[0],
            device=self.device,
            verbose=False,
        )
        if not results:
            return []

        result = results[0]
        if result.boxes is None or result.keypoints is None:
            return []

        boxes = result.boxes.xyxy.cpu().numpy()
        confidences = result.boxes.conf.cpu().numpy()
        keypoints = result.keypoints.data.cpu().numpy()

        candidates: list[PoseDetection] = []
        for bbox, conf, pose in zip(boxes, confidences, keypoints):
            pose_list = [[float(x), float(y), float(c)] for x, y, c in pose]
            valid_conf = [kp[2] for kp in pose_list if kp[2] > 0]
            pose_confidence_mean = float(sum(valid_conf) / max(len(valid_conf), 1))
            if pose_confidence_mean < self.min_pose_confidence:
                continue
            bbox_list = [int(v) for v in bbox.tolist()]
            bbox_area_ratio = float(max(0, bbox_list[2] - bbox_list[0]) * max(0, bbox_list[3] - bbox_list[1])) / float(max(frame.shape[0] * frame.shape[1], 1))
            if bbox_area_ratio < self.min_bbox_area_ratio:
                continue
            candidates.append(
                PoseDetection(
                    bbox=bbox_list,
                    bbox_confidence=float(conf),
                    keypoints=pose_list,
                    pose_confidence_mean=pose_confidence_mean,
                )
            )
        detections: list[PoseDetection] = []
        candidates.sort(key=lambda detection: detection.bbox_confidence * detection.pose_confidence_mean, reverse=True)
        for candidate in candidates:
            if any(_bbox_iou(candidate.bbox, detection.bbox) > self.nms_iou_threshold for detection in detections):
                continue
            detections.append(candidate)
            if self.max_people > 0 and len(detections) >= self.max_people:
                break
        return detections

    def _predict_onnx(self, frame: np.ndarray) -> list[PoseDetection]:
        if self._onnx_session is None or self._onnx_input_name is None:
            return []
        import cv2

        preprocess_started_at = time.perf_counter()
        resized = cv2.resize(frame, (self.imgsz, self.imgsz))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        tensor = rgb.astype(np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
        preprocess_ms = (time.perf_counter() - preprocess_started_at) * 1000.0
        onnx_started_at = time.perf_counter()
        outputs = self._onnx_session.run(None, {self._onnx_input_name: tensor})
        onnx_session_ms = (time.perf_counter() - onnx_started_at) * 1000.0
        decode_started_at = time.perf_counter()
        detections = decode_yolo_pose_onnx_output(
            outputs[0],
            frame_shape=frame.shape,
            imgsz=self.imgsz,
            conf_threshold=self.conf,
            max_people=self.max_people,
            min_pose_confidence=self.min_pose_confidence,
            nms_iou_threshold=self.nms_iou_threshold,
            min_bbox_area_ratio=self.min_bbox_area_ratio,
        )
        decode_ms = (time.perf_counter() - decode_started_at) * 1000.0
        self.last_timing_ms = {
            "preprocess_ms": preprocess_ms,
            "onnx_session_ms": onnx_session_ms,
            "decode_ms": decode_ms,
            "pose_total_ms": preprocess_ms + onnx_session_ms + decode_ms,
        }
        return detections
