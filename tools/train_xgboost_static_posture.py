from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.static_posture_training import (
    ContractError,
    TrainConfig,
    execute,
)
from tools.static_posture_metrics import classification_metrics

__all__ = [
    "ContractError",
    "TrainConfig",
    "build_parser",
    "classification_metrics",
    "execute",
    "main",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or train the static/posture XGBoost classifier",
    )
    parser.add_argument("--features-csv", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--model-out", help="required for non-dry-run training")
    parser.add_argument("--dry-run", action="store_true", help="validate and write report only")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--min-samples-per-class", type=int, default=2)
    parser.add_argument("--num-round", type=int, default=50)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--eta", type=float, default=0.1)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = TrainConfig(
        features_csv=Path(args.features_csv),
        report_out=Path(args.report_out),
        model_out=Path(args.model_out) if args.model_out else None,
        dry_run=args.dry_run,
        seed=args.seed,
        validation_fraction=args.validation_fraction,
        min_samples_per_class=args.min_samples_per_class,
        num_round=args.num_round,
        max_depth=args.max_depth,
        eta=args.eta,
    )
    try:
        report = execute(config)
    except (ContractError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"mode={report['mode']} rows={report['rows']} "
        f"train_rows={report['train_rows']} valid_rows={report['valid_rows']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
