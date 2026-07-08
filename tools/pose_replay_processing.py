from __future__ import annotations

import copy
import math
import statistics
import time
from pathlib import Path

import cv2
import numpy as np

from edge.action_classifier import ActionClassifier
from edge.config import load_config
from edge.feature_extractor import FeatureExtractor
from edge.pose_estimator import YoloPoseEstimator
from edge.tier_classifier import TierClassifier
from edge.trigger_engine import TriggerEngine
from tools.pose_replay_benchmark import (
    ResolutionMetrics,
    SelectedVideo,
    VideoResolutionResult,
    evaluate_resolution_gate,
)
from tools.pose_replay_overlay import draw_pose_overlay


def benchmark_selected_videos(
    *,
    selected: tuple[SelectedVideo, ...],
    config_path: Path,
    resolutions: tuple[tuple[int, int], ...],
    samples_dir: Path,
    pose_model_path: Path | None = None,
    pose_backend: str | None = None,
    pose_imgsz: int | None = None,
    pose_conf_threshold: float | None = None,
) -> tuple[VideoResolutionResult, ...]:
    results: list[VideoResolutionResult] = []
    base_config = load_config(config_path)
    for width, height in resolutions:
        config = copy.deepcopy(base_config)
        config["camera"]["processing_resolution"] = [width, height]
        apply_pose_overrides(
            config,
            pose_model_path=pose_model_path,
            pose_backend=pose_backend,
            pose_imgsz=pose_imgsz,
            pose_conf_threshold=pose_conf_threshold,
        )
        pose_estimator = YoloPoseEstimator(config)
        resolution = f"{width}x{height}"
        for video in selected:
            results.append(
                benchmark_one_video(
                    video=video,
                    config=copy.deepcopy(config),
                    pose_estimator=pose_estimator,
                    resolution=resolution,
                    size=(width, height),
                    samples_dir=samples_dir,
                )
            )
    return tuple(results)


def apply_pose_overrides(
    config: dict,
    *,
    pose_model_path: Path | None,
    pose_backend: str | None,
    pose_imgsz: int | None,
    pose_conf_threshold: float | None,
) -> None:
    model_config = config["model"]
    if pose_model_path is not None:
        model_config["model_path"] = str(pose_model_path)
    if pose_backend is not None:
        model_config["backend"] = pose_backend
    if pose_imgsz is not None:
        model_config["imgsz"] = pose_imgsz
    if pose_conf_threshold is not None:
        model_config["conf_threshold"] = pose_conf_threshold


def benchmark_one_video(
    *,
    video: SelectedVideo,
    config: dict,
    pose_estimator: YoloPoseEstimator,
    resolution: str,
    size: tuple[int, int],
    samples_dir: Path,
) -> VideoResolutionResult:
    capture = cv2.VideoCapture(str(video.path))
    if not capture.isOpened():
        message = f"cannot open video: {video.path}"
        raise FileNotFoundError(message)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sample_indices = set(sample_frame_indices(total_frames, 5))
    extractor = FeatureExtractor()
    action_classifier = ActionClassifier(config)
    tier_classifier = TierClassifier(config)
    trigger_engine = TriggerEngine(config)
    state = BenchmarkState()
    try:
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            process_frame(
                frame=frame,
                fps=fps,
                state=state,
                video=video,
                pose_estimator=pose_estimator,
                extractor=extractor,
                action_classifier=action_classifier,
                tier_classifier=tier_classifier,
                trigger_engine=trigger_engine,
                resolution=resolution,
                size=size,
                sample_indices=sample_indices,
                samples_dir=samples_dir,
            )
    finally:
        capture.release()
    return state.to_result(video=video, resolution=resolution, fps=fps)


