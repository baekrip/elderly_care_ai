from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
EDGE_ROOT: Final = ROOT / "device_transfer" / "Edge"
if str(EDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(EDGE_ROOT))

from edge.config import load_config
from edge.feature_extractor import FeatureExtractor
from device_transfer.Edge.shared.labels import assert_never
from tools.autolabel_contracts import (
    SourceAnnotation,
    SourceAnnotationRejected,
    TeacherPrediction,
    classify_coarse_features,
    resolve_source_annotation,
)

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]
class AutoLabelContractError(Exception):
    pass


def visible_joint_ratio(keypoints: JsonValue, minimum: float) -> float:
    if not isinstance(keypoints, list) or len(keypoints) != 17:
        return 0.0
    visible = 0
    for point in keypoints:
        if not isinstance(point, list) or len(point) != 3:
            return 0.0
        confidence = point[2]
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            visible += float(confidence) >= minimum
    return visible / 17.0


def _read_json(path: Path) -> JsonValue:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoLabelContractError(f"cannot read JSON: {path}") from exc


def _read_jsonl(path: Path) -> list[JsonObject]:
    if not path.is_file():
        raise AutoLabelContractError(f"pose JSONL missing: {path}")
    rows: list[JsonObject] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AutoLabelContractError(f"malformed pose JSONL line {line_number}") from exc
        if not isinstance(value, dict):
            raise AutoLabelContractError(f"pose row must be object at line {line_number}")
        rows.append(value)
    return rows


def _atomic_jsonl(path: Path, rows: list[JsonObject]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _policy(path: Path) -> JsonObject:
    value = _read_json(path)
    required = {
        "schema_version", "min_pose_confidence", "min_joint_confidence",
        "min_visible_joint_ratio", "min_activity_confidence",
    }
    if not isinstance(value, dict) or not required.issubset(value):
        raise AutoLabelContractError("autolabel policy is incomplete")
    return value


def generate(args: argparse.Namespace) -> JsonObject:
    policy = _policy(Path(args.policy))
    config = load_config(args.config)
    extractor = FeatureExtractor()
    accepted_frames: list[JsonObject] = []
    pose_rows: list[JsonObject] = []
    static_rows: list[JsonObject] = []
    rejected: list[JsonObject] = []
    reasons: Counter[str] = Counter()
    activities: Counter[str] = Counter()
    seen: set[tuple[str, int]] = set()
    track_ids: dict[str, int] = {}
    for row in _read_jsonl(Path(args.pose_jsonl)):
        sample_id = row.get("sample_id")
        frame_index = row.get("frame_index")
        identity = (str(sample_id), int(frame_index)) if isinstance(frame_index, int) else ("", -1)
        reason: str | None = None
        keypoints = row.get("keypoints")
        if not isinstance(sample_id, str) or not sample_id or not isinstance(frame_index, int):
            reason = "invalid_identity"
        elif identity in seen:
            raise AutoLabelContractError(f"duplicate frame: {identity[0]}:{identity[1]}")
        seen.add(identity)
        pose_conf = float(row.get("pose_confidence_mean", 0.0) or 0.0)
        visible = visible_joint_ratio(keypoints, float(policy["min_joint_confidence"]))
        if reason is None and pose_conf < float(policy["min_pose_confidence"]):
            reason = "low_pose_confidence"
        if reason is None and visible < float(policy["min_visible_joint_ratio"]):
            reason = "low_visible_joint_ratio"
        bbox = row.get("bbox_xyxy")
        width, height = row.get("width"), row.get("height")
        if reason is None and not (isinstance(bbox, list) and len(bbox) == 4 and isinstance(width, int) and isinstance(height, int)):
            reason = "invalid_pose_shape"
        activity: str | None = None
        risk: str | None = None
        source_type = ""
        source_name = ""
        teacher_score = 0.0
        teacher_rule = ""
        if reason is None:
            label_path = row.get("label_path")
            if not isinstance(label_path, str):
                reason = "missing_source_annotation_path"
            else:
                source = resolve_source_annotation(Path(label_path), frame_index)
                match source:
                    case SourceAnnotationRejected(reason=source_reason):
                        reason = source_reason
                    case SourceAnnotation(action_type=action_type, action_name=action_name, risk_label=risk_label):
                        source_type, source_name, risk = action_type, action_name, risk_label
                    case unreachable:
                        assert_never(unreachable)
        if reason is None:
            track_id = track_ids.setdefault(sample_id, len(track_ids) + 1)
            features, vector = extractor.extract(
                track_id, [int(value) for value in bbox], keypoints, (height, width, 3), int(row.get("timestamp_ms", 0) or 0),
            )
            del vector
            prediction = classify_coarse_features(features)
            match prediction:
                case None:
                    reason = "unsupported_or_low_confidence_activity"
                case TeacherPrediction(label=label, score=score, rule_id=rule_id):
                    if score < float(policy["min_activity_confidence"]):
                        reason = "unsupported_or_low_confidence_activity"
                    else:
                        activity, teacher_score, teacher_rule = label, score, rule_id
                case unreachable:
                    assert_never(unreachable)
        if reason is not None:
            reasons[reason] += 1
            rejected.append({"sample_id": str(sample_id or ""), "frame_index": frame_index, "reason": reason})
            continue
        static_id = f"{sample_id}:{frame_index}"
        accepted_frames.append({
            "sample_id": sample_id, "frame_index": frame_index,
            "activity_label": activity, "risk_label": risk, "keypoints": keypoints,
            "teacher_mode": "rule_based", "teacher_score": teacher_score,
            "teacher_rule_id": teacher_rule, "source_action_type": source_type,
            "source_action_name": source_name,
        })
        activities[str(activity)] += 1
        pose_rows.append({**row, "sample_id": static_id})
        static_rows.append({"sample_id": static_id, "activity_label": activity, "pose_jsonl": Path(args.accepted_poses_out).name})
    if not accepted_frames:
        raise AutoLabelContractError("no frames passed automatic quality gates")
    outputs = (
        (Path(args.activity_frames_out), accepted_frames),
        (Path(args.accepted_poses_out), pose_rows),
        (Path(args.static_registry_out), static_rows),
        (Path(args.rejected_out), rejected),
    )
    for path, rows in outputs:
        _atomic_jsonl(path, rows)
    report: JsonObject = {
        "status": "created", "accepted_frames": len(accepted_frames),
        "rejected_frames": len(rejected), "rejected_reasons": dict(sorted(reasons.items())),
        "activity_counts": dict(sorted(activities.items())),
        "training_started": False,
        "teacher_model": "coarse-rule-teacher-v1",
    }
    Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create conservative activity pseudo-labels from accepted pose rows")
    parser.add_argument("--pose-jsonl", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--activity-frames-out", required=True)
    parser.add_argument("--accepted-poses-out", required=True)
    parser.add_argument("--static-registry-out", required=True)
    parser.add_argument("--rejected-out", required=True)
    parser.add_argument("--report-out", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = generate(args)
    except (AutoLabelContractError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"accepted={report['accepted_frames']} rejected={report['rejected_frames']} training_started=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
