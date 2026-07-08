from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from device_transfer.Edge.edge.feature_extractor import FEATURE_COLUMNS


@dataclass(frozen=True, slots=True)
class Dataset:
    features: np.ndarray
    sequences: np.ndarray
    labels: np.ndarray
    sample_ids: tuple[str, ...]
    label_names: tuple[str, ...]
    excluded_labels: tuple[str, ...]


class CoarseTrainingError(Exception):
    pass


def _load_meta(path: Path) -> tuple[str, ...]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoarseTrainingError(f"cannot read metadata: {path}") from exc
    if not isinstance(value, dict):
        raise CoarseTrainingError("metadata root must be object")
    if value.get("profile") != "coarse-distill-v1" or value.get("canonical_21") is not False:
        raise CoarseTrainingError("metadata must declare coarse-distill-v1 and canonical_21=false")
    raw_names = value.get("label_names")
    if not isinstance(raw_names, list) or len(raw_names) < 2 or not all(isinstance(item, str) for item in raw_names):
        raise CoarseTrainingError("metadata requires at least two label names")
    return tuple(raw_names)


def load_dataset(
    features_csv: Path,
    sequence_npz: Path,
    meta_json: Path,
    min_samples_per_class: int,
) -> Dataset:
    label_names = _load_meta(meta_json)
    expected = ("sample_id", "activity_label", *FEATURE_COLUMNS)
    try:
        with features_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != expected:
                raise CoarseTrainingError("coarse feature CSV schema mismatch")
            rows = list(reader)
    except OSError as exc:
        raise CoarseTrainingError(f"cannot read feature CSV: {features_csv}") from exc
    if not rows:
        raise CoarseTrainingError("coarse feature CSV is empty")
    sample_ids = tuple(str(row["sample_id"]) for row in rows)
    if len(set(sample_ids)) != len(sample_ids):
        raise CoarseTrainingError("one sample per source video is required")
    try:
        labels = np.asarray([label_names.index(str(row["activity_label"])) for row in rows], dtype=np.int64)
        features = np.asarray([[float(row[name]) for name in FEATURE_COLUMNS] for row in rows], dtype=np.float32)
    except (ValueError, KeyError) as exc:
        raise CoarseTrainingError("invalid coarse feature row") from exc
    if not np.isfinite(features).all():
        raise CoarseTrainingError("coarse features contain NaN or Inf")
    try:
        with np.load(sequence_npz, allow_pickle=False) as payload:
            sequences = np.asarray(payload["sequences"], dtype=np.float32)
            sequence_labels = np.asarray(payload["labels"], dtype=np.int64)
            sequence_ids = tuple(str(value) for value in payload["sample_ids"].tolist())
    except (OSError, ValueError, KeyError) as exc:
        raise CoarseTrainingError(f"cannot read coarse sequence NPZ: {sequence_npz}") from exc
    if sequences.ndim != 5 or sequences.shape[1] != 3 or sequences.shape[3:] != (17, 1):
        raise CoarseTrainingError("sequences must be [N,3,T,17,1]")
    if not np.isfinite(sequences).all() or sequence_ids != sample_ids or not np.array_equal(sequence_labels, labels):
        raise CoarseTrainingError("CSV and NPZ sample contracts do not match")
    counts = np.bincount(labels, minlength=len(label_names))
    kept_old_indices = tuple(
        index for index, count in enumerate(counts)
        if int(count) >= min_samples_per_class
    )
    if len(kept_old_indices) < 2:
        raise CoarseTrainingError("fewer than two labels meet the minimum source-video count")
    keep_mask = np.isin(labels, np.asarray(kept_old_indices, dtype=np.int64))
    remap = {old: new for new, old in enumerate(kept_old_indices)}
    filtered_labels = np.asarray([remap[int(value)] for value in labels[keep_mask]], dtype=np.int64)
    kept_names = tuple(label_names[index] for index in kept_old_indices)
    excluded = tuple(label for index, label in enumerate(label_names) if index not in kept_old_indices)
    filtered_ids = tuple(sample_id for sample_id, keep in zip(sample_ids, keep_mask.tolist()) if keep)
    return Dataset(
        features[keep_mask], sequences[keep_mask], filtered_labels,
        filtered_ids, kept_names, excluded,
    )
