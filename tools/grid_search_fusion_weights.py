"""Grid search for XGBoost + ST-GCN fusion weights.

Searches over stgcn_weight / xgboost_weight / decision_threshold
combinations using labeled evaluation data (JSONL) to optimize
danger recall while constraining false-positive rate.

Usage:
    python tools/grid_search_fusion_weights.py \
        --eval-data experiments/behavior_training/eval_events.jsonl \
        --stgcn-weights 0.4 0.5 0.6 0.7 0.8 \
        --decision-thresholds 0.5 0.6 0.7 0.8 \
        --report reports/fusion_grid_search.json

Eval data format (JSONL, one event per line):
    {"ground_truth": "DANGER", "xgboost_label": "FALL", "xgboost_probability": 0.82,
     "stgcn_label": "FALL", "stgcn_probability": 0.91}
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "device_transfer" / "Edge"))

from server.services.model_input_window import fuse_model_outputs


# ── label helpers ───────────────────────────────────────────────
DANGER_LABELS = {
    "DANGER", "FALL", "GRADUAL_FALL", "GRADUAL_COLLAPSE",
    "LOSS_OF_BALANCE", "DROP", "DANGER_DROP",
    "FORWARD_COLLAPSE_FROM_STANDING", "FORWARD_COLLAPSE_FROM_SITTING",
    "SIDEWAYS_COLLAPSE", "BACKWARD_FALL", "CHAIR_SLIDE_FALL",
    "BED_ROLL_FALL", "SYNCOPE_COLLAPSE",
    "fall_confirmed", "fall_detected",
}


def is_danger_ground_truth(label: str) -> bool:
    return label.upper() in DANGER_LABELS or "DANGER" in label.upper() or "FALL" in label.upper()


def is_danger_prediction(fusion_result: dict) -> bool:
    label = str(fusion_result.get("final_label", "")).upper()
    prob = float(fusion_result.get("final_fall_probability", 0))
    threshold = float(fusion_result.get("decision_threshold", 0.5))
    return label == "FALL_CONFIRMED" or prob >= threshold


# ── evaluation logic ───────────────────────────────────────────
def evaluate_combination(
    events: list[dict[str, Any]],
    stgcn_weight: float,
    decision_threshold: float,
) -> dict[str, Any]:
    xgboost_weight = round(1.0 - stgcn_weight, 4)
    tp = fp = fn = tn = 0

    for event in events:
        gt_danger = is_danger_ground_truth(str(event.get("ground_truth", "")))
        fusion = fuse_model_outputs(
            xgboost_label=event.get("xgboost_label", "NORMAL"),
            xgboost_probability=float(event.get("xgboost_probability", 0.0)),
            stgcn_label=event.get("stgcn_label"),
            stgcn_probability=float(event.get("stgcn_probability", 0.0)) if event.get("stgcn_probability") is not None else None,
            stgcn_weight=stgcn_weight,
            xgboost_weight=xgboost_weight,
            decision_threshold=decision_threshold,
        )
        pred_danger = is_danger_prediction(fusion)

        if gt_danger and pred_danger:
            tp += 1
        elif gt_danger and not pred_danger:
            fn += 1
        elif not gt_danger and pred_danger:
            fp += 1
        else:
            tn += 1

    total = tp + fp + fn + tn
    recall = tp / max(tp + fn, 1)
    precision = tp / max(tp + fp, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    fpr = fp / max(fp + tn, 1)

    return {
        "stgcn_weight": stgcn_weight,
        "xgboost_weight": xgboost_weight,
        "decision_threshold": decision_threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "total": total,
        "recall": round(recall, 6),
        "precision": round(precision, 6),
        "f1": round(f1, 6),
        "fpr": round(fpr, 6),
    }


# ── main ────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Grid search fusion weights")
    parser.add_argument("--eval-data", type=str, required=True, help="JSONL evaluation file")
    parser.add_argument(
        "--stgcn-weights", nargs="+", type=float,
        default=[0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        help="ST-GCN weight candidates (xgboost_weight = 1 - stgcn_weight)",
    )
    parser.add_argument(
        "--decision-thresholds", nargs="+", type=float,
        default=[0.4, 0.5, 0.6, 0.7, 0.8],
        help="Decision threshold candidates",
    )
    parser.add_argument("--report", type=str, default="reports/fusion_grid_search.json")
    parser.add_argument(
        "--sort-by", choices=["recall", "f1", "precision", "fpr"],
        default="recall",
        help="Primary sort metric (default: recall)",
    )
    parser.add_argument("--max-fpr", type=float, default=0.15, help="Maximum false positive rate filter")
    args = parser.parse_args()

    # load eval data
    eval_path = Path(args.eval_data)
    if not eval_path.exists():
        print(f"[ERROR] Eval data not found: {eval_path}")
        print("Expected JSONL format: one event per line with ground_truth, xgboost_label, "
              "xgboost_probability, stgcn_label, stgcn_probability")
        sys.exit(1)

    events = []
    for line in eval_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))

    print(f"Loaded {len(events)} evaluation events from {eval_path}")
    danger_count = sum(1 for e in events if is_danger_ground_truth(str(e.get("ground_truth", ""))))
    normal_count = len(events) - danger_count
    print(f"  DANGER ground truth: {danger_count}, NORMAL ground truth: {normal_count}")

    # grid search
    combinations = list(itertools.product(args.stgcn_weights, args.decision_thresholds))
    print(f"Testing {len(combinations)} combinations...")

    results = []
    for stgcn_w, threshold in combinations:
        result = evaluate_combination(events, stgcn_w, threshold)
        results.append(result)

    # sort: primary by sort_by desc, secondary by fpr asc
    if args.sort_by == "fpr":
        results.sort(key=lambda r: r["fpr"])
    else:
        results.sort(key=lambda r: (-r[args.sort_by], r["fpr"]))

    # filter by max fpr
    filtered = [r for r in results if r["fpr"] <= args.max_fpr]

    # print top 10
    print(f"\n=== Top 10 (sorted by {args.sort_by}, max FPR {args.max_fpr}) ===")
    print(f"{'stgcn_w':>8} {'xgb_w':>6} {'thresh':>7} {'recall':>8} {'prec':>8} {'f1':>8} {'fpr':>8} {'tp':>4} {'fp':>4} {'fn':>4} {'tn':>4}")
    print("-" * 85)
    for r in filtered[:10]:
        print(
            f"{r['stgcn_weight']:8.2f} {r['xgboost_weight']:6.2f} {r['decision_threshold']:7.2f} "
            f"{r['recall']:8.4f} {r['precision']:8.4f} {r['f1']:8.4f} {r['fpr']:8.4f} "
            f"{r['tp']:4d} {r['fp']:4d} {r['fn']:4d} {r['tn']:4d}"
        )

    # best
    best = filtered[0] if filtered else results[0]
    print(f"\n=== Best ===")
    print(f"  stgcn_weight={best['stgcn_weight']}, xgboost_weight={best['xgboost_weight']}, "
          f"decision_threshold={best['decision_threshold']}")
    print(f"  recall={best['recall']}, precision={best['precision']}, f1={best['f1']}, fpr={best['fpr']}")

    # save report
    report = {
        "eval_data": str(eval_path),
        "total_events": len(events),
        "danger_ground_truth": danger_count,
        "normal_ground_truth": normal_count,
        "sort_by": args.sort_by,
        "max_fpr": args.max_fpr,
        "best": best,
        "all_results": results,
        "filtered_by_fpr": filtered,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Report → {report_path}")


if __name__ == "__main__":
    main()
