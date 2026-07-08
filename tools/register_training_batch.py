from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv"}
LABEL_SUFFIX = ".json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register a repeat-training data batch without starting training")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--registry", default="experiments/behavior_training/dataset_registry.jsonl")
    parser.add_argument("--report-out", default="experiments/behavior_training/reports/latest_batch_registry.json")
    return parser


def find_pairs(source_dir: Path) -> list[dict[str, str]]:
    videos = {
        path.stem: path
        for path in sorted(source_dir.iterdir())
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    }
    labels = {
        path.stem: path
        for path in sorted(source_dir.iterdir())
        if path.is_file() and path.suffix.lower() == LABEL_SUFFIX
    }
    pairs: list[dict[str, str]] = []
    for stem in sorted(videos.keys() & labels.keys()):
        pairs.append({"sample_id": stem, "video_path": str(videos[stem]), "label_path": str(labels[stem])})
    return pairs


def load_existing_batch_ids(registry: Path) -> set[str]:
    if not registry.exists():
        return set()
    found: set[str] = set()
    for line in registry.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("batch_id"):
            found.add(str(payload["batch_id"]))
    return found


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    source_dir = Path(args.source_dir)
    registry = Path(args.registry)
    report_out = Path(args.report_out)

    if not source_dir.exists() or not source_dir.is_dir():
        write_report(report_out, {"status": "failed", "reason": "source_dir_missing", "training_started": False})
        return 2

    pairs = find_pairs(source_dir)
    existing_batch_ids = load_existing_batch_ids(registry)
    if args.batch_id in existing_batch_ids:
        write_report(
            report_out,
            {
                "status": "failed",
                "reason": "duplicate_batch_id",
                "batch_id": args.batch_id,
                "training_started": False,
            },
        )
        return 1

    record = {
        "batch_id": args.batch_id,
        "source_dir": str(source_dir),
        "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "matched_pairs": len(pairs),
        "samples": pairs,
        "training_started": False,
    }
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_report(report_out, {"status": "registered", **record})
    print(f"registered batch_id={args.batch_id} matched_pairs={len(pairs)} training_started=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
