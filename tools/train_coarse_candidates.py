from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import xgboost as xgb
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from device_transfer.Edge.shared.stgcn_model import MiniSTGCN
from tools.coarse_distill_training_data import (
    CoarseTrainingError,
    Dataset,
    load_dataset,
)
from device_transfer.Edge.edge.feature_extractor import FEATURE_COLUMNS

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class TrainConfig:
    features_csv: Path
    sequence_npz: Path
    meta_json: Path
    candidate_dir: Path
    report_out: Path
    dry_run: bool
    seed: int
    validation_fraction: float
    min_samples_per_class: int
    num_round: int
    epochs: int


def _split(dataset: Dataset, fraction: float, seed: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if not 0.0 < fraction < 1.0:
        raise CoarseTrainingError("validation fraction must be between 0 and 1")
    generator = random.Random(seed)
    train: list[int] = []
    valid: list[int] = []
    for label_index in range(len(dataset.label_names)):
        group = [index for index, label in enumerate(dataset.labels.tolist()) if label == label_index]
        generator.shuffle(group)
        valid_count = max(1, round(len(group) * fraction))
        valid_count = min(valid_count, len(group) - 1)
        valid.extend(group[:valid_count])
        train.extend(group[valid_count:])
    if not train or not valid:
        raise CoarseTrainingError("group-safe split produced empty partition")
    return tuple(sorted(train)), tuple(sorted(valid))


def _accuracy(expected: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(expected == predicted)) if len(expected) else 0.0


def _train_xgb(dataset: Dataset, train: tuple[int, ...], valid: tuple[int, ...], rounds: int) -> tuple[xgb.Booster, float]:
    dtrain = xgb.DMatrix(dataset.features[list(train)], label=dataset.labels[list(train)], feature_names=list(FEATURE_COLUMNS))
    dvalid = xgb.DMatrix(dataset.features[list(valid)], label=dataset.labels[list(valid)], feature_names=list(FEATURE_COLUMNS))
    booster = xgb.train(
        {"objective": "multi:softprob", "num_class": len(dataset.label_names), "max_depth": 3,
         "eta": 0.1, "tree_method": "hist", "nthread": 1, "seed": 42},
        dtrain, num_boost_round=rounds, evals=[(dvalid, "valid")], verbose_eval=False,
    )
    return booster, _accuracy(dataset.labels[list(valid)], booster.predict(dvalid).argmax(axis=1))


def _train_stgcn(dataset: Dataset, train: tuple[int, ...], valid: tuple[int, ...], epochs: int) -> tuple[MiniSTGCN, float]:
    torch.manual_seed(42)
    model = MiniSTGCN(num_classes=len(dataset.label_names))
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.CrossEntropyLoss()
    train_set = TensorDataset(torch.from_numpy(dataset.sequences[list(train)]), torch.from_numpy(dataset.labels[list(train)]))
    for _ in range(epochs):
        model.train()
        for batch_x, batch_y in DataLoader(train_set, batch_size=16, shuffle=True):
            optimizer.zero_grad()
            loss = criterion(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()
    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(dataset.sequences[list(valid)]))
    accuracy = _accuracy(dataset.labels[list(valid)], logits.argmax(dim=1).numpy())
    return model, accuracy


def _write_report(path: Path, report: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execute(config: TrainConfig) -> JsonObject:
    dataset = load_dataset(
        config.features_csv, config.sequence_npz, config.meta_json,
        config.min_samples_per_class,
    )
    train, valid = _split(dataset, config.validation_fraction, config.seed)
    report: JsonObject = {
        "profile": "coarse-distill-v1", "canonical_21": False,
        "teacher_distillation": True, "ready_for_training": True,
        "training_started": False, "reload_verified": False,
        "label_names": list(dataset.label_names),
        "excluded_underfilled_labels": list(dataset.excluded_labels),
        "label_counts": dict(Counter(dataset.label_names[int(index)] for index in dataset.labels)),
        "train_sample_ids": [dataset.sample_ids[index] for index in train],
        "validation_sample_ids": [dataset.sample_ids[index] for index in valid],
        "group_overlap": [],
    }
    if config.dry_run:
        _write_report(config.report_out, report)
        return report
    if config.candidate_dir.name != "candidates" or "models" in {part.lower() for part in config.candidate_dir.parts}:
        raise CoarseTrainingError("candidate directory is outside the allowed run boundary")
    if config.candidate_dir.exists():
        raise CoarseTrainingError(f"candidate directory already exists: {config.candidate_dir}")
    booster, xgb_accuracy = _train_xgb(dataset, train, valid, config.num_round)
    stgcn, stgcn_accuracy = _train_stgcn(dataset, train, valid, config.epochs)
    staging = config.candidate_dir.with_name(f".{config.candidate_dir.name}.staging")
    if staging.exists():
        raise CoarseTrainingError(f"candidate staging directory already exists: {staging}")
    staging.mkdir(parents=True)
    xgb_path = staging / "xgboost_coarse_action.json"
    stgcn_path = staging / "stgcn_coarse_activity.pth"
    booster.save_model(xgb_path)
    torch.save({"state_dict": stgcn.state_dict(), "label_map": list(dataset.label_names)}, stgcn_path)
    reloaded_xgb = xgb.Booster()
    reloaded_xgb.load_model(xgb_path)
    xgb_shape = reloaded_xgb.inplace_predict(dataset.features[:1]).shape
    checkpoint = torch.load(stgcn_path, map_location="cpu", weights_only=False)
    reloaded_stgcn = MiniSTGCN(num_classes=len(dataset.label_names))
    reloaded_stgcn.load_state_dict(checkpoint["state_dict"])
    with torch.no_grad():
        stgcn_shape = tuple(reloaded_stgcn(torch.from_numpy(dataset.sequences[:1])).shape)
    if xgb_shape != (1, len(dataset.label_names)) or stgcn_shape != (1, len(dataset.label_names)):
        raise CoarseTrainingError("candidate reload output shape mismatch")
    staging.rename(config.candidate_dir)
    report.update({
        "training_started": True, "reload_verified": True,
        "xgboost_validation_accuracy": xgb_accuracy,
        "stgcn_validation_accuracy": stgcn_accuracy,
        "candidate_sha256": {
            "xgboost_coarse_action.json": _sha256(config.candidate_dir / xgb_path.name),
            "stgcn_coarse_activity.pth": _sha256(config.candidate_dir / stgcn_path.name),
        },
    })
    _write_report(config.report_out, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate or train coarse-distillation candidate bundle")
    parser.add_argument("--features-csv", required=True)
    parser.add_argument("--sequence-npz", required=True)
    parser.add_argument("--meta-json", required=True)
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--min-samples-per-class", type=int, default=2)
    parser.add_argument("--num-round", type=int, default=50)
    parser.add_argument("--epochs", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = execute(TrainConfig(
            Path(args.features_csv), Path(args.sequence_npz), Path(args.meta_json),
            Path(args.candidate_dir), Path(args.report_out), args.dry_run, args.seed,
            args.validation_fraction, args.min_samples_per_class, args.num_round, args.epochs,
        ))
    except (CoarseTrainingError, OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 30
    print(f"ready={report['ready_for_training']} trained={report['training_started']} reload={report['reload_verified']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
