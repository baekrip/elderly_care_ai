from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import xgboost as xgb

PREFERRED_LABEL_ORDER = ["NORMAL", "SUSPECT", "DANGER", "DROP", "WANDER"]


def build_label_order(values: list[str]) -> list[str]:
    unique = {str(value) for value in values}
    ordered = [label for label in PREFERRED_LABEL_ORDER if label in unique]
    ordered.extend(sorted(unique - set(ordered)))
    return ordered


def load_rows(csv_path: Path, label_column: str) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    with csv_path.open("r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
    if not rows:
        raise RuntimeError("empty feature csv")
    if label_column not in rows[0]:
        raise RuntimeError(f"missing label column: {label_column}")
    label_order = build_label_order([str(row[label_column]) for row in rows])
    feature_columns = [
        key
        for key in rows[0].keys()
        if key not in {"sample_id", "video_path", "window_role", "tier_label", "model_label", "action_type", "action_name", "used_frames"}
    ]
    features = np.asarray(
        [[float(row[column]) for column in feature_columns] for row in rows],
        dtype=np.float32,
    )
    labels = np.asarray([label_order.index(str(row[label_column])) for row in rows], dtype=np.int32)
    return features, labels, feature_columns, label_order


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train XGBoost tier classifier")
    parser.add_argument("--features-csv", default="experiments/behavior_training/features/xgboost_tier_features.csv")
    parser.add_argument("--model-out", default="edge/models/xgboost_tier.json")
    parser.add_argument("--meta-out", default="experiments/behavior_training/reports/xgboost_tier_meta.json")
    parser.add_argument("--num-round", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--eta", type=float, default=0.08)
    parser.add_argument("--label-column", default="model_label")
    parser.add_argument("--early-stopping", type=int, default=30,
                        help="early stopping rounds (0=disable)")
    parser.add_argument("--runs", type=int, default=3,
                        help="number of runs with different seeds (best model kept)")
    return parser


def train_one_run(
    features: np.ndarray,
    labels_arr: np.ndarray,
    feature_columns: list[str],
    label_order: list[str],
    args: argparse.Namespace,
    seed: int,
) -> tuple[xgb.Booster, float, int, np.ndarray, np.ndarray]:
    """Single training run with a given seed. Returns (booster, accuracy, best_round, valid_x, valid_y)."""
    rng = np.random.default_rng(seed)
    indices = rng.permutation(len(features))
    shuffled_x = features[indices]
    shuffled_y = labels_arr[indices]

    split_index = int(len(shuffled_x) * 0.8)
    train_x = shuffled_x[:split_index]
    train_y = shuffled_y[:split_index]
    valid_x = shuffled_x[split_index:]
    valid_y = shuffled_y[split_index:]

    dtrain = xgb.DMatrix(train_x, label=train_y, feature_names=feature_columns)
    dvalid = xgb.DMatrix(valid_x, label=valid_y, feature_names=feature_columns)

    params = {
        "objective": "multi:softprob",
        "num_class": len(label_order),
        "max_depth": args.max_depth,
        "eta": args.eta,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "eval_metric": ["mlogloss", "merror"],
        "tree_method": "hist",
        "seed": seed,
    }

    callbacks = []
    if args.early_stopping > 0:
        callbacks.append(xgb.callback.EarlyStopping(
            rounds=args.early_stopping,
            metric_name="merror",
            data_name="valid",
            maximize=False,
            save_best=True,
        ))

    booster = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=args.num_round,
        evals=[(dtrain, "train"), (dvalid, "valid")],
        verbose_eval=50,
        callbacks=callbacks,
    )

    best_round = getattr(booster, "best_iteration", args.num_round)
    predictions = booster.inplace_predict(valid_x)
    predicted_labels = predictions.argmax(axis=1)
    accuracy = float((predicted_labels == valid_y).mean()) if len(valid_y) else 0.0

    return booster, accuracy, best_round, valid_x, valid_y


def main() -> int:
    args = build_parser().parse_args()
    csv_path = Path(args.features_csv)
    model_path = Path(args.model_out)
    meta_path = Path(args.meta_out)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    features, labels_arr, feature_columns, label_order = load_rows(csv_path, args.label_column)
    print(f"[xgboost] rows={len(features)} labels={label_order} runs={args.runs} max_round={args.num_round} early_stop={args.early_stopping}")
    print("=" * 60)

    best_acc = -1.0
    best_booster = None
    best_seed = 0
    best_round = 0
    run_results: list[dict] = []

    for run_idx in range(args.runs):
        seed = 42 + run_idx * 7
        print(f"\n--- Run {run_idx+1}/{args.runs} (seed={seed}) ---")
        booster, accuracy, used_round, valid_x, valid_y = train_one_run(
            features, labels_arr, feature_columns, label_order, args, seed
        )
        run_results.append({"run": run_idx + 1, "seed": seed, "accuracy": accuracy, "best_round": used_round})
        print(f"  -> accuracy={accuracy:.4f}  best_round={used_round}")

        if accuracy > best_acc:
            best_acc = accuracy
            best_booster = booster
            best_seed = seed
            best_round = used_round

    print("\n" + "=" * 60)
    print(f"[xgboost] Best: run seed={best_seed} accuracy={best_acc:.4f} round={best_round}")

    best_booster.save_model(model_path)

    meta = {
        "label_order": label_order,
        "label_column": args.label_column,
        "feature_columns": feature_columns,
        "rows": int(len(features)),
        "train_rows": int(int(len(features) * 0.8)),
        "valid_rows": int(len(features)) - int(int(len(features) * 0.8)),
        "valid_accuracy": best_acc,
        "best_seed": best_seed,
        "best_round": best_round,
        "num_runs": args.runs,
        "all_runs": run_results,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"trained_xgboost_rows={len(features)} valid_accuracy={best_acc:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
