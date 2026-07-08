"""Model evaluation tool — compare old vs new model on validation data.

Evaluates XGBoost, ST-GCN (fall-binary), and ST-GCN (multi-task) models
on a common validation set and produces a comparison report.

Usage:
    python tools/evaluate_model.py \
        --eval-data experiments/behavior_training/sequences/stgcn_sequences_fall.npz \
        --meta experiments/behavior_training/reports/stgcn_sequence_meta_fall.json \
        --old-model device_transfer/Edge/server/models/stgcn_fall_binary.pth \
        --new-model device_transfer/Edge/server/models/stgcn_fall_binary_new.pth \
        --report-out reports/model_comparison.json

NOTE: This tool does NOT modify any model file. Read-only evaluation only.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "device_transfer" / "Edge"))

try:
    import torch
except ImportError:
    print("[ERROR] PyTorch not installed.")
    sys.exit(1)


# ── metrics ─────────────────────────────────────────────────────
def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray, label_names: list[str]) -> dict[str, Any]:
    num_classes = len(label_names)
    confusion = np.zeros((num_classes, num_classes), dtype=int)
    for t, p in zip(y_true, y_pred):
        if 0 <= t < num_classes and 0 <= p < num_classes:
            confusion[t][p] += 1

    per_class: list[dict[str, Any]] = []
    for i, name in enumerate(label_names):
        tp = int(confusion[i][i])
        fp = int(confusion[:, i].sum() - tp)
        fn = int(confusion[i, :].sum() - tp)
        tn = int(confusion.sum() - tp - fp - fn)
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        support = int(confusion[i, :].sum())
        per_class.append({
            "label": name,
            "label_index": i,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "support": support,
        })

    total = int(y_true.shape[0])
    correct = int((y_true == y_pred).sum())
    accuracy = correct / max(total, 1)

    # macro averages
    macro_precision = np.mean([c["precision"] for c in per_class])
    macro_recall = np.mean([c["recall"] for c in per_class])
    macro_f1 = np.mean([c["f1"] for c in per_class])

    # danger recall (last group)
    danger_labels = set()
    for i, name in enumerate(label_names):
        upper = name.upper()
        if any(tok in upper for tok in ("FALL", "DROP", "COLLAPSE", "DANGER")):
            danger_labels.add(i)

    danger_tp = sum(c["tp"] for c in per_class if c["label_index"] in danger_labels)
    danger_fn = sum(c["fn"] for c in per_class if c["label_index"] in danger_labels)
    danger_recall = danger_tp / max(danger_tp + danger_fn, 1)

    return {
        "accuracy": round(accuracy, 6),
        "total": total,
        "correct": correct,
        "macro_precision": round(float(macro_precision), 6),
        "macro_recall": round(float(macro_recall), 6),
        "macro_f1": round(float(macro_f1), 6),
        "danger_recall": round(danger_recall, 6),
        "per_class": per_class,
        "confusion_matrix": confusion.tolist(),
    }


# ── model loading ───────────────────────────────────────────────
def load_stgcn_model(model_path: str) -> tuple[Any, list[str], str]:
    """Load ST-GCN model (fall-binary or multi-task). Returns (model, label_names, model_type)."""
    checkpoint = torch.load(model_path, map_location="cpu")
    model_type = checkpoint.get("model_type", "MiniSTGCN")

    if model_type == "MultiTaskSTGCN":
        from tools.train_stgcn_activity import MultiTaskSTGCN
        num_activity = checkpoint["num_activity_classes"]
        num_risk = checkpoint["num_risk_classes"]
        model = MultiTaskSTGCN(num_activity, num_risk)
        model.load_state_dict(checkpoint["state_dict"])
        label_names = checkpoint.get("activity_label_map", [])
    else:
        from shared.stgcn_model import MiniSTGCN
        label_names = checkpoint.get("label_map", ["NORMAL", "DROP"])
        model = MiniSTGCN(num_classes=len(label_names))
        model.load_state_dict(checkpoint["state_dict"])

    model.eval()
    return model, label_names, model_type


def evaluate_model(model: Any, sequences: np.ndarray, labels: np.ndarray, model_type: str) -> np.ndarray:
    """Run inference and return predicted labels."""
    x = torch.tensor(sequences, dtype=torch.float32)
    predictions = []

    with torch.no_grad():
        for i in range(0, len(x), 32):
            batch = x[i:i+32]
            output = model(batch)
            if isinstance(output, tuple):
                # multi-task: use activity head
                logits = output[0]
            else:
                logits = output
            preds = logits.argmax(dim=1).cpu().numpy()
            predictions.extend(preds)

    return np.array(predictions)


# ── main ────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Model evaluation & comparison")
    parser.add_argument("--eval-data", type=str, required=True, help="NPZ validation data")
    parser.add_argument("--meta", type=str, required=True, help="Meta JSON")
    parser.add_argument("--old-model", type=str, required=True, help="Old model .pth")
    parser.add_argument("--new-model", type=str, default=None, help="New model .pth (optional)")
    parser.add_argument("--report-out", type=str, default="reports/model_comparison.json")
    args = parser.parse_args()

    # load data
    data = np.load(args.eval_data)
    with open(args.meta, "r", encoding="utf-8") as f:
        meta = json.load(f)

    sequences = data["sequences"]
    labels = data.get("activity_labels", data.get("labels"))
    label_names = meta.get("activity_label_names", meta.get("label_names", []))

    print(f"Loaded {len(sequences)} sequences, {len(label_names)} classes")

    # evaluate old model
    print(f"\n=== Old model: {args.old_model} ===")
    old_model, old_labels, old_type = load_stgcn_model(args.old_model)
    old_preds = evaluate_model(old_model, sequences, labels, old_type)

    # remap if label sets differ
    if len(old_labels) != len(label_names):
        print(f"  [WARN] Label count mismatch: old={len(old_labels)}, eval={len(label_names)}")
        eval_label_names = old_labels  # use old model's labels for comparison
    else:
        eval_label_names = label_names

    old_metrics = compute_metrics(labels, old_preds, eval_label_names)
    print(f"  accuracy={old_metrics['accuracy']}, danger_recall={old_metrics['danger_recall']}")
    print(f"  macro_f1={old_metrics['macro_f1']}")

    report: dict[str, Any] = {
        "eval_data": args.eval_data,
        "total_sequences": len(sequences),
        "label_names": eval_label_names,
        "old_model": {
            "path": args.old_model,
            "type": old_type,
            "metrics": old_metrics,
        },
    }

    # evaluate new model if provided
    if args.new_model:
        print(f"\n=== New model: {args.new_model} ===")
        new_model, new_labels, new_type = load_stgcn_model(args.new_model)
        new_preds = evaluate_model(new_model, sequences, labels, new_type)
        new_metrics = compute_metrics(labels, new_preds, eval_label_names)
        print(f"  accuracy={new_metrics['accuracy']}, danger_recall={new_metrics['danger_recall']}")
        print(f"  macro_f1={new_metrics['macro_f1']}")

        report["new_model"] = {
            "path": args.new_model,
            "type": new_type,
            "metrics": new_metrics,
        }

        # comparison
        acc_diff = new_metrics["accuracy"] - old_metrics["accuracy"]
        recall_diff = new_metrics["danger_recall"] - old_metrics["danger_recall"]
        f1_diff = new_metrics["macro_f1"] - old_metrics["macro_f1"]

        gate_passed = recall_diff >= 0  # danger recall must not decrease
        report["comparison"] = {
            "accuracy_diff": round(acc_diff, 6),
            "danger_recall_diff": round(recall_diff, 6),
            "macro_f1_diff": round(f1_diff, 6),
            "gate_passed": gate_passed,
            "recommendation": "REPLACE" if gate_passed and acc_diff >= 0 else "KEEP_OLD",
        }
        print(f"\n=== Comparison ===")
        print(f"  accuracy_diff={acc_diff:+.4f}, danger_recall_diff={recall_diff:+.4f}")
        print(f"  gate_passed={gate_passed}, recommendation={report['comparison']['recommendation']}")

    # save report
    Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report_out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Report → {args.report_out}")


if __name__ == "__main__":
    main()
