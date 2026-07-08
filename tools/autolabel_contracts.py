from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Mapping

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

SOURCE_RISK: Final[dict[str, str]] = {
    "M_I_001": "normal",
    "M_I_004": "normal",
    "M_I_007": "normal",
    "ABNOR_W": "abnormal",
    "ABNOR_H": "danger",
}


@dataclass(frozen=True, slots=True)
class SourceAnnotation:
    action_type: str
    action_name: str
    risk_label: str


@dataclass(frozen=True, slots=True)
class SourceAnnotationRejected:
    reason: str


@dataclass(frozen=True, slots=True)
class TeacherPrediction:
    label: str
    score: float
    rule_id: str


class AnnotationContractError(Exception):
    pass


def _load_json(path: Path) -> JsonValue:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationContractError(f"cannot read source annotation: {path}") from exc


def _finite_number(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def resolve_source_annotation(
    label_path: Path,
    frame_index: int,
) -> SourceAnnotation | SourceAnnotationRejected:
    value = _load_json(label_path)
    if not isinstance(value, dict):
        return SourceAnnotationRejected("invalid_source_annotation_root")
    annotations = value.get("annotations")
    if not isinstance(annotations, dict):
        return SourceAnnotationRejected("missing_source_annotations")
    raw_objects = annotations.get("object")
    if not isinstance(raw_objects, list):
        return SourceAnnotationRejected("missing_source_objects")
    matches: list[SourceAnnotation] = []
    for raw in raw_objects:
        if not isinstance(raw, dict):
            return SourceAnnotationRejected("invalid_source_object")
        start = _finite_number(raw.get("startFrame", 0.0))
        end = _finite_number(raw.get("endFrame", raw.get("startFrame", 0.0)))
        if start is None or end is None:
            return SourceAnnotationRejected("invalid_source_interval")
        if min(start, end) <= frame_index <= max(start, end):
            action_type = str(raw.get("actionType") or "").strip().upper()
            risk = SOURCE_RISK.get(action_type)
            if risk is None:
                return SourceAnnotationRejected("unsupported_source_action_type")
            matches.append(
                SourceAnnotation(
                    action_type=action_type,
                    action_name=str(raw.get("actionName") or "").strip(),
                    risk_label=risk,
                ),
            )
    if not matches:
        return SourceAnnotationRejected("no_covering_source_annotation")
    unique = set(matches)
    if len(unique) != 1:
        return SourceAnnotationRejected("conflicting_source_annotations")
    return matches[0]


def classify_coarse_features(features: Mapping[str, float]) -> TeacherPrediction | None:
    raw_torso = abs(float(features.get("torso_angle_deg", 0.0))) % 360.0
    folded_torso = min(raw_torso, 360.0 - raw_torso)
    torso = min(folded_torso, abs(180.0 - folded_torso))
    left_knee = float(features.get("left_knee_angle_deg", 180.0))
    right_knee = float(features.get("right_knee_angle_deg", 180.0))
    knee = (left_knee + right_knee) / 2.0
    velocity = abs(float(features.get("center_velocity_px_s", 0.0)))
    aspect = float(features.get("bbox_aspect_ratio", 0.0))
    values = (torso, knee, velocity, aspect)
    if not all(math.isfinite(value) for value in values):
        return None
    if torso > 70.0 or aspect > 1.4:
        return TeacherPrediction("lying_rest", 0.8, "horizontal_body_geometry")
    if knee < 77.0 and torso < 55.0:
        return TeacherPrediction("sitting", 0.75, "bent_knees_upright_torso")
    if velocity > 80.0 and torso < 35.0:
        return TeacherPrediction("walking", 0.75, "upright_high_center_velocity")
    if torso < 35.0 and knee > 150.0 and 0.0 < aspect < 0.9 and velocity <= 80.0:
        return TeacherPrediction("standing", 0.8, "upright_straight_legs")
    return None
