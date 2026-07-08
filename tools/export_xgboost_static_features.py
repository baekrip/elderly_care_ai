from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from device_transfer.Edge.edge.feature_extractor import FEATURE_COLUMNS, FeatureExtractor
from device_transfer.Edge.shared.labels import STATIC_POSTURE_LABELS
from tools.static_feature_export_io import ExportMetadata, JsonObject, JsonValue, atomic_write_pair

MIN_KEYPOINT_CONFIDENCE: Final = 0.2
MIN_MEAN_POSE_CONFIDENCE: Final = 0.35
MIN_VALID_KEYPOINTS: Final = 10
KEYPOINT_COUNT: Final = 17
OUTPUT_COLUMNS: Final = ("sample_id", "activity_label", *FEATURE_COLUMNS)


@dataclass(frozen=True, slots=True)
class RegistryContractError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class RegistrySample:
    sample_id: str
    activity_label: str
    pose_path: Path


def _parse_json_object(raw: str, source: Path, line_number: int) -> JsonObject:
    try:
        value: JsonValue = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RegistryContractError(f"malformed JSONL: {source}:{line_number}") from exc
    if not isinstance(value, dict):
        raise RegistryContractError(f"JSONL row must be an object: {source}:{line_number}")
    return value


def _safe_child_path(root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RegistryContractError(f"pose path escapes registry root: {raw_path}") from exc
    return resolved


def _required_text(row: JsonObject, key: str, source: Path, line_number: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryContractError(f"missing {key}: {source}:{line_number}")
    return value.strip()


def _registry_rows(path: Path) -> list[RegistrySample]:
    root = path.resolve().parent
    samples: list[RegistrySample] = []
    sample_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise RegistryContractError(f"cannot read registry: {path}") from exc
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        row = _parse_json_object(raw, path, line_number)
        nested = row.get("samples")
        records = nested if isinstance(nested, list) else [row]
        for record in records:
            if not isinstance(record, dict):
                raise RegistryContractError(f"invalid registry sample: {path}:{line_number}")
            sample_id = _required_text(record, "sample_id", path, line_number)
            if sample_id in sample_ids:
                raise RegistryContractError(f"duplicate sample_id: {sample_id}")
            sample_ids.add(sample_id)
            activity_label = _required_text(record, "activity_label", path, line_number).lower()
            if activity_label not in STATIC_POSTURE_LABELS:
                raise RegistryContractError(f"unknown activity_label: {activity_label}")
            pose_jsonl = _required_text(record, "pose_jsonl", path, line_number)
            samples.append(
                RegistrySample(
                    sample_id=sample_id,
                    activity_label=activity_label,
                    pose_path=_safe_child_path(root, pose_jsonl),
                ),
            )
    return samples


def _pose_rows(path: Path) -> tuple[dict[str, JsonObject], int]:
    records: dict[str, JsonObject] = {}
    malformed_rows = 0
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return records, 1
    for line_number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            row = _parse_json_object(raw, path, line_number)
        except RegistryContractError:
            malformed_rows += 1
            continue
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            malformed_rows += 1
            continue
        if sample_id in records:
            raise RegistryContractError(f"duplicate pose sample_id: {sample_id}")
        records[sample_id] = row
    return records, malformed_rows


def _number_list(value: JsonValue | None, expected: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != expected:
        return None
    if not all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in value):
        return None
    return [float(item) for item in value]


def _keypoints(value: JsonValue | None) -> list[list[float]] | None:
    if not isinstance(value, list) or len(value) != KEYPOINT_COUNT:
        return None
    points: list[list[float]] = []
    for raw_point in value:
        point = _number_list(raw_point, 3)
        if point is None:
            return None
        points.append(point)
    return points


def _frame_shape(row: JsonObject, bbox: list[float]) -> tuple[int, int, int] | None:
    raw_shape = _number_list(row.get("frame_shape"), 3)
    if raw_shape is not None:
        return int(raw_shape[0]), int(raw_shape[1]), int(raw_shape[2])
    width = row.get("width")
    height = row.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        return int(height), int(width), 3
    inferred_width = int(max(bbox[0], bbox[2]))
    inferred_height = int(max(bbox[1], bbox[3]))
    return (inferred_height, inferred_width, 3) if inferred_width > 0 and inferred_height > 0 else None


def _feature_row(sample: RegistrySample, row: JsonObject, track_id: int) -> tuple[dict[str, str] | None, str | None]:
    points = _keypoints(row.get("keypoints"))
    bbox = _number_list(row.get("bbox_xyxy"), 4)
    if points is None or bbox is None:
        return None, "invalid_pose_shape"
    confidences = [point[2] for point in points]
    if sum(confidence >= MIN_KEYPOINT_CONFIDENCE for confidence in confidences) < MIN_VALID_KEYPOINTS:
        return None, "too_few_valid_keypoints"
    if sum(confidences) / len(confidences) < MIN_MEAN_POSE_CONFIDENCE:
        return None, "low_mean_pose_confidence"
    shape = _frame_shape(row, bbox)
    if shape is None:
        return None, "invalid_frame_shape"
    masked = [[0.0, 0.0, point[2]] if point[2] < MIN_KEYPOINT_CONFIDENCE else point for point in points]
    timestamp = row.get("timestamp_ms", 0)
    timestamp_ms = int(timestamp) if isinstance(timestamp, (int, float)) else 0
    features, _ = FeatureExtractor().extract(
        track_id=track_id,
        bbox=[int(value) for value in bbox],
        keypoints=masked,
        frame_shape=shape,
        timestamp_ms=timestamp_ms,
    )
    output = {"sample_id": sample.sample_id, "activity_label": sample.activity_label}
    output.update({name: f"{float(features[name]):.10g}" for name in FEATURE_COLUMNS})
    return output, None


def export_static_features(registry_path: Path, output_csv: Path, meta_out: Path) -> ExportMetadata:
    samples = _registry_rows(registry_path)
    cache: dict[Path, tuple[dict[str, JsonObject], int]] = {}
    skipped: Counter[str] = Counter()
    rows: list[dict[str, str]] = []
    label_counts = {label: 0 for label in STATIC_POSTURE_LABELS}
    for track_id, sample in enumerate(samples):
        if sample.pose_path not in cache:
            cache[sample.pose_path] = _pose_rows(sample.pose_path)
        pose_rows, malformed_rows = cache[sample.pose_path]
        pose_row = pose_rows.get(sample.sample_id)
        if pose_row is None:
            skipped["malformed_pose_jsonl" if malformed_rows else "missing_pose_record"] += 1
            continue
        feature_row, reason = _feature_row(sample, pose_row, track_id)
        if feature_row is None:
            skipped[reason or "invalid_pose_shape"] += 1
            continue
        rows.append(feature_row)
        label_counts[sample.activity_label] += 1
    rows.sort(key=lambda row: row["sample_id"])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    metadata = ExportMetadata(
        label_names=STATIC_POSTURE_LABELS,
        feature_names=tuple(FEATURE_COLUMNS),
        exported_rows=len(rows),
        label_counts=label_counts,
        skipped_reasons=dict(sorted(skipped.items())),
    )
    thresholds: dict[str, JsonValue] = {
        "min_keypoint_confidence": MIN_KEYPOINT_CONFIDENCE,
        "min_mean_pose_confidence": MIN_MEAN_POSE_CONFIDENCE,
        "min_valid_keypoints": MIN_VALID_KEYPOINTS,
    }
    metadata_text = json.dumps(
        metadata.to_json(thresholds),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    atomic_write_pair(output_csv, stream.getvalue(), meta_out, metadata_text)
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export XGBoost static/posture features")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--meta-out", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        metadata = export_static_features(
            registry_path=Path(args.registry),
            output_csv=Path(args.output_csv),
            meta_out=Path(args.meta_out),
        )
    except (OSError, RegistryContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"exported_rows={metadata.exported_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
