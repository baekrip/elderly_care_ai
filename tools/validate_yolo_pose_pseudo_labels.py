from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ALLOWED_REMOVED_REASONS = {
    "bbox_wrong",
    "keypoint_wrong",
    "person_missing",
    "multi_person_ambiguous",
    "low_confidence",
    "blurred_frame",
    "occluded",
    "other",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate YOLO pose pseudo-label JSONL before dataset build")
    parser.add_argument("--pseudo-labels-jsonl", required=True)
    parser.add_argument("--removed-jsonl", default="experiments/behavior_training/labels/yolo_pose_removed_frames.jsonl")
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--min-pose-confidence", type=float, default=0.6)
    return parser


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            rows.append({"__line__": line_number, "__invalid__": True})
        else:
            payload["__line__"] = line_number
            rows.append(payload)
    return rows


def _bbox_valid(record: dict[str, Any]) -> bool:
    try:
        width = float(record["width"])
        height = float(record["height"])
        x1, y1, x2, y2 = [float(value) for value in record["bbox_xyxy"][:4]]
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height


def _keypoints_valid(record: dict[str, Any]) -> bool:
    keypoints = record.get("keypoints")
    return isinstance(keypoints, list) and len(keypoints) == 17


def _pose_confidence_valid(record: dict[str, Any], minimum: float) -> bool:
    try:
        return float(record.get("pose_confidence_mean", 0.0)) >= minimum
    except (TypeError, ValueError):
        return False


def validate_pseudo_rows(rows: list[dict[str, Any]], min_pose_confidence: float) -> tuple[int, list[dict[str, Any]]]:
    valid_rows = 0
    issues: list[dict[str, Any]] = []
    for record in rows:
        line = int(record.get("__line__", 0) or 0)
        image_path = Path(str(record.get("image_path", "")))
        row_issues: list[str] = []
        if not image_path.exists():
            row_issues.append("image_missing")
        if not _bbox_valid(record):
            row_issues.append("bbox_invalid")
        if not _keypoints_valid(record):
            row_issues.append("keypoints_not_17")
        if not _pose_confidence_valid(record, min_pose_confidence):
            row_issues.append("low_confidence")
        if row_issues:
            for reason in row_issues:
                issues.append({"line": line, "image_path": str(image_path), "reason": reason})
        else:
            valid_rows += 1
    return valid_rows, issues


def validate_removed_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for record in rows:
        reason = str(record.get("reason", ""))
        if reason not in ALLOWED_REMOVED_REASONS:
            issues.append(
                {
                    "line": int(record.get("__line__", 0) or 0),
                    "image_path": str(record.get("image_path", "")),
                    "reason": "invalid_removed_reason",
                    "value": reason,
                }
            )
    return issues


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pseudo_path = Path(args.pseudo_labels_jsonl)
    removed_path = Path(args.removed_jsonl)
    report_out = Path(args.report_out)

    pseudo_rows = _read_jsonl(pseudo_path)
    removed_rows = _read_jsonl(removed_path)
    issues: list[dict[str, Any]] = []
    if not pseudo_path.exists():
        issues.append({"line": 0, "image_path": "", "reason": "pseudo_labels_missing"})
    if not pseudo_rows:
        issues.append({"line": 0, "image_path": "", "reason": "pseudo_labels_empty"})

    valid_rows, pseudo_issues = validate_pseudo_rows(pseudo_rows, float(args.min_pose_confidence))
    issues.extend(pseudo_issues)
    issues.extend(validate_removed_rows(removed_rows))

    invalid_rows = len({(item.get("line"), item.get("image_path")) for item in issues if item.get("line")})
    passed = not issues and valid_rows > 0
    report = {
        "status": "passed" if passed else "failed",
        "dataset_build_allowed": passed,
        "rows": len(pseudo_rows),
        "valid_rows": valid_rows,
        "invalid_rows": invalid_rows,
        "removed_rows": len(removed_rows),
        "allowed_removed_reasons": sorted(ALLOWED_REMOVED_REASONS),
        "issues": issues[:200],
        "training_started": False,
    }
    write_report(report_out, report)
    print(
        "yolo-pose-pseudo-validation "
        f"status={report['status']} rows={len(pseudo_rows)} valid={valid_rows} "
        f"dataset_build_allowed={str(passed).lower()} training_started=false"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
