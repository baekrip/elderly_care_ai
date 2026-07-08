"""Multi-task ST-GCN trainer: activity head + risk head.

Trains a shared-backbone ST-GCN with two classification heads:
  - activity_head: 21-class activity label (standing, walking, ..., collapse_out_of_frame)
  - risk_head: 3-class risk tier (NORMAL, SUSPECT, DANGER)

Usage:
    python tools/train_stgcn_activity.py \
        --input experiments/behavior_training/sequences/stgcn_activity_sequences.npz \
        --meta-in experiments/behavior_training/reports/stgcn_activity_meta.json \
        --model-out device_transfer/Edge/server/models/stgcn_multitask.pth \
        --report-out experiments/behavior_training/reports/stgcn_multitask_report.json \
        --runs 5 --epochs 30

NOTE: This tool creates the training code structure only. Actual training
requires user-initiated execution per project policy (학습참고.md).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "device_transfer" / "Edge"))

try:
    import torch
    from torch.utils.data import DataLoader, TensorDataset
except ImportError:
    print("[ERROR] PyTorch not installed. Run: pip install torch")
    sys.exit(1)

from device_transfer.Edge.shared.labels import TARGET_ACTION_LABELS
from tools.static_feature_export_io import JsonObject
from tools.stgcn_activity_model import MultiTaskSTGCN, RunResult, train_one_run
from tools.stgcn_activity_training import (
    TrainingContractError,
    build_dry_run_report,
    group_safe_split,
    load_training_dataset,
)
from tools.stgcn_sequence_input import RISK_LABELS


def compute_class_weights(labels: np.ndarray, num_classes: int, mode: str = "inverse") -> torch.Tensor:
    counts = np.bincount(labels, minlength=num_classes).astype(np.float64)
    counts = np.maximum(counts, 1.0)
    if mode == "inverse":
        weights = 1.0 / counts
    else:
        weights = np.ones(num_classes)
    weights = weights / weights.sum() * num_classes
    return torch.tensor(weights, dtype=torch.float32)


def detach_run_result(
    result: RunResult,
    *,
    run_number: int,
    elapsed_seconds: float,
) -> tuple[JsonObject, dict[str, torch.Tensor]]:
    state = result["best_state"]
    if state is None:
        raise TrainingContractError("training run did not produce a checkpoint state")
    report: JsonObject = {
        "best_val_combined_acc": result["best_val_combined_acc"],
        "history": result["history"],
        "run": run_number,
        "elapsed_sec": round(elapsed_seconds, 2),
    }
    return report, state


# ── main ────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-task ST-GCN trainer (activity + risk)")
    parser.add_argument("--input", type=str, required=True, help="NPZ sequences path")
    parser.add_argument("--meta-in", type=str, required=True, help="Meta JSON path")
    parser.add_argument("--model-out", type=str, help="Output candidate .pth checkpoint")
    parser.add_argument("--report-out", type=str, required=True, help="Output training report JSON")
    parser.add_argument("--runs", type=int, default=5, help="Number of training runs")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--activity-loss-weight", type=float, default=1.0)
    parser.add_argument("--risk-loss-weight", type=float, default=1.0)
    parser.add_argument("--class-weight-mode", choices=["inverse", "uniform"], default="inverse")
    parser.add_argument("--dry-run", action="store_true", help="validate and report without training")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-samples-per-class", type=int, default=2)
    args = parser.parse_args()

    try:
        dry_report = build_dry_run_report(
            Path(args.input),
            Path(args.meta_in),
            seed=args.seed,
            validation_fraction=args.val_split,
            minimum=args.min_samples_per_class,
        )
    except (OSError, TrainingContractError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if args.dry_run:
        report_path = Path(args.report_out)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(dry_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[OK] Dry-run report -> {report_path}")
        return
    if not args.model_out:
        parser.error("--model-out is required unless --dry-run is used")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")

    contract = load_training_dataset(Path(args.input), Path(args.meta_in), args.min_samples_per_class)
    data = {
        "sequences": contract.sequences,
        "activity_labels": contract.activity_labels,
        "risk_labels": contract.risk_labels,
        "activity_label_names": list(TARGET_ACTION_LABELS),
        "risk_label_names": list(RISK_LABELS),
    }
    sequences = torch.tensor(contract.sequences, dtype=torch.float32)
    activity_labels = torch.tensor(contract.activity_labels, dtype=torch.long)
    risk_labels = torch.tensor(contract.risk_labels, dtype=torch.long)

    n = len(sequences)
    num_activity_classes = len(data["activity_label_names"])
    num_risk_classes = len(data["risk_label_names"])

    activity_weights = compute_class_weights(data["activity_labels"], num_activity_classes, args.class_weight_mode)
    risk_weights = compute_class_weights(data["risk_labels"], num_risk_classes, args.class_weight_mode)

    best_overall = None
    all_runs: list[dict] = []

    for run_idx in range(args.runs):
        print(f"\n=== Run {run_idx + 1}/{args.runs} ===")
        train_rows, val_rows = group_safe_split(contract, args.val_split, args.seed + run_idx)
        train_idx = torch.tensor(train_rows, dtype=torch.long)
        val_idx = torch.tensor(val_rows, dtype=torch.long)

        train_ds = TensorDataset(sequences[train_idx], activity_labels[train_idx], risk_labels[train_idx])
        val_ds = TensorDataset(sequences[val_idx], activity_labels[val_idx], risk_labels[val_idx])
        train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=args.batch_size)

        model = MultiTaskSTGCN(num_activity_classes, num_risk_classes)
        start = time.perf_counter()
        result = train_one_run(
            model, train_loader, val_loader,
            epochs=args.epochs, lr=args.lr, device=device,
            activity_weight=args.activity_loss_weight,
            risk_weight=args.risk_loss_weight,
            activity_class_weights=activity_weights,
            risk_class_weights=risk_weights,
        )
        elapsed = time.perf_counter() - start
        report_run, run_state = detach_run_result(
            result,
            run_number=run_idx + 1,
            elapsed_seconds=elapsed,
        )
        all_runs.append(report_run)
        print(f"  best_val_combined_acc={report_run['best_val_combined_acc']}, elapsed={report_run['elapsed_sec']}s")

        if best_overall is None or report_run["best_val_combined_acc"] > best_overall["best_val_combined_acc"]:
            best_overall = report_run
            best_state = run_state

    # save model
    Path(args.model_out).parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "state_dict": best_state,
        "activity_label_map": data["activity_label_names"],
        "risk_label_map": data["risk_label_names"],
        "num_activity_classes": num_activity_classes,
        "num_risk_classes": num_risk_classes,
        "model_type": "MultiTaskSTGCN",
    }, args.model_out)
    print(f"\n[OK] Model → {args.model_out}")

    # save report
    report = {
        "model_type": "MultiTaskSTGCN",
        "input": args.input,
        "total_sequences": n,
        "num_activity_classes": num_activity_classes,
        "num_risk_classes": num_risk_classes,
        "activity_label_names": data["activity_label_names"],
        "risk_label_names": data["risk_label_names"],
        "runs": all_runs,
        "best_run": best_overall,
        "config": {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "lr": args.lr,
            "val_split": args.val_split,
            "activity_loss_weight": args.activity_loss_weight,
            "risk_loss_weight": args.risk_loss_weight,
            "class_weight_mode": args.class_weight_mode,
        },
    }
    Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Report → {args.report_out}")


if __name__ == "__main__":
    main()
