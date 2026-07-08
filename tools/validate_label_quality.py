from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REQUIRED_LABEL_FIELDS = (
    "camera_id",
    "track_id",
    "start_ts_ms",
    "end_ts_ms",
    "label",
    "source",
    "confidence",
    "notes",
)

ACTION_STATE_LABELS = {
    "LYING_BED",
    "LYING_FLOOR",
    "BENDING_REACHING",
    "EXERCISING_STRETCHING",
}

RISK_EVENT_LABELS = {
    "NEAR_FALL_STUMBLE",
    "FALL",
    "POST_FALL_IMMOBILE",
}


def validate_label_record(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_LABEL_FIELDS:
        if field not in record:
            errors.append(f"missing:{field}")
    try:
        start_ts_ms = int(record.get("start_ts_ms"))
        end_ts_ms = int(record.get("end_ts_ms"))
        if end_ts_ms <= start_ts_ms:
            errors.append("end_before_start")
    except (TypeError, ValueError):
        errors.append("invalid_time_range")
    try:
        confidence = float(record.get("confidence"))
        if confidence < 0.0 or confidence > 1.0:
            errors.append("confidence_out_of_range")
    except (TypeError, ValueError):
        errors.append("invalid_confidence")
    label = str(record.get("label", ""))
    if label not in ACTION_STATE_LABELS and label not in RISK_EVENT_LABELS:
        errors.append("unknown_label")
    return errors


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return int(a["start_ts_ms"]) < int(b["end_ts_ms"]) and int(b["start_ts_ms"]) < int(a["end_ts_ms"])


def _has_bed_floor_overlap(records: list[dict[str, Any]]) -> bool:
    by_track: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_track[str(record.get("track_id", ""))].append(record)
    for rows in by_track.values():
        beds = [row for row in rows if row.get("label") == "LYING_BED"]
        floors = [row for row in rows if row.get("label") == "LYING_FLOOR"]
        if any(_overlaps(bed, floor) for bed in beds for floor in floors):
            return True
    return False


def _has_rapid_transition(records: list[dict[str, Any]]) -> bool:
    by_track: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_track[str(record.get("track_id", ""))].append(record)
    for rows in by_track.values():
        ordered = sorted(rows, key=lambda item: int(item.get("start_ts_ms", 0) or 0))
        for index, row in enumerate(ordered):
            window_start = int(row.get("start_ts_ms", 0) or 0)
            labels = {
                str(item.get("label", ""))
                for item in ordered[index:]
                if int(item.get("start_ts_ms", 0) or 0) - window_start <= 1000
            }
            if len(labels) > 3:
                return True
    return False


def build_label_quality_report(
    records: list[dict[str, Any]],
    *,
    missing_inputs: tuple[str, ...] = (),
    min_near_fall_windows: int = 50,
    low_confidence_threshold: float = 0.6,
    max_low_confidence_ratio: float = 0.2,
) -> dict[str, Any]:
    label_counts = Counter(str(record.get("label", "")) for record in records)
    schema_errors = [
        {"index": index, "errors": errors}
        for index, record in enumerate(records)
        if (errors := validate_label_record(record))
    ]
    low_confidence_count = sum(
        1
        for record in records
        if float(record.get("confidence", 0.0) or 0.0) < low_confidence_threshold
    )
    low_confidence_ratio = float(low_confidence_count / len(records)) if records else 0.0

    errors: list[str] = []
    warnings: list[str] = []
    if missing_inputs:
        errors.append("missing_label_inputs")
    if schema_errors:
        errors.append("schema_errors")
    if _has_bed_floor_overlap(records):
        errors.append("lying_bed_floor_overlap")
    if label_counts.get("NEAR_FALL_STUMBLE", 0) < min_near_fall_windows:
        warnings.append("near_fall_stumble_count_below_minimum")
    if low_confidence_ratio > max_low_confidence_ratio:
        warnings.append("low_confidence_ratio_above_threshold")
    if _has_rapid_transition(records):
        warnings.append("rapid_label_transition")

    expected_fail = bool(missing_inputs)
    passed = not errors and not warnings
    return {
        "schema_version": "label-quality-v1",
        "record_count": len(records),
        "missing_inputs": list(missing_inputs),
        "label_counts": dict(sorted(label_counts.items())),
        "low_confidence_count": low_confidence_count,
        "low_confidence_ratio": round(low_confidence_ratio, 6),
        "schema_errors": schema_errors,
        "errors": errors,
        "warnings": warnings,
        "passed": passed,
        "expected_fail": expected_fail,
        "training_allowed": False,
        "thresholds": {
            "min_near_fall_windows": min_near_fall_windows,
            "low_confidence_threshold": low_confidence_threshold,
            "max_low_confidence_ratio": max_low_confidence_ratio,
        },
    }


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source = Path(path)
    if not source.exists():
        return rows
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate behavior label quality gates.")
    parser.add_argument("--action-labels", default="experiments/behavior_training/labels/action_state_labels.jsonl")
    parser.add_argument("--risk-labels", default="experiments/behavior_training/labels/risk_event_labels.jsonl")
    parser.add_argument("--report-out", default="experiments/behavior_training/reports/label_quality_gate.json")
    parser.add_argument("--min-near-fall-windows", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    action_path = Path(args.action_labels)
    risk_path = Path(args.risk_labels)
    missing_inputs = tuple(str(path) for path in (action_path, risk_path) if not path.exists())
    records = load_jsonl(action_path) + load_jsonl(risk_path)
    report = build_label_quality_report(
        records,
        missing_inputs=missing_inputs,
        min_near_fall_windows=args.min_near_fall_windows,
    )
    report_path = Path(args.report_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] or report["expected_fail"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
