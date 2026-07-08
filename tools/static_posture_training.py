from __future__ import annotations

import csv
import json
import math
import os
import random
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from device_transfer.Edge.edge.feature_extractor import FEATURE_COLUMNS
from device_transfer.Edge.shared.labels import STATIC_POSTURE_LABELS
from tools.static_feature_export_io import JsonObject
from tools.static_posture_metrics import classification_metrics


EXPECTED_COLUMNS: Final = ("sample_id", "activity_label", *FEATURE_COLUMNS)


@dataclass(frozen=True, slots=True)
class ContractError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class Sample:
    sample_id: str
    label_index: int
    features: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Dataset:
    samples: tuple[Sample, ...]
    label_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class TrainConfig:
    features_csv: Path
    report_out: Path
    model_out: Path | None
    dry_run: bool
    seed: int
    validation_fraction: float
    min_samples_per_class: int
    num_round: int
    max_depth: int
    eta: float


def _validate_config(config: TrainConfig) -> None:
    if not 0.0 < config.validation_fraction < 1.0:
        raise ContractError("validation fraction must be between 0 and 1")
    if config.min_samples_per_class < 2:
        raise ContractError("minimum samples per class must be at least 2")
    if not 1 <= config.num_round <= 500:
        raise ContractError("num-round must be between 1 and 500")
    if not 1 <= config.max_depth <= 12:
        raise ContractError("max-depth must be between 1 and 12")
    if not 0.0 < config.eta <= 1.0:
        raise ContractError("eta must be between 0 and 1")
    input_path = config.features_csv.resolve()
    report_path = config.report_out.resolve()
    if input_path == report_path:
        raise ContractError("output collision: report path matches input CSV")
    if config.dry_run:
        return
    if config.model_out is None:
        raise ContractError("--model-out is required unless --dry-run is used")
    if config.model_out.resolve() in {input_path, report_path}:
        raise ContractError("output collision: model path matches another path")


def load_dataset(path: Path, minimum: int) -> Dataset:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
                raise ContractError("feature schema mismatch: expected exact Task 2 columns")
            raw_rows = list(reader)
    except OSError as exc:
        raise ContractError(f"cannot read feature CSV: {path}") from exc
    if not raw_rows:
        raise ContractError("empty feature CSV")
    label_to_index = {label: index for index, label in enumerate(STATIC_POSTURE_LABELS)}
    samples: list[Sample] = []
    seen_ids: set[str] = set()
    counts: Counter[str] = Counter()
    for line_number, row in enumerate(raw_rows, start=2):
        sample_id = (row.get("sample_id") or "").strip()
        if not sample_id:
            raise ContractError(f"missing sample_id at line {line_number}")
        if sample_id in seen_ids:
            raise ContractError(f"duplicate sample_id: {sample_id}")
        seen_ids.add(sample_id)
        label = (row.get("activity_label") or "").strip()
        if label not in label_to_index:
            raise ContractError(f"unknown activity_label: {label or '<missing>'}")
        values: list[float] = []
        for feature_name in FEATURE_COLUMNS:
            raw_value = row.get(feature_name)
            try:
                value = float(raw_value) if raw_value is not None else math.nan
            except ValueError as exc:
                raise ContractError(
                    f"feature must be finite numeric: {feature_name} at line {line_number}",
                ) from exc
            if not math.isfinite(value):
                raise ContractError(
                    f"feature must be finite numeric: {feature_name} at line {line_number}",
                )
            values.append(value)
        samples.append(Sample(sample_id, label_to_index[label], tuple(values)))
        counts[label] += 1
    missing = [label for label in STATIC_POSTURE_LABELS if counts[label] == 0]
    if missing:
        raise ContractError(f"missing required labels: {', '.join(missing)}")
    underfilled = [label for label in STATIC_POSTURE_LABELS if counts[label] < minimum]
    if underfilled:
        raise ContractError(f"minimum samples per class not met: {', '.join(underfilled)}")
    samples.sort(key=lambda sample: sample.sample_id)
    ordered_counts = {label: counts[label] for label in STATIC_POSTURE_LABELS}
    return Dataset(samples=tuple(samples), label_counts=ordered_counts)


