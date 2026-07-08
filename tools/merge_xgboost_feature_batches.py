from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge XGBoost feature CSV batches without running training")
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--report-out", default="experiments/behavior_training/reports/xgboost_batch_merge_report.json")
    parser.add_argument("--allow-duplicates", action="store_true")
    return parser


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    args = build_parser().parse_args()
    input_paths = [Path(item) for item in args.inputs]
    report_out = Path(args.report_out)
    missing = [str(path) for path in input_paths if not path.exists()]
    if missing:
        write_report(report_out, {"status": "failed", "reason": "missing_inputs", "missing": missing})
        return 2

    rows: list[dict[str, str]] = []
    fieldnames: list[str] = []
    seen_sample_ids: set[str] = set()
    duplicate_sample_ids: set[str] = set()
    for path in input_paths:
        for row in read_rows(path):
            sample_id = str(row.get("sample_id", ""))
            if sample_id:
                if sample_id in seen_sample_ids:
                    duplicate_sample_ids.add(sample_id)
                seen_sample_ids.add(sample_id)
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
            rows.append(row)

    if duplicate_sample_ids and not args.allow_duplicates:
        write_report(
            report_out,
            {
                "status": "failed",
                "reason": "duplicate_sample_ids",
                "duplicate_sample_ids": sorted(duplicate_sample_ids),
                "rows": len(rows),
            },
        )
        return 1

    output_csv = Path(args.output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    write_report(
        report_out,
        {
            "status": "merged",
            "inputs": [str(path) for path in input_paths],
            "output_csv": str(output_csv),
            "rows": len(rows),
            "duplicate_sample_ids": sorted(duplicate_sample_ids),
            "training_started": False,
        },
    )
    print(f"merged rows={len(rows)} output={output_csv} training_started=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