class BenchmarkState:
    def __init__(self) -> None:
        self.frame_count = 0
        self.detected_frames = 0
        self.danger_event_count = 0
        self.previous_danger = False
        self.latencies_ms: list[float] = []
        self.pose_confidences: list[float] = []
        self.visible_ratios: list[float] = []
        self.sample_images: list[str] = []

    def to_result(self, *, video: SelectedVideo, resolution: str, fps: float) -> VideoResolutionResult:
        danger_detected = self.danger_event_count > 0
        detection_rate = float(self.detected_frames / self.frame_count) if self.frame_count else 0.0
        visible_joint_ratio = statistics.fmean(self.visible_ratios) if self.visible_ratios else 0.0
        skeleton_confidence = statistics.fmean(self.pose_confidences) if self.pose_confidences else 0.0
        mean_latency = statistics.fmean(self.latencies_ms) if self.latencies_ms else 0.0
        gate = evaluate_resolution_gate(
            ResolutionMetrics(
                expected_label=video.expected_label,
                detection_rate=detection_rate,
                visible_joint_ratio=visible_joint_ratio,
                skeleton_confidence_mean=skeleton_confidence,
                danger_detected=danger_detected,
            )
        )
        return VideoResolutionResult(
            expected_label=video.expected_label,
            video_path=str(video.path),
            resolution=resolution,
            frame_count=self.frame_count,
            detected_frames=self.detected_frames,
            duration_sec=float(self.frame_count / fps) if fps > 0.0 else 0.0,
            pose_latency_p95_ms=percentile_nearest(self.latencies_ms, 95.0),
            pose_fps=float(1000.0 / mean_latency) if mean_latency > 0.0 else 0.0,
            skeleton_confidence_mean=float(skeleton_confidence),
            visible_joint_ratio=float(visible_joint_ratio),
            danger_detected=danger_detected,
            danger_event_count=self.danger_event_count,
            sample_images=tuple(self.sample_images),
            gate_allowed=gate.allowed,
            gate_reasons=gate.reasons,
        )


def process_frame(
    *,
    frame: np.ndarray,
    fps: float,
    state: BenchmarkState,
    video: SelectedVideo,
    pose_estimator: YoloPoseEstimator,
    extractor: FeatureExtractor,
    action_classifier: ActionClassifier,
    tier_classifier: TierClassifier,
    trigger_engine: TriggerEngine,
    resolution: str,
    size: tuple[int, int],
    sample_indices: set[int],
    samples_dir: Path,
) -> None:
    resized = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    timestamp_ms = int((state.frame_count / fps) * 1000.0)
    started = time.perf_counter()
    detections = pose_estimator.predict(resized)
    state.latencies_ms.append((time.perf_counter() - started) * 1000.0)
    action_label = "NO_PERSON"
    risk_label = "NORMAL"
    danger_now = False
    bbox: list[int] | None = None
    keypoints: list[list[float]] = []
    if detections:
        detection = detections[0]
        state.detected_frames += 1
        bbox = detection.bbox
        keypoints = detection.keypoints
        feature_map, feature_vector = extractor.extract(1, detection.bbox, detection.keypoints, resized.shape, timestamp_ms)
        action_label, _ = action_classifier.predict(1, feature_map, feature_vector)
        risk_label, _ = tier_classifier.update(1, feature_map)
        flags = trigger_engine.update(1, feature_map, action_label, timestamp_ms)
        candidate = trigger_engine.judge_candidate(1, flags, action_label)
        danger_now = risk_label in {"DANGER", "DROP"} or (candidate is not None and candidate[1] == "DANGER")
        state.pose_confidences.append(float(detection.pose_confidence_mean))
        state.visible_ratios.append(float(feature_map.get("visible_joint_ratio", 0.0)))
    if danger_now and not state.previous_danger:
        state.danger_event_count += 1
    state.previous_danger = danger_now
    if state.frame_count in sample_indices:
        state.sample_images.append(
            save_sample(
                frame=resized,
                bbox=bbox,
                keypoints=keypoints,
                expected_label=video.expected_label,
                action_label=action_label,
                risk_label=risk_label,
                resolution=resolution,
                frame_index=state.frame_count,
                video_path=video.path,
                samples_dir=samples_dir,
            )
        )
    state.frame_count += 1


def sample_frame_indices(total_frames: int, count: int) -> tuple[int, ...]:
    if total_frames <= 0:
        return (0,)
    if total_frames <= count:
        return tuple(range(total_frames))
    last = total_frames - 1
    return tuple(sorted({round(index * last / (count - 1)) for index in range(count)}))


def save_sample(
    *,
    frame: np.ndarray,
    bbox: list[int] | None,
    keypoints: list[list[float]],
    expected_label: str,
    action_label: str,
    risk_label: str,
    resolution: str,
    frame_index: int,
    video_path: Path,
    samples_dir: Path,
) -> str:
    samples_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = video_path.stem.replace(" ", "_")
    image_path = samples_dir / f"{expected_label}_{resolution}_{safe_stem}_{frame_index:06d}.jpg"
    overlay = draw_pose_overlay(
        frame,
        bbox=bbox,
        keypoints=keypoints,
        expected_label=expected_label,
        action_label=action_label,
        risk_label=risk_label,
        frame_index=frame_index,
    )
    cv2.imwrite(str(image_path), overlay)
    return str(image_path)


def percentile_nearest(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1)
    return float(ordered[min(index, len(ordered) - 1)])
