from __future__ import annotations

import argparse
import csv
import io
import json
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np

from device_transfer.Edge.edge.feature_extractor import FEATURE_COLUMNS, FeatureExtractor

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]
LABEL_ORDER: Final = ("standing", "walking", "sitting", "lying_rest")


@dataclass(frozen=True, slots=True)
class ExportReport:
    samples: int
    label_names: tuple[str, ...]
    label_counts: dict[str, int]


class CoarseExportError(Exception):
    pass


def _read_jsonl(path: Path) -> list[JsonObject]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise CoarseExportError(f"cannot read JSONL: {path}") from exc
    rows: list[JsonObject] = []
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CoarseExportError(f"malformed JSONL: {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise CoarseExportError(f"JSONL row must be object: {path}:{line_number}")
        rows.append(value)
    return rows


def _identity(row: JsonObject) -> tuple[str, int]:
    sample_id, frame_index = row.get("sample_id"), row.get("frame_index")
    if not isinstance(sample_id, str) or not isinstance(frame_index, int):
        raise CoarseExportError("row requires sample_id and integer frame_index")
    return sample_id, frame_index


def _point_array(value: JsonValue | None) -> np.ndarray:
    if not isinstance(value, list) or len(value) != 17:
        raise CoarseExportError("keypoints must contain 17 joints")
    points = np.asarray(value, dtype=np.float32)
    if points.shape != (17, 3) or not np.isfinite(points).all():
        raise CoarseExportError("keypoints must be finite [17,3]")
    return points


def _longest_run(rows: list[JsonObject], length: int) -> list[JsonObject] | None:
    ordered = sorted(rows, key=lambda row: _identity(row)[1])
    best: list[JsonObject] = []
    current: list[JsonObject] = []
    for row in ordered:
        label = row.get("activity_label")
        if not isinstance(label, str) or label not in LABEL_ORDER:
            raise CoarseExportError(f"unsupported coarse label: {label}")
        contiguous = current and _identity(row)[1] == _identity(current[-1])[1] + 1
        same_label = current and label == current[-1].get("activity_label")
        if contiguous and same_label:
            current.append(row)
        else:
            if len(current) > len(best):
                best = current
            current = [row]
    if len(current) > len(best):
        best = current
    return best[:length] if len(best) >= length else None


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def export_coarse_dataset(
    activity_frames: Path,
    accepted_poses: Path,
    output_csv: Path,
    output_npz: Path,
    meta_out: Path,
    *,
    sequence_length: int,
) -> ExportReport:
    if sequence_length < 2:
        raise CoarseExportError("sequence length must be at least 2")
    grouped: dict[str, list[JsonObject]] = defaultdict(list)
    for row in _read_jsonl(activity_frames):
        grouped[_identity(row)[0]].append(row)
    pose_map = {_identity(row): row for row in _read_jsonl(accepted_poses)}
    feature_rows: list[dict[str, str]] = []
    sequences: list[np.ndarray] = []
    labels: list[str] = []
    sample_ids: list[str] = []
    extractor = FeatureExtractor()
    for track_id, (sample_id, rows) in enumerate(sorted(grouped.items()), 1):
        run = _longest_run(rows, sequence_length)
        if run is None:
            continue
        label = str(run[0]["activity_label"])
        middle_id = _identity(run[len(run) // 2])
        pose = pose_map.get((f"{middle_id[0]}:{middle_id[1]}", middle_id[1]))
        if pose is None:
            raise CoarseExportError(f"accepted pose missing: {middle_id[0]}:{middle_id[1]}")
        bbox = pose.get("bbox_xyxy")
        width, height = pose.get("width"), pose.get("height")
        if not isinstance(bbox, list) or len(bbox) != 4 or not isinstance(width, int) or not isinstance(height, int):
            raise CoarseExportError("accepted pose shape is invalid")
        points = _point_array(pose.get("keypoints"))
        features, _ = extractor.extract(
            track_id, [int(value) for value in bbox], points.tolist(), (height, width, 3),
            int(pose.get("timestamp_ms", 0) or 0),
        )
        feature = {"sample_id": sample_id, "activity_label": label}
        feature.update({name: f"{float(features[name]):.10g}" for name in FEATURE_COLUMNS})
        feature_rows.append(feature)
        frame_tensor = np.stack([_point_array(row.get("keypoints")) for row in run])
        sequences.append(np.transpose(frame_tensor, (2, 0, 1))[:, :, :, np.newaxis])
        labels.append(label)
        sample_ids.append(sample_id)
    if len(set(labels)) < 2:
        raise CoarseExportError("at least two coarse labels are required")
    label_names = tuple(label for label in LABEL_ORDER if label in labels)
    label_index = {label: index for index, label in enumerate(label_names)}
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=("sample_id", "activity_label", *FEATURE_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(feature_rows)
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer, sequences=np.stack(sequences),
        labels=np.asarray([label_index[label] for label in labels], dtype=np.int64),
        sample_ids=np.asarray(sample_ids),
    )
    counts = {label: labels.count(label) for label in label_names}
    meta: JsonObject = {
        "schema_version": 1, "profile": "coarse-distill-v1",
        "canonical_21": False, "teacher_distillation": True,
        "label_names": list(label_names), "label_counts": counts,
        "samples": len(sample_ids), "sequence_length": sequence_length,
    }
    _atomic_write(output_csv, stream.getvalue().encode())
    _atomic_write(output_npz, buffer.getvalue())
    _atomic_write(meta_out, (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode())
    return ExportReport(len(sample_ids), label_names, counts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export one group-safe coarse-distillation sample per video")
    parser.add_argument("--activity-frames", required=True)
    parser.add_argument("--accepted-poses", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-npz", required=True)
    parser.add_argument("--meta-out", required=True)
    parser.add_argument("--sequence-length", type=int, default=24)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = export_coarse_dataset(
            Path(args.activity_frames), Path(args.accepted_poses), Path(args.output_csv),
            Path(args.output_npz), Path(args.meta_out), sequence_length=args.sequence_length,
        )
    except (CoarseExportError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 23
    print(f"samples={report.samples} labels={report.label_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
