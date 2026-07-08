from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EDGE_ROOT = ROOT / "device_transfer" / "Edge"
if str(EDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(EDGE_ROOT))

from edge.config import load_config
from edge.pose_estimator import PoseDetection, YoloPoseEstimator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract YOLO pose pseudo-label JSONL from registered MP4 batches")
    parser.add_argument("--registry", required=True, help="Batch registry JSONL from tools.register_training_batch.")
    parser.add_argument("--frames-dir", required=True, help="Output extracted frame image directory.")
    parser.add_argument("--output-jsonl", required=True, help="Output pseudo-label JSONL for tools.build_yolo_pose_dataset.")
    parser.add_argument("--report-out", required=True, help="Output quality report JSON.")
    parser.add_argument("--config", default="edge/config.raspi_cam01.yaml", help="Pose estimator config.")
    parser.add_argument("--pose-jsonl", default="", help="Optional reviewed/fixed detection JSONL. Used for tests/manual corrections.")
    parser.add_argument("--max-frames-per-sample", type=int, default=12)
    parser.add_argument("--default-fps", type=float, default=30.0)
    parser.add_argument("--val-ratio", type=float, default=0.2)
    parser.add_argument("--contiguous-frames", action="store_true")
    return parser


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _load_pose_fixture(path: Path) -> dict[tuple[str, int], PoseDetection]:
    fixture: dict[tuple[str, int], PoseDetection] = {}
    for row in _read_jsonl(path):
        sample_id = str(row["sample_id"])
        frame_index = int(row["frame_index"])
        fixture[(sample_id, frame_index)] = PoseDetection(
            bbox=[int(value) for value in row["bbox_xyxy"]],
            bbox_confidence=float(row.get("bbox_confidence", 1.0)),
            keypoints=[[float(value) for value in item] for item in row["keypoints"]],
            pose_confidence_mean=float(row.get("pose_confidence_mean", 0.0)),
        )
    return fixture


def _label_windows(label_path: Path) -> tuple[float, list[tuple[int, int]]]:
    if not label_path.exists():
        return 0.0, []
    raw = json.loads(label_path.read_text(encoding="utf-8-sig"))
    annotations = raw.get("annotations", {}) if isinstance(raw, dict) else {}
    fps = float(annotations.get("fps", 0.0) or 0.0)
    windows: list[tuple[int, int]] = []
    for item in annotations.get("object", []) or []:
        start = int(round(float(item.get("startFrame", 0.0) or 0.0)))
        end = int(round(float(item.get("endFrame", start) or start)))
        if end < start:
            start, end = end, start
        windows.append((max(0, start), max(0, end)))
    return fps, windows


