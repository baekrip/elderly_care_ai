from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.stgcn_model import MiniSTGCN


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train Mini ST-GCN on exported sequences")
    parser.add_argument("--input", default="experiments/behavior_training/sequences/stgcn_sequences.npz")
    parser.add_argument("--meta-in", default="experiments/behavior_training/reports/stgcn_sequence_meta.json")
    parser.add_argument("--model-out", default="server/models/stgcn_fall_local.pth")
    parser.add_argument("--report-out", default="experiments/behavior_training/reports/stgcn_train_report.json")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--class-weight-mode", choices=["none", "inverse"], default="inverse")
    parser.add_argument("--weight-power", type=float, default=1.0)
    parser.add_argument("--runs", type=int, default=3,
                        help="number of runs with different seeds (best model kept)")
    return parser


def build_class_weights(labels: torch.Tensor, num_classes: int, mode: str, weight_power: float) -> torch.Tensor | None:
    if mode == "none":
        return None
    counts = torch.bincount(labels, minlength=num_classes).float()
    weights = torch.ones(num_classes, dtype=torch.float32)
    nonzero = counts > 0
    if torch.any(nonzero):
        reference = counts[nonzero].mean()
        weights[nonzero] = torch.pow(reference / counts[nonzero], weight_power)
    return weights


def evaluate(model: MiniSTGCN, loader: DataLoader, label_names: list[str]) -> tuple[float, list[list[int]]]:
    """Return (accuracy, confusion_matrix)."""
    confusion = [[0 for _ in label_names] for _ in label_names]
    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for batch_x, batch_y in loader:
            logits = model(batch_x)
            predicted = logits.argmax(dim=1)
            correct += int((predicted == batch_y).sum())
            total += int(batch_y.numel())
            for truth, pred in zip(batch_y.tolist(), predicted.tolist()):
                confusion[int(truth)][int(pred)] += 1
    accuracy = float(correct / total) if total else 0.0
    return accuracy, confusion


def train_one_run(
    sequences: torch.Tensor,
    labels: torch.Tensor,
    label_names: list[str],
    args: argparse.Namespace,
    seed: int,
) -> tuple[dict, float, list[list[int]], int]:
    """Single training run. Returns (state_dict, best_accuracy, confusion, best_epoch)."""
    torch.manual_seed(seed)
    np.random.seed(seed)

    # shuffle before split
    indices = torch.randperm(len(sequences))
    shuffled_seq = sequences[indices]
    shuffled_lab = labels[indices]

    split_index = max(1, int(len(shuffled_seq) * 0.8))
    train_y = shuffled_lab[:split_index]
    train_dataset = TensorDataset(shuffled_seq[:split_index], shuffled_lab[:split_index])
    valid_dataset = TensorDataset(shuffled_seq[split_index:], shuffled_lab[split_index:]) if split_index < len(shuffled_seq) else TensorDataset(shuffled_seq[:0], shuffled_lab[:0])
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    valid_loader = DataLoader(valid_dataset, batch_size=args.batch_size) if len(valid_dataset) else None

    model = MiniSTGCN(num_classes=len(label_names))
    class_weights = build_class_weights(train_y, len(label_names), args.class_weight_mode, args.weight_power)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    best_acc = -1.0
    best_state = None
    best_epoch = 0
    best_confusion: list[list[int]] = []

    for epoch in range(args.epochs):
        model.train()
        epoch_loss = 0.0
        batches = 0
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad()
            logits = model(batch_x)
            loss = criterion(logits, batch_y)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
            batches += 1
        scheduler.step()

        avg_loss = epoch_loss / max(1, batches)

        if valid_loader is not None and len(valid_dataset):
            val_acc, confusion = evaluate(model, valid_loader, label_names)
        else:
            val_acc, confusion = 0.0, []

        lr_now = optimizer.param_groups[0]["lr"]
        marker = ""
        if val_acc > best_acc:
            best_acc = val_acc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch + 1
            best_confusion = confusion
            marker = " *best*"

        if (epoch + 1) % 2 == 0 or (epoch + 1) == args.epochs or marker:
            print(f"    epoch {epoch+1:>3}/{args.epochs}  loss={avg_loss:.4f}  val_acc={val_acc:.4f}  lr={lr_now:.6f}{marker}")

    return best_state, best_acc, best_confusion, best_epoch


def main() -> int:
    args = build_parser().parse_args()
    npz = np.load(args.input)
    sequences = torch.tensor(npz["sequences"], dtype=torch.float32)
    labels = torch.tensor(npz["labels"], dtype=torch.long)
    meta = json.loads(Path(args.meta_in).read_text(encoding="utf-8"))
    label_names = meta["label_names"]

    print(f"[stgcn] rows={len(sequences)} labels={label_names} runs={args.runs} epochs={args.epochs}")
    print("=" * 60)

    best_acc_global = -1.0
    best_state_global = None
    best_confusion_global: list[list[int]] = []
    best_seed = 0
    best_epoch_global = 0
    run_results: list[dict] = []

    for run_idx in range(args.runs):
        seed = 42 + run_idx * 13
        print(f"\n--- Run {run_idx+1}/{args.runs} (seed={seed}) ---")
        state, acc, confusion, best_ep = train_one_run(
            sequences, labels, label_names, args, seed
        )
        run_results.append({"run": run_idx + 1, "seed": seed, "accuracy": acc, "best_epoch": best_ep})
        print(f"  -> best_accuracy={acc:.4f}  best_epoch={best_ep}")

        if acc > best_acc_global:
            best_acc_global = acc
            best_state_global = state
            best_confusion_global = confusion
            best_seed = seed
            best_epoch_global = best_ep

    print("\n" + "=" * 60)
    print(f"[stgcn] Best: seed={best_seed} accuracy={best_acc_global:.4f} epoch={best_epoch_global}")

    model_path = Path(args.model_out)
    model_path.parent.mkdir(parents=True, exist_ok=True)

    # rebuild model and load best weights
    final_model = MiniSTGCN(num_classes=len(label_names))
    final_model.load_state_dict(best_state_global)

    torch.save(
        {
            "state_dict": final_model.state_dict(),
            "label_map": label_names,
            "target_frames": int(sequences.shape[2]),
            "joints": int(sequences.shape[3]),
            "persons": int(sequences.shape[4]),
        },
        model_path,
    )

    report_path = Path(args.report_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    class_weights = build_class_weights(labels, len(label_names), args.class_weight_mode, args.weight_power)
    report = {
        "rows": int(len(sequences)),
        "train_rows": int(int(len(sequences) * 0.8)),
        "valid_rows": int(len(sequences)) - int(int(len(sequences) * 0.8)),
        "label_names": label_names,
        "valid_accuracy": best_acc_global,
        "best_seed": best_seed,
        "best_epoch": best_epoch_global,
        "num_runs": args.runs,
        "class_weight_mode": args.class_weight_mode,
        "class_weights": class_weights.tolist() if class_weights is not None else None,
        "confusion_matrix": best_confusion_global,
        "all_runs": run_results,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"trained_stgcn_rows={len(sequences)} valid_accuracy={best_acc_global:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
