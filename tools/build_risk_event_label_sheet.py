from __future__ import annotations

import argparse
import json
from pathlib import Path


TARGET_RISK_LABELS = [
    "fall_detected",
    "running_over_speed",
    "collision_suspected",
    "faint_static",
]


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Registry row is not an object: {path}")
        rows.append(payload)
    return rows


def build_label_rows(registry_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    label_rows: list[dict[str, object]] = []
    for batch in registry_rows:
        batch_id = str(batch.get("batch_id", ""))
        samples = batch.get("samples", [])
        if not isinstance(samples, list):
            raise ValueError(f"Batch {batch_id} samples must be a list")
        for sample in samples:
            if not isinstance(sample, dict):
                raise ValueError(f"Batch {batch_id} sample must be an object")
            label_rows.append(
                {
                    "batch_id": batch_id,
                    "sample_id": str(sample.get("sample_id", "")),
                    "video_path": str(sample.get("video_path", "")),
                    "label_path": str(sample.get("label_path", "")),
                    "risk_label": "needs_review",
                    "target_labels": TARGET_RISK_LABELS,
                    "start_ms": None,
                    "end_ms": None,
                    "notes": "",
                    "training_ready": False,
                }
            )
    return label_rows


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a risk event label review sheet")
    parser.add_argument("--registry", type=Path, required=True, help="Training batch registry JSONL.")
    parser.add_argument("--output-jsonl", type=Path, required=True, help="Output risk label review JSONL.")
    parser.add_argument("--report-out", type=Path, required=True, help="Output report JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry_rows = _read_jsonl(args.registry)
    label_rows = build_label_rows(registry_rows)
    write_jsonl(args.output_jsonl, label_rows)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(
        json.dumps(
            {
                "status": "created",
                "rows": len(label_rows),
                "target_labels": TARGET_RISK_LABELS,
                "training_started": False,
                "output_jsonl": str(args.output_jsonl),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "created", "rows": len(label_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
