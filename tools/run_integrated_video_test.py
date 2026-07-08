from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parents[1]
PIPELINE: Final[tuple[str, ...]] = (
    "YOLO26s-pose",
    "XGBoost action",
    "XGBoost fall",
    "TriggerEngine",
    "ST-GCN",
)
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "capture_ts",
    "analysis_ts",
    "camera_id",
    "patient_id",
    "action_label",
    "risk_label",
    "event_level",
)


@dataclass(frozen=True, slots=True)
class ReplayFrame:
    keypoints_17: list[list[float]]


@dataclass(frozen=True, slots=True)
class ReplayCandidateWindow:
    sequence: list[ReplayFrame]
    coarse_action: str
    coarse_confidence: float


def build_blocked_report(
    *,
    video_path: Path,
    config_path: Path,
    label_path: Path | None,
    reason: str,
) -> dict[str, str | list[str] | None]:
    return {
        "status": "blocked",
        "reason": reason,
        "pipeline": list(PIPELINE),
        "required_fields": list(REQUIRED_FIELDS),
        "video": str(video_path),
        "config": str(config_path),
        "label_jsonl": None if label_path is None else str(label_path),
    }


def run_video_pipeline(
    *,
    video_path: Path,
    config_path: Path,
    label_path: Path | None,
    report_path: Path,
    max_frames: int,
    camera_id: str,
    patient_id: str,
) -> int:
    if not video_path.exists():
        report = build_blocked_report(
            video_path=video_path,
            config_path=config_path,
            label_path=label_path,
            reason=f"video not found: {video_path}",
        )
        write_report(report_path, report)
        print(json.dumps({"status": "blocked", "report_out": str(report_path)}, ensure_ascii=False))
        return 2
    if not config_path.exists():
        report = build_blocked_report(
            video_path=video_path,
            config_path=config_path,
            label_path=label_path,
            reason=f"config not found: {config_path}",
        )
        write_report(report_path, report)
        print(json.dumps({"status": "blocked", "report_out": str(report_path)}, ensure_ascii=False))
        return 2
    return execute_existing_pipeline(
        video_path=video_path,
        config_path=config_path,
        label_path=label_path,
        report_path=report_path,
        max_frames=max_frames,
        camera_id=camera_id,
        patient_id=patient_id,
    )


def prepare_replay_config(config: dict, *, video_path: Path) -> dict:
    replay_config = copy.deepcopy(config)
    replay_config["camera"]["source_mode"] = "file"
    replay_config["camera"]["source"] = str(video_path)
    replay_config["server"]["enabled"] = False
    return replay_config