def stratified_split(
    dataset: Dataset,
    validation_fraction: float,
    seed: int,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    groups = {index: [] for index in range(len(STATIC_POSTURE_LABELS))}
    for index, sample in enumerate(dataset.samples):
        groups[sample.label_index].append(index)
    generator = random.Random(seed)
    train_indices: list[int] = []
    valid_indices: list[int] = []
    for label_index in range(len(STATIC_POSTURE_LABELS)):
        group = groups[label_index]
        generator.shuffle(group)
        valid_count = max(1, int(math.floor(len(group) * validation_fraction + 0.5)))
        valid_count = min(valid_count, len(group) - 1)
        valid_indices.extend(group[:valid_count])
        train_indices.extend(group[valid_count:])
    return tuple(sorted(train_indices)), tuple(sorted(valid_indices))


def _label_counts(dataset: Dataset, indices: tuple[int, ...]) -> dict[str, int]:
    counts = Counter(STATIC_POSTURE_LABELS[dataset.samples[index].label_index] for index in indices)
    return {label: counts[label] for label in STATIC_POSTURE_LABELS}


def _atomic_json(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _train(
    dataset: Dataset,
    train_indices: tuple[int, ...],
    valid_indices: tuple[int, ...],
    config: TrainConfig,
) -> tuple[list[int], Path]:
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise ContractError("xgboost is required for non-dry-run training") from exc
    if config.model_out is None:
        raise ContractError("--model-out is required for training")
    train_x = [list(dataset.samples[index].features) for index in train_indices]
    train_y = [dataset.samples[index].label_index for index in train_indices]
    valid_x = [list(dataset.samples[index].features) for index in valid_indices]
    valid_y = [dataset.samples[index].label_index for index in valid_indices]
    dtrain = xgb.DMatrix(train_x, label=train_y, feature_names=list(FEATURE_COLUMNS))
    dvalid = xgb.DMatrix(valid_x, label=valid_y, feature_names=list(FEATURE_COLUMNS))
    params = {
        "objective": "multi:softprob",
        "num_class": len(STATIC_POSTURE_LABELS),
        "max_depth": config.max_depth,
        "eta": config.eta,
        "eval_metric": "mlogloss",
        "tree_method": "hist",
        "seed": config.seed,
        "nthread": 1,
        "subsample": 1.0,
        "colsample_bytree": 1.0,
    }
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=config.num_round,
        evals=[(dvalid, "valid")],
        verbose_eval=False,
    )
    probabilities = booster.predict(dvalid).tolist()
    predictions = [
        max(range(len(row)), key=lambda index: float(row[index]))
        for row in probabilities
    ]
    config.model_out.parent.mkdir(parents=True, exist_ok=True)
    prefix = f".{config.model_out.stem}."
    descriptor, raw_path = tempfile.mkstemp(prefix=prefix, suffix=config.model_out.suffix, dir=config.model_out.parent)
    os.close(descriptor)
    staged = Path(raw_path)
    try:
        staged.unlink(missing_ok=True)
        booster.save_model(str(staged))
        os.replace(staged, config.model_out)
    finally:
        staged.unlink(missing_ok=True)
    return predictions, config.model_out


def execute(config: TrainConfig) -> JsonObject:
    _validate_config(config)
    dataset = load_dataset(config.features_csv, config.min_samples_per_class)
    train_indices, valid_indices = stratified_split(
        dataset,
        config.validation_fraction,
        config.seed,
    )
    report: JsonObject = {
        "schema_version": 1,
        "task": "static_posture",
        "mode": "dry_run" if config.dry_run else "trained",
        "label_order": list(STATIC_POSTURE_LABELS),
        "feature_columns": list(FEATURE_COLUMNS),
        "rows": len(dataset.samples),
        "train_rows": len(train_indices),
        "valid_rows": len(valid_indices),
        "label_counts": dataset.label_counts,
        "train_label_counts": _label_counts(dataset, train_indices),
        "valid_label_counts": _label_counts(dataset, valid_indices),
        "split": {
            "strategy": "deterministic_stratified",
            "seed": config.seed,
            "validation_fraction": config.validation_fraction,
            "train_sample_ids": [dataset.samples[index].sample_id for index in train_indices],
            "valid_sample_ids": [dataset.samples[index].sample_id for index in valid_indices],
        },
        "valid_accuracy": None,
        "confusion_matrix_labels": list(STATIC_POSTURE_LABELS),
        "confusion_matrix": None,
        "classification_report": None,
        "model_path": None,
    }
    if not config.dry_run:
        predictions, model_path = _train(dataset, train_indices, valid_indices, config)
        expected = [dataset.samples[index].label_index for index in valid_indices]
        accuracy, matrix, classification_report = classification_metrics(expected, predictions)
        report.update(
            {
                "valid_accuracy": accuracy,
                "confusion_matrix": matrix,
                "classification_report": classification_report,
                "model_path": str(model_path),
            },
        )
    _atomic_json(config.report_out, report)
    return report
