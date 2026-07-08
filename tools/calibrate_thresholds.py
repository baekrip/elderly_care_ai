"""
Calibrate trigger thresholds from labeled mp4+json pairs.

Usage:
    python -m tools.calibrate_thresholds --data-dir "C:/Users/jju03/Downloads/video/run" --out-dir "tools/calibration_results"

Flow:
  1. Scan data-dir for json labels; find matching mp4 in sub-folders
  2. For each matched pair, run YOLO26s-pose on the labeled frame range
  3. Extract features and record them into a CSV
  4. Compute statistics (mean, std, p5, p95) for each feature
  5. Suggest calibrated threshold values
  6. Save results
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# --- project imports ---
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edge.feature_extractor import FeatureExtractor, KEYPOINT_NAMES

LOGGER = logging.getLogger("calibrate")

# ---------- helpers ----------

def find_matched_pairs(data_dir: Path) -> list[dict]:
    """Find json labels with matching mp4 files in subdirectories."""
    pairs = []
    for json_file in sorted(data_dir.rglob("*.json")):
        with open(json_file, "r", encoding="utf-8") as f:
            try:
                label = json.load(f)
            except json.JSONDecodeError:
                continue
        ann = label.get("annotations", {})
        resource = ann.get("resource", "")
        if not resource.endswith(".mp4"):
            continue
        # mp4 is in a subdirectory named by actionName
        action_name = ann.get("object", [{}])[0].get("actionName", "")
        mp4_candidate = json_file.parent / action_name / resource
        if not mp4_candidate.exists():
            # try flat search
            for candidate in data_dir.rglob(resource):
                mp4_candidate = candidate
                break
            else:
                continue
        obj = ann.get("object", [{}])[0]
        pairs.append({
            "json_path": str(json_file),
            "mp4_path": str(mp4_candidate),
            "fps": float(ann.get("fps", 29.97)),
            "start_frame": int(float(obj.get("startFrame", 0))),
            "end_frame": int(float(obj.get("endFrame", 0))),
            "action_type": obj.get("actionType", ""),
            "action_name": action_name,
            "resource": resource,
        })
    return pairs


def extract_features_from_video(
    mp4_path: str,
    start_frame: int,
    end_frame: int,
    fps: float,
    max_frames: int = 120,
) -> list[dict[str, Any]]:
    """Run YOLO26s-pose on a frame range and extract features."""
    from ultralytics import YOLO

    model_path = Path(__file__).resolve().parents[1] / "yolo26s-pose.pt"
    if not model_path.exists():
        model_path = Path(__file__).resolve().parents[1] / "edge" / "models" / "yolo26s-pose.pt"
    if not model_path.exists():
        LOGGER.error("yolo26s-pose.pt not found")
        return []

    model = YOLO(str(model_path))
    cap = cv2.VideoCapture(mp4_path)
    if not cap.isOpened():
        LOGGER.error("cannot open %s", mp4_path)
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap_fps = cap.get(cv2.CAP_PROP_FPS) or fps
    actual_end = min(end_frame, total_frames - 1) if end_frame > 0 else total_frames - 1
    actual_start = max(0, start_frame)

    # sample frames if range is too large
    frame_range = list(range(actual_start, actual_end + 1))
    if len(frame_range) > max_frames:
        step = len(frame_range) // max_frames
        frame_range = frame_range[::step][:max_frames]

    extractor = FeatureExtractor()
    results = []
    prev_hip_y = None
    prev_torso_angle = None
    prev_ts = None

    for frame_idx in frame_range:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue
        timestamp_ms = int(frame_idx / cap_fps * 1000)

        preds = model(frame, verbose=False)
        if preds is None or len(preds) == 0:
            continue
        pred = preds[0]
        if pred.keypoints is None or len(pred.keypoints.data) == 0:
            continue
        if pred.boxes is None or len(pred.boxes) == 0:
            continue

        for person_idx in range(len(pred.boxes)):
            box = pred.boxes[person_idx]
            kps = pred.keypoints.data[person_idx]
            if kps.shape[0] < 17:
                continue

            bbox = [int(box.xyxy[0][j].item()) for j in range(4)]
            keypoints = [[float(kps[k][0].item()), float(kps[k][1].item()), float(kps[k][2].item())] for k in range(17)]
            bbox_conf = float(box.conf[0].item()) if box.conf is not None else 0.0

            feature_map, feature_vector = extractor.extract(
                track_id=person_idx,
                bbox=bbox,
                keypoints=keypoints,
                frame_shape=frame.shape,
                timestamp_ms=timestamp_ms,
            )

            # compute extra features
            left_hip = np.array(keypoints[11][:2])
            right_hip = np.array(keypoints[12][:2])
            hip_mid_y = (left_hip[1] + right_hip[1]) / 2
            nose_y = keypoints[0][1]

            vertical_velocity = 0.0
            if prev_hip_y is not None and prev_ts is not None:
                dt = max((timestamp_ms - prev_ts) / 1000.0, 1e-3)
                vertical_velocity = (hip_mid_y - prev_hip_y) / dt
            prev_hip_y = hip_mid_y
            prev_ts = timestamp_ms

            torso_angle_delta = 0.0
            current_torso = feature_map.get("torso_angle_deg", 0.0)
            if prev_torso_angle is not None:
                torso_angle_delta = abs(current_torso - prev_torso_angle)
            prev_torso_angle = current_torso

            row = {
                "mp4": os.path.basename(mp4_path),
                "frame_idx": frame_idx,
                "person_idx": person_idx,
                "bbox_conf": bbox_conf,
                **feature_map,
                "vertical_velocity_px_s": vertical_velocity,
                "head_hip_y_diff": nose_y - hip_mid_y,
                "torso_angle_delta": torso_angle_delta,
            }
            results.append(row)
            break  # one person per frame for simplicity

    cap.release()
    return results


def compute_statistics(all_rows: list[dict], action_type: str) -> dict:
    """Compute per-feature stats for rows of a given action_type."""
    filtered = [r for r in all_rows if r.get("_action_type") == action_type]
    if not filtered:
        return {}

    feature_keys = [
        "torso_angle_deg", "shoulder_tilt_deg", "hip_tilt_deg",
        "left_knee_angle_deg", "right_knee_angle_deg",
        "left_hip_angle_deg", "right_hip_angle_deg",
        "center_velocity_px_s", "vertical_velocity_px_s",
        "bbox_aspect_ratio", "pose_confidence_mean", "visible_joint_ratio",
        "head_hip_y_diff", "torso_angle_delta",
    ]
    stats = {}
    for key in feature_keys:
        values = [r.get(key, 0.0) for r in filtered if r.get(key) is not None]
        if not values:
            continue
        arr = np.array(values, dtype=np.float64)
        stats[key] = {
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "p5": float(np.percentile(arr, 5)),
            "p25": float(np.percentile(arr, 25)),
            "p50": float(np.percentile(arr, 50)),
            "p75": float(np.percentile(arr, 75)),
            "p95": float(np.percentile(arr, 95)),
            "count": len(values),
        }
    return stats


def suggest_thresholds(drop_stats: dict, wander_stats: dict) -> dict:
    """Generate calibrated thresholds from real data statistics."""
    thresholds = {}

    # torso_angle_spike: use p75 of torso_angle_delta during drops
    if "torso_angle_delta" in drop_stats:
        val = drop_stats["torso_angle_delta"]["p75"]
        thresholds["torso_angle_spike"] = {"suggested": round(max(val * 0.8, 15.0), 1), "raw_p75": round(val, 2)}

    # torso_angle_sustained_high: use p25 of torso_angle_deg during drops (absolute)
    if "torso_angle_deg" in drop_stats:
        val = abs(drop_stats["torso_angle_deg"]["p75"])
        thresholds["torso_angle_sustained_high"] = {"suggested": round(max(val * 0.8, 35.0), 1), "raw_p75": round(val, 2)}

    # vertical_velocity_spike: use p75 of vertical velocity during drops
    if "vertical_velocity_px_s" in drop_stats:
        val = drop_stats["vertical_velocity_px_s"]["p75"]
        thresholds["vertical_velocity_spike"] = {"suggested": round(max(val * 0.7, 80.0), 1), "raw_p75": round(val, 2)}

    # center_velocity_spike
    if "center_velocity_px_s" in drop_stats:
        val = drop_stats["center_velocity_px_s"]["p75"]
        thresholds["center_velocity_spike"] = {"suggested": round(max(val * 0.8, 100.0), 1), "raw_p75": round(val, 2)}

    # bbox_aspect_ratio_change: compare drop vs normal ranges
    if "bbox_aspect_ratio" in drop_stats:
        val = drop_stats["bbox_aspect_ratio"]["p75"] - drop_stats["bbox_aspect_ratio"]["p25"]
        thresholds["bbox_aspect_ratio_change"] = {"suggested": round(max(val * 0.6, 0.2), 2), "raw_iqr": round(val, 3)}

    # shoulder_tilt_spike
    if "shoulder_tilt_deg" in drop_stats:
        val = abs(drop_stats["shoulder_tilt_deg"]["p95"] - drop_stats["shoulder_tilt_deg"]["p5"])
        thresholds["shoulder_tilt_spike"] = {"suggested": round(max(val * 0.5, 15.0), 1), "raw_range": round(val, 2)}

    # knee_angle_collapse
    if "left_knee_angle_deg" in drop_stats:
        val = drop_stats["left_knee_angle_deg"]["p25"]
        thresholds["knee_angle_collapse"] = {"suggested": round(max(val * 1.1, 70.0), 1), "raw_p25": round(val, 2)}

    # pose_confidence_drop
    if "pose_confidence_mean" in drop_stats:
        val = drop_stats["pose_confidence_mean"]["p25"]
        thresholds["pose_confidence_drop"] = {"suggested": round(min(val * 0.9, 0.4), 2), "raw_p25": round(val, 2)}

    # visible_joint_drop
    if "visible_joint_ratio" in drop_stats:
        val = drop_stats["visible_joint_ratio"]["p25"]
        thresholds["visible_joint_drop"] = {"suggested": round(min(val * 0.9, 0.45), 2), "raw_p25": round(val, 2)}

    # head_below_hip: check how often head is below hip during drops
    if "head_hip_y_diff" in drop_stats:
        val = drop_stats["head_hip_y_diff"]["p75"]
        thresholds["head_below_hip"] = {"note": "trigger when head_hip_y_diff > 0 (y-axis down)", "raw_p75": round(val, 2)}

    return thresholds


def main():
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Calibrate trigger thresholds from labeled data")
    parser.add_argument("--data-dir", required=True, help="Root directory with label JSONs and mp4 subdirs")
    parser.add_argument("--out-dir", default="tools/calibration_results", help="Output directory for results")
    parser.add_argument("--max-pairs", type=int, default=20, help="Max video pairs to process (per category)")
    parser.add_argument("--max-frames", type=int, default=90, help="Max frames per video")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Scanning %s for labeled pairs...", data_dir)
    all_pairs = find_matched_pairs(data_dir)
    LOGGER.info("Found %d matched pairs total", len(all_pairs))

    # categorize
    drop_pairs = [p for p in all_pairs if p["action_type"] == "ABNOR_H"]
    wander_pairs = [p for p in all_pairs if p["action_type"] == "ABNOR_W"]
    LOGGER.info("  Drop pairs: %d, Wander pairs: %d", len(drop_pairs), len(wander_pairs))

    # limit
    drop_pairs = drop_pairs[:args.max_pairs]
    wander_pairs = wander_pairs[:args.max_pairs]

    all_rows: list[dict] = []

    for label, pairs in [("ABNOR_H", drop_pairs), ("ABNOR_W", wander_pairs)]:
        LOGGER.info("Processing %s (%d pairs)...", label, len(pairs))
        for i, pair in enumerate(pairs):
            LOGGER.info("  [%d/%d] %s  frames %d-%d", i+1, len(pairs), pair["resource"], pair["start_frame"], pair["end_frame"])
            rows = extract_features_from_video(
                mp4_path=pair["mp4_path"],
                start_frame=pair["start_frame"],
                end_frame=pair["end_frame"],
                fps=pair["fps"],
                max_frames=args.max_frames,
            )
            for r in rows:
                r["_action_type"] = label
                r["_action_name"] = pair["action_name"]
            all_rows.extend(rows)
            LOGGER.info("    extracted %d frame features", len(rows))

    LOGGER.info("Total features extracted: %d rows", len(all_rows))

    # save raw CSV
    if all_rows:
        csv_path = out_dir / "features_raw.csv"
        keys = list(all_rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(all_rows)
        LOGGER.info("Saved raw features to %s", csv_path)

    # compute stats
    drop_stats = compute_statistics(all_rows, "ABNOR_H")
    wander_stats = compute_statistics(all_rows, "ABNOR_W")

    stats_path = out_dir / "feature_statistics.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump({"ABNOR_H_drop": drop_stats, "ABNOR_W_wander": wander_stats}, f, indent=2, ensure_ascii=False)
    LOGGER.info("Saved feature statistics to %s", stats_path)

    # suggest thresholds
    suggested = suggest_thresholds(drop_stats, wander_stats)
    threshold_path = out_dir / "calibrated_thresholds.json"
    with open(threshold_path, "w", encoding="utf-8") as f:
        json.dump(suggested, f, indent=2, ensure_ascii=False)
    LOGGER.info("Saved calibrated thresholds to %s", threshold_path)

    # print summary
    print("\n" + "=" * 60)
    print("CALIBRATION SUMMARY")
    print("=" * 60)
    print(f"Total pairs processed: drop={len(drop_pairs)}, wander={len(wander_pairs)}")
    print(f"Total feature rows: {len(all_rows)}")
    print(f"\nDrop (ABNOR_H) key statistics:")
    for key in ["torso_angle_deg", "vertical_velocity_px_s", "center_velocity_px_s",
                 "bbox_aspect_ratio", "head_hip_y_diff", "torso_angle_delta"]:
        if key in drop_stats:
            s = drop_stats[key]
            print(f"  {key:30s}  mean={s['mean']:8.2f}  std={s['std']:8.2f}  p25={s['p25']:8.2f}  p75={s['p75']:8.2f}")
    print(f"\nSuggested trigger thresholds:")
    for name, val in suggested.items():
        print(f"  {name:35s}  {val}")


if __name__ == "__main__":
    main()
