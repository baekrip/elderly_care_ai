from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from device_transfer.Edge.shared.labels import TARGET_ACTION_LABELS
from tools.static_feature_export_io import JsonObject, JsonValue
from tools.stgcn_sequence_input import RISK_LABELS


@dataclass(frozen=True, slots=True)
class TrainingContractError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class TrainingDataset:
    sequences: np.ndarray
    activity_labels: np.ndarray
    risk_labels: np.ndarray
    sample_ids: tuple[str, ...]
    group_ids: tuple[str, ...]


def _load_meta(path: Path) -> JsonObject:
    try:
        parsed: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingContractError(f"cannot read metadata: {path}") from exc
    if not isinstance(parsed, dict):
        raise TrainingContractError("metadata root must be an object")
    if parsed.get("activity_label_names") != list(TARGET_ACTION_LABELS):
        raise TrainingContractError("activity label contract must match canonical 21 labels")
    if parsed.get("risk_label_names") != list(RISK_LABELS):
        raise TrainingContractError("risk label contract must be normal/abnormal/danger")
    return parsed


def load_training_dataset(npz_path: Path, meta_path: Path, minimum: int) -> TrainingDataset:
    if minimum < 2:
        raise TrainingContractError("minimum samples per class must be at least 2")
    _load_meta(meta_path)
    try:
        with np.load(npz_path, allow_pickle=False) as data:
            required = {"sequences", "activity_labels", "risk_labels", "sample_ids"}
            if not required.issubset(data.files):
                raise TrainingContractError("NPZ requires sequences, activity_labels, risk_labels, and sample_ids")
            sequences = np.asarray(data["sequences"], dtype=np.float32)
            activity_labels = np.asarray(data["activity_labels"], dtype=np.int64)
            risk_labels = np.asarray(data["risk_labels"], dtype=np.int64)
            sample_values = np.asarray(data["sample_ids"])
    except (OSError, ValueError) as exc:
        raise TrainingContractError(f"cannot read sequence NPZ: {npz_path}") from exc
    if sequences.ndim != 4 or sequences.shape[2:] != (17, 3):
        raise TrainingContractError("sequences must have shape [N,T,17,3]")
    count = sequences.shape[0]
    if count == 0 or activity_labels.shape != (count,) or risk_labels.shape != (count,) or sample_values.shape != (count,):
        raise TrainingContractError("NPZ array lengths must match non-empty sequence count")
    if not np.isfinite(sequences).all():
        raise TrainingContractError("sequences contain NaN or Inf")
    if np.any(activity_labels < 0) or np.any(activity_labels >= len(TARGET_ACTION_LABELS)):
        raise TrainingContractError("activity label index is out of range")
    if np.any(risk_labels < 0) or np.any(risk_labels >= len(RISK_LABELS)):
        raise TrainingContractError("risk label index is out of range")
    sample_ids = tuple(str(value) for value in sample_values.tolist())
    if len(set(sample_ids)) != len(sample_ids):
        raise TrainingContractError("duplicate sample_ids are not allowed")
    if any(":" not in sample_id for sample_id in sample_ids):
        raise TrainingContractError("sample_id must end with :frame_index for source-group split")
    group_ids = tuple(sample_id.rsplit(":", 1)[0] for sample_id in sample_ids)
    activity_counts = np.bincount(activity_labels, minlength=len(TARGET_ACTION_LABELS))
    risk_counts = np.bincount(risk_labels, minlength=len(RISK_LABELS))
    missing_activity = [TARGET_ACTION_LABELS[index] for index, value in enumerate(activity_counts) if value < minimum]
    missing_risk = [RISK_LABELS[index] for index, value in enumerate(risk_counts) if value < minimum]
    if missing_activity or missing_risk:
        raise TrainingContractError(f"minimum samples per class not met: {', '.join((*missing_activity, *missing_risk))}")
    return TrainingDataset(sequences, activity_labels, risk_labels, sample_ids, group_ids)


def group_safe_split(dataset: TrainingDataset, validation_fraction: float, seed: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if not 0.0 < validation_fraction < 1.0:
        raise TrainingContractError("validation fraction must be between 0 and 1")
    group_rows: dict[str, list[int]] = defaultdict(list)
    for index, group_id in enumerate(dataset.group_ids):
        group_rows[group_id].append(index)
    if len(group_rows) < 2:
        raise TrainingContractError("at least two source groups are required")
    generator = random.Random(seed)
    groups = sorted(group_rows)
    generator.shuffle(groups)
    activity_totals = Counter(int(value) for value in dataset.activity_labels)
    risk_totals = Counter(int(value) for value in dataset.risk_labels)
    selected: set[str] = set()
    val_activity: Counter[int] = Counter()
    val_risk: Counter[int] = Counter()
    missing_activity = set(range(len(TARGET_ACTION_LABELS)))
    missing_risk = set(range(len(RISK_LABELS)))
    while missing_activity or missing_risk:
        best_group: str | None = None
        best_score = -1
        for group in groups:
            if group in selected:
                continue
            rows = group_rows[group]
            local_activity = Counter(int(dataset.activity_labels[index]) for index in rows)
            local_risk = Counter(int(dataset.risk_labels[index]) for index in rows)
            if any(val_activity[label] + count >= activity_totals[label] for label, count in local_activity.items()):
                continue
            if any(val_risk[label] + count >= risk_totals[label] for label, count in local_risk.items()):
                continue
            score = len(set(local_activity) & missing_activity) + len(set(local_risk) & missing_risk)
            if score > best_score:
                best_group, best_score = group, score
        if best_group is None or best_score <= 0:
            raise TrainingContractError("cannot create leakage-free split with every class in train and validation")
        selected.add(best_group)
        rows = group_rows[best_group]
        val_activity.update(int(dataset.activity_labels[index]) for index in rows)
        val_risk.update(int(dataset.risk_labels[index]) for index in rows)
        missing_activity -= set(val_activity)
        missing_risk -= set(val_risk)
    target = max(1, int(math.floor(len(dataset.sample_ids) * validation_fraction + 0.5)))
    for group in groups:
        if sum(len(group_rows[item]) for item in selected) >= target:
            break
        if group not in selected:
            selected.add(group)
    valid = tuple(index for index, group in enumerate(dataset.group_ids) if group in selected)
    train = tuple(index for index, group in enumerate(dataset.group_ids) if group not in selected)
    if not train or not valid:
        raise TrainingContractError("group split produced an empty partition")
    return train, valid


def build_dry_run_report(
    npz_path: Path,
    meta_path: Path,
    *,
    seed: int,
    validation_fraction: float,
    minimum: int,
) -> JsonObject:
    dataset = load_training_dataset(npz_path, meta_path, minimum)
    train, valid = group_safe_split(dataset, validation_fraction, seed)
    train_groups = {dataset.group_ids[index] for index in train}
    valid_groups = {dataset.group_ids[index] for index in valid}
    return {
        "schema_version": 1,
        "activity_label_names": list(TARGET_ACTION_LABELS),
        "risk_label_names": list(RISK_LABELS),
        "total_sequences": len(dataset.sample_ids),
        "train_rows": len(train),
        "validation_rows": len(valid),
        "train_groups": sorted(train_groups),
        "validation_groups": sorted(valid_groups),
        "group_overlap": sorted(train_groups & valid_groups),
        "training_started": False,
        "ready_for_training": True,
    }