def execute_existing_pipeline(
    *,
    video_path: Path,
    config_path: Path,
    label_path: Path | None,
    report_path: Path,
    max_frames: int,
    camera_id: str,
    patient_id: str,
) -> int:
    import cv2

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from edge.action_classifier import ActionClassifier
    from edge.config import load_config
    from edge.feature_extractor import FeatureExtractor
    from edge.pose_estimator import YoloPoseEstimator
    from edge.tier_classifier import TierClassifier
    from edge.trigger_engine import TriggerEngine
    from server.services.stgcn_classifier import STGCNClassifier

    config = prepare_replay_config(load_config(config_path), video_path=video_path)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        report = build_blocked_report(
            video_path=video_path,
            config_path=config_path,
            label_path=label_path,
            reason=f"cannot open video: {video_path}",
        )
        write_report(report_path, report)
        print(json.dumps({"status": "blocked", "report_out": str(report_path)}, ensure_ascii=False))
        return 2

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    pose_estimator = YoloPoseEstimator(config)
    extractor = FeatureExtractor()
    action_classifier = ActionClassifier(config)
    tier_classifier = TierClassifier(config)
    trigger_engine = TriggerEngine(config)
    stgcn = STGCNClassifier(model_path=str(ROOT / "server/models/stgcn_fall_binary.pth"))
    events: list[dict[str, str | int | float | list[str] | None]] = []
    latencies_ms: list[float] = []
    sequence: list[ReplayFrame] = []
    frame_index = 0
    detected_frames = 0
    try:
        while frame_index < max_frames:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            capture_ts = frame_index / fps
            started = time.perf_counter()
            detections = pose_estimator.predict(frame)
            analysis_ts = time.time()
            latencies_ms.append((time.perf_counter() - started) * 1000.0)
            if detections:
                detection = detections[0]
                detected_frames += 1
                feature_map, feature_vector = extractor.extract(
                    1,
                    detection.bbox,
                    detection.keypoints,
                    frame.shape,
                    int(capture_ts * 1000.0),
                )
                action_label, action_confidence = action_classifier.predict(1, feature_map, feature_vector)
                risk_label, risk_confidence = tier_classifier.update(1, feature_map)
                flags = trigger_engine.update(1, feature_map, action_label, int(capture_ts * 1000.0))
                candidate = trigger_engine.judge_candidate(1, flags, action_label)
                sequence.append(ReplayFrame(keypoints_17=detection.keypoints))
                if len(sequence) > 24:
                    sequence = sequence[-24:]
                stgcn_label = None
                stgcn_confidence = None
                stgcn_detail = "not_run: no danger candidate"
                if candidate is not None and sequence:
                    window = ReplayCandidateWindow(
                        sequence=sequence,
                        coarse_action=action_label,
                        coarse_confidence=action_confidence,
                    )
                    stgcn_label, stgcn_confidence, stgcn_detail = stgcn.classify(window)
                events.append(
                    {
                        "capture_ts": round(capture_ts, 6),
                        "analysis_ts": round(analysis_ts, 6),
                        "camera_id": camera_id,
                        "patient_id": patient_id,
                        "action_label": action_label,
                        "risk_label": risk_label,
                        "event_level": event_level(risk_label, candidate is not None),
                        "frame_id": frame_index,
                        "action_confidence": round(float(action_confidence), 6),
                        "risk_confidence": round(float(risk_confidence), 6),
                        "trigger_flags": list(flags),
                        "stgcn_label": stgcn_label,
                        "stgcn_confidence": None if stgcn_confidence is None else round(float(stgcn_confidence), 6),
                        "stgcn_detail": stgcn_detail,
                    }
                )
            frame_index += 1
    finally:
        capture.release()

    report = {
        "status": "pass",
        "pipeline": list(PIPELINE),
        "required_fields": list(REQUIRED_FIELDS),
        "video": str(video_path),
        "config": str(config_path),
        "label_jsonl": None if label_path is None else str(label_path),
        "frames_processed": frame_index,
        "detected_frames": detected_frames,
        "pose_latency_p95_ms": percentile_nearest(latencies_ms, 95.0),
        "events": events,
    }
    write_report(report_path, report)
    print(json.dumps({"status": "pass", "report_out": str(report_path)}, ensure_ascii=False))
    return 0


def event_level(risk_label: str, has_candidate: bool) -> str:
    match risk_label:
        case "DANGER" | "DROP":
            return "DANGER"
        case "ABNORMAL":
            return "ABNORMAL"
        case _ if has_candidate:
            return "ABNORMAL"
        case _:
            return "NORMAL"


def percentile_nearest(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile / 100.0)
    return float(ordered[index])


def write_report(report_path: Path, report: dict[str, str | int | float | list[str] | list[dict[str, str | int | float | list[str] | None]] | None]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run YOLO -> XGBoost -> TriggerEngine -> ST-GCN offline video test")
    parser.add_argument("--video", required=True)
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--label-jsonl", default=None)
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--max-frames", type=int, default=120)
    parser.add_argument("--camera-id", default="offline_cam01")
    parser.add_argument("--patient-id", default="P001")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    label_path = None if args.label_jsonl is None else Path(args.label_jsonl)
    return run_video_pipeline(
        video_path=Path(args.video),
        config_path=Path(args.config),
        label_path=label_path,
        report_path=Path(args.report_out),
        max_frames=int(args.max_frames),
        camera_id=str(args.camera_id),
        patient_id=str(args.patient_id),
    )


if __name__ == "__main__":
    raise SystemExit(main())
