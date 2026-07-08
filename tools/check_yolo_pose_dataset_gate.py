from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check YOLO pose dataset training gate")
    parser.add_argument("--quality-report", required=True)
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--min-accepted-frames", type=int, default=20)
    parser.add_argument("--recommended-accepted-frames", type=int, default=50)
    return parser


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_quality(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def _has_txt(path: Path) -> bool:
    return path.exists() and any(item.is_file() and item.suffix.lower() == ".txt" for item in path.iterdir())


def check_gate(
    *,
    quality: dict[str, Any],
    dataset_dir: Path,
    min_accepted_frames: int,
    recommended_accepted_frames: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    warnings: list[str] = []
    accepted_frames = int(quality.get("accepted_frames", 0) or 0)
    manual_review_frames = int(quality.get("manual_review_frames", 0) or 0)
    rejected_frames = int(quality.get("rejected_frames", 0) or 0)

    if quality.get("status") != "built":
        reasons.append("quality_report_not_built")
    if rejected_frames != 0:
        reasons.append("rejected_frames_not_zero")
    if manual_review_frames != 0:
        reasons.append("manual_review_frames_not_zero")
    if accepted_frames < int(min_accepted_frames):
        reasons.append("accepted_frames_below_minimum")
    elif accepted_frames < int(recommended_accepted_frames):
        warnings.append("accepted_frames_below_recommended")
    if not (dataset_dir / "dataset.yaml").exists():
        reasons.append("dataset_yaml_missing")
    if not _has_txt(dataset_dir / "labels" / "train"):
        reasons.append("train_labels_missing")
    if not _has_txt(dataset_dir / "labels" / "val"):
        reasons.append("val_labels_missing")

    allowed = not reasons
    return {
        "status": "passed" if allowed else "failed",
        "dataset_train_allowed": allowed,
        "accepted_frames": accepted_frames,
        "manual_review_frames": manual_review_frames,
        "rejected_frames": rejected_frames,
        "min_accepted_frames": int(min_accepted_frames),
        "recommended_accepted_frames": int(recommended_accepted_frames),
        "dataset_yaml": str(dataset_dir / "dataset.yaml"),
        "train_label_dir": str(dataset_dir / "labels" / "train"),
        "val_label_dir": str(dataset_dir / "labels" / "val"),
        "reasons": reasons,
        "warnings": warnings,
        "training_started": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    quality_path = Path(args.quality_report)
    dataset_dir = Path(args.dataset_dir)
    report = check_gate(
        quality=_read_quality(quality_path),
        dataset_dir=dataset_dir,
        min_accepted_frames=int(args.min_accepted_frames),
        recommended_accepted_frames=int(args.recommended_accepted_frames),
    )
    if not quality_path.exists():
        report["reasons"].append("quality_report_missing")
        report["status"] = "failed"
        report["dataset_train_allowed"] = False
    write_json(Path(args.report_out), report)
    print(
        "yolo-pose-dataset-gate "
        f"status={report['status']} dataset_train_allowed={str(report['dataset_train_allowed']).lower()} "
        f"accepted_frames={report['accepted_frames']} training_started=false"
    )
    return 0 if report["dataset_train_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
