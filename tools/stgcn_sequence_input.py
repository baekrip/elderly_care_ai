from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from device_transfer.Edge.shared.labels import TARGET_ACTION_LABELS
from tools.static_feature_export_io import JsonObject, JsonValue

KEYPOINT_COUNT: Final = 17
CHANNEL_COUNT: Final = 3
MIN_KEYPOINT_CONFIDENCE: Final = 0.2
MIN_MEAN_POSE_CONFIDENCE: Final = 0.35
MIN_VALID_KEYPOINTS: Final = 10
RISK_LABELS: Final[tuple[str, ...]] = ("normal", "abnormal", "danger")
RISK_ALIASES: Final[dict[str, str]] = {
    "normal": "normal",
    "suspect": "abnormal",
    "abnormal": "abnormal",
    "danger": "danger",
    "danger_precursor": "danger",
    "fall": "danger",
}


@dataclass(frozen=True, slots=True)
class SequenceContractError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class FrameRecord:
    sample_id: str
    frame_index: int
    activity_label: str
    risk_label: str
    keypoints: np.ndarray


def _required_text(row: JsonObject, key: str, line_number: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SequenceContractError(f"missing {key} at line {line_number}")
    return value.strip()


def _frame_index(row: JsonObject, line_number: int) -> int:
    value = row.get("frame_index")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SequenceContractError(f"invalid frame_index at line {line_number}")
    return value


def _keypoints(value: JsonValue | None, line_number: int) -> np.ndarray:
    if not isinstance(value, list) or len(value) != KEYPOINT_COUNT:
        raise SequenceContractError(f"wrong joint count at line {line_number}")
    points: list[list[float]] = []
    for raw_point in value:
        if not isinstance(raw_point, list) or len(raw_point) != CHANNEL_COUNT:
            raise SequenceContractError(f"malformed keypoint array at line {line_number}")
        if not all(
            not isinstance(item, bool)
            and isinstance(item, (int, float))
            and math.isfinite(float(item))
            for item in raw_point
        ):
            raise SequenceContractError(f"non-finite keypoint at line {line_number}")
        points.append([float(item) for item in raw_point])
    return np.asarray(points, dtype=np.float32)


def _risk_label(row: JsonObject, line_number: int) -> str:
    raw_value = row.get("risk_label", row.get("tier_label"))
    if not isinstance(raw_value, str):
        raise SequenceContractError(f"missing risk_label/tier_label at line {line_number}")
    normalized = RISK_ALIASES.get(raw_value.strip().lower())
    if normalized is None:
        raise SequenceContractError(f"unknown risk label: {raw_value}")
    return normalized


def load_frames(path: Path) -> tuple[list[FrameRecord], Counter[str]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise SequenceContractError(f"cannot read input JSONL: {path}") from exc
    frames: list[FrameRecord] = []
    skipped: Counter[str] = Counter()
    seen: set[tuple[str, int]] = set()
    activity_names = set(TARGET_ACTION_LABELS)
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            parsed: JsonValue = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SequenceContractError(f"malformed JSONL at line {line_number}") from exc
        if not isinstance(parsed, dict):
            raise SequenceContractError(f"JSONL row must be an object at line {line_number}")
        sample_id = _required_text(parsed, "sample_id", line_number)
        frame_index = _frame_index(parsed, line_number)
        identity = (sample_id, frame_index)
        if identity in seen:
            raise SequenceContractError(f"duplicate frame: {sample_id}:{frame_index}")
        seen.add(identity)
        activity_label = _required_text(parsed, "activity_label", line_number).lower()
        if activity_label not in activity_names:
            raise SequenceContractError(f"unknown activity_label: {activity_label}")
        points = _keypoints(parsed.get("keypoints"), line_number)
        confidences = points[:, 2]
        if int(np.count_nonzero(confidences >= MIN_KEYPOINT_CONFIDENCE)) < MIN_VALID_KEYPOINTS:
            skipped["too_few_valid_keypoints"] += 1
            continue
        if float(np.mean(confidences)) < MIN_MEAN_POSE_CONFIDENCE:
            skipped["low_mean_pose_confidence"] += 1
            continue
        points[confidences < MIN_KEYPOINT_CONFIDENCE, :2] = 0.0
        frames.append(
            FrameRecord(
                sample_id=sample_id,
                frame_index=frame_index,
                activity_label=activity_label,
                risk_label=_risk_label(parsed, line_number),
                keypoints=points,
            ),
        )
    return frames, skipped
