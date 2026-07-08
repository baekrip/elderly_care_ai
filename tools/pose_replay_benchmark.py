from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Final


VIDEO_SUFFIXES: Final[frozenset[str]] = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm"})


@dataclass(frozen=True, slots=True)
class LabelSpec:
    folder_name: str
    expected_label: str


@dataclass(frozen=True, slots=True)
class SelectedVideo:
    expected_label: str
    path: Path


@dataclass(frozen=True, slots=True)
class ResolutionMetrics:
    expected_label: str
    detection_rate: float
    visible_joint_ratio: float
    skeleton_confidence_mean: float
    danger_detected: bool


@dataclass(frozen=True, slots=True)
class GateResult:
    allowed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VideoResolutionResult:
    expected_label: str
    video_path: str
    resolution: str
    frame_count: int
    detected_frames: int
    duration_sec: float
    pose_latency_p95_ms: float
    pose_fps: float
    skeleton_confidence_mean: float
    visible_joint_ratio: float
    danger_detected: bool
    danger_event_count: int
    sample_images: tuple[str, ...]
    gate_allowed: bool
    gate_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolutionSummary:
    resolution: str
    video_count: int
    danger_recall: float | None
    normal_fp_per_hour: float | None
    gate_allowed: bool
    gate_reasons: tuple[str, ...]


LABEL_SPECS: Final[tuple[LabelSpec, ...]] = (
    LabelSpec(folder_name="Dementia_Daily_Activity", expected_label="NORMAL"),
    LabelSpec(folder_name="Abnormal_Behavior_Wander", expected_label="ABNORMAL"),
    LabelSpec(folder_name="abnormal_drop", expected_label="DANGER"),
)


def select_replay_videos(*, root: Path, seed: int, per_label: int) -> tuple[SelectedVideo, ...]:
    rng = random.Random(seed)
    selected: list[SelectedVideo] = []
    for spec in LABEL_SPECS:
        candidates = _video_files(root / spec.folder_name)
        if len(candidates) < per_label:
            message = f"{spec.folder_name} has {len(candidates)} videos, needs {per_label}"
            raise FileNotFoundError(message)
        for path in rng.sample(candidates, per_label):
            selected.append(SelectedVideo(expected_label=spec.expected_label, path=path))
    return tuple(selected)


def evaluate_resolution_gate(
    metrics: ResolutionMetrics,
    *,
    min_detection_rate: float = 0.80,
    min_visible_joint_ratio: float = 0.60,
    min_skeleton_confidence_mean: float = 0.60,
) -> GateResult:
    reasons: list[str] = []
    if metrics.detection_rate < min_detection_rate:
        reasons.append(f"detection_rate {metrics.detection_rate:.4f} < {min_detection_rate:.4f}")
    if metrics.visible_joint_ratio < min_visible_joint_ratio:
        reasons.append(
            f"visible_joint_ratio {metrics.visible_joint_ratio:.4f} < {min_visible_joint_ratio:.4f}"
        )
    if metrics.skeleton_confidence_mean < min_skeleton_confidence_mean:
        reasons.append(
            "skeleton_confidence_mean "
            f"{metrics.skeleton_confidence_mean:.4f} < {min_skeleton_confidence_mean:.4f}"
        )
    if metrics.expected_label == "DANGER" and not metrics.danger_detected:
        reasons.append("DANGER not detected")
    return GateResult(allowed=not reasons, reasons=tuple(reasons))


def summarize_by_resolution(results: tuple[VideoResolutionResult, ...]) -> tuple[ResolutionSummary, ...]:
    summaries: list[ResolutionSummary] = []
    for resolution in sorted({result.resolution for result in results}):
        matching = tuple(result for result in results if result.resolution == resolution)
        danger = tuple(result for result in matching if result.expected_label == "DANGER")
        normal = tuple(result for result in matching if result.expected_label == "NORMAL")
        danger_recall = (
            sum(1 for result in danger if result.danger_detected) / len(danger)
            if danger
            else None
        )
        normal_duration_hours = sum(result.duration_sec for result in normal) / 3600.0
        normal_fp_per_hour = (
            sum(result.danger_event_count for result in normal) / normal_duration_hours
            if normal_duration_hours > 0.0
            else None
        )
        reasons = tuple(
            reason
            for result in matching
            for reason in result.gate_reasons
            if not result.gate_allowed
        )
        summaries.append(
            ResolutionSummary(
                resolution=resolution,
                video_count=len(matching),
                danger_recall=None if danger_recall is None else round(danger_recall, 6),
                normal_fp_per_hour=None
                if normal_fp_per_hour is None
                else round(normal_fp_per_hour, 6),
                gate_allowed=not reasons,
                gate_reasons=reasons,
            )
        )
    return tuple(summaries)


def _video_files(folder: Path) -> list[Path]:
    if not folder.exists():
        message = f"video folder not found: {folder}"
        raise FileNotFoundError(message)
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    )
