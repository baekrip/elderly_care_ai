from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from edge.action_classifier import ActionClassifier
from edge.config import load_config
from edge.feature_extractor import FeatureExtractor
from edge.pose_estimator import YoloPoseEstimator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a labeled abnormal segment against pose/action output")
    parser.add_argument("--label", required=True, help="path to label json")
    parser.add_argument("--video", required=True, help="path to matching mp4")
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--step", type=int, default=1, help="sample every N frames")
    parser.add_argument("--output", default="", help="optional output json path")
    return parser


def load_label(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    annotations = data["annotations"]
    obj = annotations["object"][0]
    return {
        "resource": annotations["resource"],
        "duration": annotations.get("duration"),
        "fps": annotations.get("fps"),
        "action_type": obj["actionType"],
        "action_name": obj["actionName"],
        "start_frame": int(round(obj["startFrame"])),
        "end_frame": int(round(obj["endFrame"])),
        "start_position": {
            "x": float(obj["startPosition"]["x"]),
            "y": float(obj["startPosition"]["y"]),
        },
        "end_position": {
            "x": float(obj["endPosition"]["x"]),
            "y": float(obj["endPosition"]["y"]),
        },
    }


def evaluate_segment(label_info: dict[str, Any], video_path: Path, config_path: str, step: int) -> dict[str, Any]:
    config = load_config(config_path)
    config["server"]["enabled"] = False
    config["camera"]["source_mode"] = "file"
    config["camera"]["source"] = str(video_path)
    config["model"]["backend"] = "ultralytics"
    config["model"]["model_path"] = str(Path("edge/models/yolo26s-pose.pt"))

    pose = YoloPoseEstimator(config)
    feature_extractor = FeatureExtractor()
    classifier = ActionClassifier(config)

    cap = cv2.VideoCapture(str(video_path))
    results: list[dict[str, Any]] = []

    for frame_idx in range(label_info["start_frame"], label_info["end_frame"] + 1, max(step, 1)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = cap.read()
        if not ok:
            results.append({"frame": frame_idx, "detected": False})
            continue

        detections = pose.predict(frame)
        if not detections:
            results.append({"frame": frame_idx, "detected": False})
            continue

        detection = detections[0]
        feature_map, feature_vector = feature_extractor.extract(
            track_id=1,
            bbox=detection.bbox,
            keypoints=detection.keypoints,
            frame_shape=frame.shape,
            timestamp_ms=frame_idx,
        )
        action_label, action_confidence = classifier.predict(1, feature_map, feature_vector)
        x1, y1, x2, y2 = detection.bbox
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        results.append(
            {
                "frame": frame_idx,
                "detected": True,
                "bbox": detection.bbox,
                "center_x": center_x,
                "center_y": center_y,
                "pose_conf_mean": detection.pose_confidence_mean,
                "action_label": action_label,
                "action_confidence": action_confidence,
            }
        )

    cap.release()

    detected = [item for item in results if item.get("detected")]
    label_counts = Counter(item["action_label"] for item in detected)
    summary: dict[str, Any] = {
        "video": video_path.name,
        "resource": label_info["resource"],
        "label_action_type": label_info["action_type"],
        "label_action_name": label_info["action_name"],
        "start_frame": label_info["start_frame"],
        "end_frame": label_info["end_frame"],
        "sample_step": step,
        "sampled_frames": len(results),
        "detected_frames": len(detected),
        "detection_rate": round(len(detected) / len(results), 4) if results else 0.0,
        "action_counts": dict(label_counts),
        "avg_pose_conf_mean": round(float(np.mean([item["pose_conf_mean"] for item in detected])), 4) if detected else None,
        "classifier_mode": "xgboost" if Path(config["classification"].get("model_path", "")).exists() else "heuristic_fallback",
    }

    if detected:
        nearest_start = min(detected, key=lambda item: abs(item["frame"] - label_info["start_frame"]))
        nearest_end = min(detected, key=lambda item: abs(item["frame"] - label_info["end_frame"]))
        start_dist = float(
            ((nearest_start["center_x"] - label_info["start_position"]["x"]) ** 2 + (nearest_start["center_y"] - label_info["start_position"]["y"]) ** 2) ** 0.5
        )
        end_dist = float(
            ((nearest_end["center_x"] - label_info["end_position"]["x"]) ** 2 + (nearest_end["center_y"] - label_info["end_position"]["y"]) ** 2) ** 0.5
        )
        summary["nearest_start_detection"] = nearest_start
        summary["nearest_end_detection"] = nearest_end
        summary["start_center_distance_px"] = round(start_dist, 2)
        summary["end_center_distance_px"] = round(end_dist, 2)

    return summary


def main() -> None:
    args = build_parser().parse_args()
    label_path = Path(args.label)
    video_path = Path(args.video)
    label_info = load_label(label_path)
    summary = evaluate_segment(label_info, video_path, args.config, args.step)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