def sample_frame_indices(
    windows: list[tuple[int, int]], total_frames: int, max_frames: int, *, contiguous: bool = False,
) -> list[int]:
    if total_frames <= 0 or max_frames <= 0:
        return []
    ranges = windows or [(0, total_frames - 1)]
    candidates: list[int] = []
    per_window = max(1, int(np.ceil(max_frames / max(len(ranges), 1))))
    for start, end in ranges:
        start = min(max(0, start), total_frames - 1)
        end = min(max(start, end), total_frames - 1)
        count = min(per_window, end - start + 1)
        if contiguous:
            midpoint = (start + end) // 2
            first = max(start, min(midpoint - count // 2, end - count + 1))
            candidates.extend(range(first, first + count))
        else:
            candidates.extend(int(value) for value in np.linspace(start, end, num=count, dtype=int))
    return sorted(dict.fromkeys(candidates))[:max_frames]


def _split_for_index(row_index: int, val_ratio: float) -> str:
    if val_ratio <= 0:
        return "train"
    interval = max(2, int(round(1.0 / min(max(val_ratio, 0.01), 0.5))))
    return "val" if row_index % interval == interval - 1 else "train"


def _select_detection(
    *,
    sample_id: str,
    frame_index: int,
    frame: np.ndarray,
    estimator: YoloPoseEstimator | None,
    fixture: dict[tuple[str, int], PoseDetection],
) -> PoseDetection | None:
    if fixture:
        return fixture.get((sample_id, frame_index))
    if estimator is None:
        return None
    detections = estimator.predict(frame)
    if len(detections) != 1:
        return None
    return detections[0]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_edge_config(path: str) -> dict[str, Any]:
    config = load_config(path)
    model_path = Path(str(config["model"].get("model_path", "")))
    if not model_path.is_absolute():
        edge_candidate = (EDGE_ROOT / model_path).resolve()
        if edge_candidate.is_file():
            config["model"]["model_path"] = str(edge_candidate)
    return config


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = Path(args.registry)
    frames_dir = Path(args.frames_dir)
    output_jsonl = Path(args.output_jsonl)
    report_out = Path(args.report_out)

    if not registry.exists():
        write_json(report_out, {"status": "failed", "reason": "registry_missing", "training_started": False})
        return 2

    pose_fixture = _load_pose_fixture(Path(args.pose_jsonl)) if args.pose_jsonl else {}
    estimator = None if pose_fixture else YoloPoseEstimator(_load_edge_config(args.config))
    frames_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    batches = _read_jsonl(registry)
    total_samples = sum(len(batch.get("samples", []) or []) for batch in batches)
    print(f"[pseudo-labels] Extraction started (Total samples: {total_samples})")

    rows: list[dict[str, Any]] = []
    missing_detection = 0
    unreadable_frames = 0
    samples_seen = 0

    for batch in batches:
        for sample in batch.get("samples", []) or []:
            if not isinstance(sample, dict):
                continue
            samples_seen += 1
            if samples_seen > 0 and samples_seen % 5 == 0:
                print(f"[pseudo-labels] Processing sample {samples_seen}/{total_samples}...")
            sample_id = str(sample.get("sample_id", ""))
            video_path = Path(str(sample.get("video_path", "")))
            label_path = Path(str(sample.get("label_path", "")))
            capture = cv2.VideoCapture(str(video_path))
            if not capture.isOpened():
                unreadable_frames += 1
                continue
            total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            video_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            label_fps, windows = _label_windows(label_path)
            fps = label_fps or video_fps or float(args.default_fps)
            frame_indices = sample_frame_indices(
                windows, total_frames, int(args.max_frames_per_sample), contiguous=bool(args.contiguous_frames),
            )

            for frame_index in frame_indices:
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = capture.read()
                if not ok or frame is None:
                    unreadable_frames += 1
                    continue
                detection = _select_detection(
                    sample_id=sample_id,
                    frame_index=frame_index,
                    frame=frame,
                    estimator=estimator,
                    fixture=pose_fixture,
                )
                if detection is None:
                    missing_detection += 1
                    continue
                image_name = f"{sample_id}_frame_{frame_index:06d}.jpg"
                image_path = frames_dir / image_name
                if not cv2.imwrite(str(image_path), frame):
                    unreadable_frames += 1
                    continue
                rows.append(
                    {
                        "sample_id": sample_id,
                        "frame_index": frame_index,
                        "timestamp_ms": int(round((frame_index / max(fps, 1.0)) * 1000.0)),
                        "source_video": str(video_path),
                        "label_path": str(label_path),
                        "image_path": str(image_path),
                        "split": _split_for_index(len(rows), float(args.val_ratio)),
                        "width": int(frame.shape[1]),
                        "height": int(frame.shape[0]),
                        "bbox_xyxy": detection.bbox,
                        "bbox_confidence": detection.bbox_confidence,
                        "keypoints": detection.keypoints,
                        "pose_confidence_mean": detection.pose_confidence_mean,
                    }
                )
            capture.release()

    with output_jsonl.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    report = {
        "status": "created" if rows else "failed",
        "samples_seen": samples_seen,
        "frames_written": len(rows),
        "pseudo_labels": len(rows),
        "missing_detection_frames": missing_detection,
        "unreadable_frames": unreadable_frames,
        "output_jsonl": str(output_jsonl),
        "frames_dir": str(frames_dir),
        "training_started": False,
    }
    write_json(report_out, report)
    print(
        "pseudo-labels "
        f"samples={samples_seen} frames={len(rows)} missing_detection={missing_detection} "
        "training_started=false"
    )
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
