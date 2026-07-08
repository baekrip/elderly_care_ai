from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ONNX_CANDIDATE_OPTIONS: dict[str, dict[str, object]] = {
    "default": {},
    "threads1": {
        "intra_op_num_threads": 1,
        "graph_optimization_level": "ORT_ENABLE_ALL",
        "execution_mode": "ORT_SEQUENTIAL",
        "session.intra_op.allow_spinning": "1",
    },
    "threads2": {
        "intra_op_num_threads": 2,
        "graph_optimization_level": "ORT_ENABLE_ALL",
        "execution_mode": "ORT_SEQUENTIAL",
        "session.intra_op.allow_spinning": "1",
    },
    "threads4": {
        "intra_op_num_threads": 4,
        "graph_optimization_level": "ORT_ENABLE_ALL",
        "execution_mode": "ORT_SEQUENTIAL",
        "session.intra_op.allow_spinning": "1",
    },
    "threads4_nospin": {
        "intra_op_num_threads": 4,
        "graph_optimization_level": "ORT_ENABLE_ALL",
        "execution_mode": "ORT_SEQUENTIAL",
        "session.intra_op.allow_spinning": "0",
    },
}


def _mean(values: list[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def _percentile_nearest(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1)
    return float(ordered[min(index, len(ordered) - 1)])


def evaluate_pose_benchmark_gate(
    *,
    danger_recall: float | None,
    fp_per_hour: float | None,
    min_danger_recall: float = 0.90,
    max_fp_per_hour: float = 2.0,
) -> dict[str, object]:
    reasons: list[str] = []
    if danger_recall is not None and float(danger_recall) < min_danger_recall:
        reasons.append(f"DANGER recall {float(danger_recall):.3f} < {min_danger_recall:.3f}")
    if fp_per_hour is not None and float(fp_per_hour) > max_fp_per_hour:
        reasons.append(f"FP/hour {float(fp_per_hour):.3f} > {max_fp_per_hour:.3f}")
    if danger_recall is None or fp_per_hour is None:
        return {
            "allowed": None,
            "reasons": reasons,
            "note": "accuracy gate not evaluated because DANGER recall or FP/hour is missing",
        }
    return {"allowed": not reasons, "reasons": reasons}


def build_onnx_candidate_options(candidate_names: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for candidate in candidate_names:
        if candidate not in ONNX_CANDIDATE_OPTIONS:
            allowed = ", ".join(sorted(ONNX_CANDIDATE_OPTIONS))
            raise ValueError(f"unknown ONNX candidate {candidate!r}; allowed: {allowed}")
        rows.append(
            {
                "candidate": candidate,
                "options": dict(ONNX_CANDIDATE_OPTIONS[candidate]),
                "rollback": {
                    "default_candidate": "default",
                    "rollback_on_missing_accuracy": True,
                    "rollback_on_fps_drop": True,
                    "rollback_on_latency_regression": True,
                },
            }
        )
    return rows


def _infer_bottleneck_stage(candidates: list[dict[str, object]]) -> str | None:
    stage_totals: dict[str, list[float]] = {}
    for candidate in candidates:
        for key, value in candidate.items():
            if not key.endswith("_ms") or key == "pose_total_ms":
                continue
            if isinstance(value, int | float):
                stage_totals.setdefault(key.removesuffix("_ms"), []).append(float(value))
    if not stage_totals:
        return None
    return max(stage_totals.items(), key=lambda item: _mean(item[1]))[0]


def build_replay_decision_report(
    *,
    candidates: list[dict[str, object]],
    stage_metrics_present: bool,
    danger_recall_min: float = 0.90,
    fp_per_hour_max: float = 2.0,
    camera_rtsp_fps_min: float = 30.0,
    risk_latency_ms_max: float = 500.0,
) -> dict[str, object]:
    reasons: list[str] = []
    if not stage_metrics_present:
        reasons.append("missing_stage_metrics")

    for metric_name in ("DANGER_recall", "FP_per_hour", "pose_latency_p95_ms", "pose_fps"):
        if not any(candidate.get(metric_name) is not None for candidate in candidates):
            reasons.append(f"missing_{metric_name}")

    for candidate in candidates:
        danger_recall = candidate.get("DANGER_recall")
        fp_per_hour = candidate.get("FP_per_hour")
        pose_latency = candidate.get("pose_latency_p95_ms")
        pose_fps = candidate.get("pose_fps")
        if isinstance(danger_recall, int | float) and float(danger_recall) < danger_recall_min:
            reasons.append(f"{candidate.get('candidate', 'candidate')}:DANGER_recall_below_gate")
        if isinstance(fp_per_hour, int | float) and float(fp_per_hour) > fp_per_hour_max:
            reasons.append(f"{candidate.get('candidate', 'candidate')}:FP_per_hour_above_gate")
        if isinstance(pose_latency, int | float) and float(pose_latency) > risk_latency_ms_max:
            reasons.append(f"{candidate.get('candidate', 'candidate')}:risk_latency_above_gate")
        if isinstance(pose_fps, int | float) and float(pose_fps) < camera_rtsp_fps_min:
            reasons.append(f"{candidate.get('candidate', 'candidate')}:pose_fps_below_gate")

    return {
        "schema_version": "1.0",
        "candidates": candidates,
        "required_metrics_present": not any(reason.startswith("missing_") for reason in reasons),
        "bottleneck_stage": _infer_bottleneck_stage(candidates) if stage_metrics_present else None,
        "operational_gate": {
            "allowed": not reasons,
            "reasons": reasons,
        },
        "thresholds": {
            "danger_recall_min": danger_recall_min,
            "fp_per_hour_max": fp_per_hour_max,
            "camera_rtsp_fps_min": camera_rtsp_fps_min,
            "risk_latency_ms_max": risk_latency_ms_max,
        },
    }


def build_pose_benchmark_row(
    *,
    candidate: str,
    resolution: str,
    frames: int,
    detected_frames: int,
    latencies_ms: list[float],
    pose_confidences: list[float],
    visible_ratios: list[float],
    danger_recall: float | None = None,
    fp_per_hour: float | None = None,
    cpu_percent: float | None = None,
    ram_mb: float | None = None,
    temperature_c: float | None = None,
) -> dict[str, object]:
    mean_latency_ms = _mean(latencies_ms)
    pose_fps = float(1000.0 / mean_latency_ms) if mean_latency_ms > 0 else 0.0
    skeleton_confidence_mean = _mean(pose_confidences)
    visible_joint_ratio = _mean(visible_ratios)
    row: dict[str, object] = {
        "candidate": candidate,
        "resolution": resolution,
        "frames": int(frames),
        "detected_frames": int(detected_frames),
        "detection_rate": round(float(detected_frames) / float(frames), 6) if frames else 0.0,
        "pose_latency_mean_ms": round(mean_latency_ms, 4),
        "pose_latency_p95_ms": round(_percentile_nearest(latencies_ms, 95.0), 4),
        "pose_fps": round(pose_fps, 4),
        "skeleton_confidence_mean": round(skeleton_confidence_mean, 4),
        "visible_joint_ratio": round(visible_joint_ratio, 4),
        "DANGER_recall": danger_recall,
        "FP_per_hour": fp_per_hour,
        "cpu_percent": cpu_percent,
        "ram_mb": ram_mb,
        "temperature_c": temperature_c,
        "operational_gate": evaluate_pose_benchmark_gate(
            danger_recall=danger_recall,
            fp_per_hour=fp_per_hour,
        ),
        # Backward-compatible field names used by older reports/docs.
        "mean_latency_ms": round(mean_latency_ms, 4),
        "fps": round(pose_fps, 4),
        "pose_conf_mean": round(skeleton_confidence_mean, 4),
        "visible_joint_ratio_mean": round(visible_joint_ratio, 4),
    }
    return row


def parse_resolutions(raw: str) -> list[tuple[int, int]]:
    values: list[tuple[int, int]] = []
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        width, height = item.split("x", 1)
        values.append((int(width), int(height)))
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark pose inference by resolution")
    parser.add_argument("--video", default=r"C:\Users\jju03\Downloads\test.mp4")
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--resolutions", default="1280x720,960x540,640x360")
    parser.add_argument("--report-out", default="experiments/behavior_training/reports/pose_resolution_benchmark.json")
    parser.add_argument("--candidate", default="baseline")
    parser.add_argument("--model-path", default="", help="Optional pose model path override for model candidate comparison.")
    parser.add_argument("--danger-recall", type=float, default=None)
    parser.add_argument("--fp-per-hour", type=float, default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    import cv2

    from edge.config import load_config
    from edge.feature_extractor import FeatureExtractor
    from edge.pose_estimator import YoloPoseEstimator

    config = load_config(args.config)
    if args.model_path:
        config.setdefault("model", {})["model_path"] = args.model_path
    pose_estimator = YoloPoseEstimator(config)
    extractor = FeatureExtractor()
    capture = cv2.VideoCapture(args.video)
    if not capture.isOpened():
        print(f"cannot_open_video={args.video}")
        return 1

    source_frames: list = []
    for _ in range(args.frames):
        ok, frame = capture.read()
        if not ok or frame is None:
            break
        source_frames.append(frame)
    capture.release()
    if not source_frames:
        print("no_frames_loaded")
        return 1

    report_rows: list[dict[str, float | int | str]] = []
    for width, height in parse_resolutions(args.resolutions):
        latencies_ms: list[float] = []
        pose_confidences: list[float] = []
        visible_ratios: list[float] = []
        detected_frames = 0
        for frame_idx, frame in enumerate(source_frames):
            resized = cv2.resize(frame, (width, height))
            started = time.perf_counter()
            detections = pose_estimator.predict(resized)
            latencies_ms.append((time.perf_counter() - started) * 1000.0)
            if not detections:
                continue
            detected_frames += 1
            detection = detections[0]
            feature_map, _ = extractor.extract(
                track_id=1,
                bbox=detection.bbox,
                keypoints=detection.keypoints,
                frame_shape=resized.shape,
                timestamp_ms=frame_idx * 100,
            )
            pose_confidences.append(float(detection.pose_confidence_mean))
            visible_ratios.append(float(feature_map.get("visible_joint_ratio", 0.0)))

        mean_latency_ms = float(statistics.fmean(latencies_ms))
        fps = float(1000.0 / mean_latency_ms) if mean_latency_ms > 0 else 0.0
        report_rows.append(
            build_pose_benchmark_row(
                candidate=args.candidate,
                resolution=f"{width}x{height}",
                frames=len(source_frames),
                detected_frames=detected_frames,
                latencies_ms=latencies_ms,
                pose_confidences=pose_confidences,
                visible_ratios=visible_ratios,
                danger_recall=args.danger_recall,
                fp_per_hour=args.fp_per_hour,
            )
        )

    report_path = Path(args.report_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "pose-benchmark-v1",
        "candidate": args.candidate,
        "video": args.video,
        "frames": len(source_frames),
        "rows": report_rows,
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
