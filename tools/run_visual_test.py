"""
Visual test runner — shows live skeleton + action label overlay on screen.

Usage:
    python -m tools.run_visual_test --source "C:/Users/jju03/Downloads/test.mp4"

Keys during playback:
    q / ESC  : quit
    SPACE    : pause / resume
    s        : save current frame as PNG
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edge.feature_extractor import FeatureExtractor
from edge.action_classifier import ActionClassifier
from edge.tier_classifier import TierClassifier
from edge.preprocess import FramePreprocessor
from edge.pose_estimator import YoloPoseEstimator

LOGGER = logging.getLogger("visual_test")

# COCO 17 keypoint skeleton connections
SKELETON_PAIRS = [
    (0, 1), (0, 2),
    (1, 3), (2, 4),
    (5, 6),
    (5, 7), (7, 9),
    (6, 8), (8, 10),
    (5, 11), (6, 12),
    (11, 12),
    (11, 13), (13, 15),
    (12, 14), (14, 16),
]

LABEL_COLORS = {
    "STANDING":   (0, 220, 0),
    "SITTING":    (0, 180, 255),
    "LYING":      (0, 80, 255),
    "WALKING":    (0, 255, 200),
    "TRANSITION": (0, 200, 255),
    "FALL_LIKE":  (0, 0, 255),
    "UNKNOWN":    (120, 120, 120),
}
DEFAULT_COLOR = (200, 200, 200)


def parse_resolution(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    normalized = value.lower().replace("x", ",")
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("resolution must be WIDTHxHEIGHT, for example 640x360")
    width, height = int(parts[0]), int(parts[1])
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("resolution width and height must be positive")
    return width, height


def draw_skeleton(frame: np.ndarray, keypoints: list[list[float]], conf_thresh: float = 0.30) -> None:
    pts = [(int(kp[0]), int(kp[1]), float(kp[2])) for kp in keypoints]
    for i, j in SKELETON_PAIRS:
        if pts[i][2] > conf_thresh and pts[j][2] > conf_thresh:
            cv2.line(frame, pts[i][:2], pts[j][:2], (180, 180, 0), 2, cv2.LINE_AA)
    for x, y, c in pts:
        if c > conf_thresh:
            color = (0, 255, 0) if c > 0.6 else (0, 180, 100)
            cv2.circle(frame, (x, y), 5, color, -1, cv2.LINE_AA)
            cv2.circle(frame, (x, y), 5, (0, 0, 0), 1, cv2.LINE_AA)


def draw_bbox(frame: np.ndarray, bbox: list[int], label: str, conf: float, color: tuple) -> None:
    x1, y1, x2, y2 = bbox
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
    text = f"{label} {conf:.2f}"
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
    cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 4, y1), color, -1)
    cv2.putText(frame, text, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 2, cv2.LINE_AA)


def draw_feature_panel(
    frame: np.ndarray,
    features: dict,
    action: str,
    conf: float,
    risk_label: str = "NORMAL",
    risk_conf: float = 0.0,
) -> np.ndarray:
    h, w = frame.shape[:2]
    panel_w = 340
    panel = np.zeros((h, panel_w, 3), dtype=np.uint8)
    color = LABEL_COLORS.get(action, DEFAULT_COLOR)
    cv2.rectangle(panel, (0, 0), (panel_w - 1, h - 1), (30, 30, 30), -1)

    lines = [
        f"ACTION: {action}  ({conf:.2f})",
        f"RISK:   {risk_label}  ({risk_conf:.2f})",
        "",
        f"torso_angle:     {features.get('torso_angle_deg', 0.0):7.1f}deg",
        f"shoulder_tilt:   {features.get('shoulder_tilt_deg', 0.0):7.1f}deg",
        f"hip_tilt:        {features.get('hip_tilt_deg', 0.0):7.1f}deg",
        f"lknee_angle:     {features.get('left_knee_angle_deg', 0.0):7.1f}deg",
        f"rknee_angle:     {features.get('right_knee_angle_deg', 0.0):7.1f}deg",
        f"lhip_angle:      {features.get('left_hip_angle_deg', 0.0):7.1f}deg",
        f"rhip_angle:      {features.get('right_hip_angle_deg', 0.0):7.1f}deg",
        "",
        f"velocity:        {features.get('center_velocity_px_s', 0.0):7.1f} px/s",
        f"vert_velocity:   {features.get('vertical_velocity_px_s', 0.0):7.1f} px/s",
        f"bbox_aspect:     {features.get('bbox_aspect_ratio', 0.0):7.3f}",
        f"pose_conf:       {features.get('pose_confidence_mean', 0.0):7.3f}",
        f"visible_joints:  {features.get('visible_joint_ratio', 0.0):7.3f}",
        f"head_hip_diff:   {features.get('head_hip_y_diff', 0.0):7.1f} px",
        f"torso_delta:     {features.get('torso_angle_delta', 0.0):7.2f}deg",
    ]

    flags = []
    torso_delta = abs(features.get("torso_angle_delta", 0.0))
    torso_abs = abs(features.get("torso_angle_deg", 0.0))
    vert_v = features.get("vertical_velocity_px_s", 0.0)
    center_v = features.get("center_velocity_px_s", 0.0)
    aspect = features.get("bbox_aspect_ratio", 0.0)
    knee_l = features.get("left_knee_angle_deg", 180.0)
    knee_r = features.get("right_knee_angle_deg", 180.0)
    head_hip = features.get("head_hip_y_diff", -999.0)
    pose_conf = features.get("pose_confidence_mean", 1.0)
    vis_ratio = features.get("visible_joint_ratio", 1.0)

    if torso_delta > 15:              flags.append("torso_spike")
    if torso_abs > 100:               flags.append("torso_sustained_high")
    if vert_v > 490:                  flags.append("!! vertical_vel_spike")
    if center_v > 540:                flags.append("!! center_vel_spike")
    if aspect > 1.4:                  flags.append("bbox_aspect_collapse")
    if knee_l < 77 and knee_r < 77:   flags.append("knee_collapse")
    if head_hip > 0:                  flags.append("!! HEAD_BELOW_HIP")
    if pose_conf < 0.40:              flags.append("pose_conf_low")
    if vis_ratio < 0.45:              flags.append("vis_joint_low")

    lines += ["", "--- TRIGGERS ---"]
    lines += ([f"  {f}" for f in flags] if flags else ["  (none)"])

    y_offset = 22
    for i, line in enumerate(lines):
        lcolor = color if i == 0 else ((0, 100, 255) if line.startswith("  !") else (200, 200, 200))
        cv2.putText(panel, line, (8, y_offset + i * 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, lcolor, 1, cv2.LINE_AA)

    return np.hstack([frame, panel])


def run(
    source: str,
    config_path: str,
    processing_resolution: tuple[int, int] | None = None,
    playback_speed: float = 1.0,
    max_frames: int | None = None,
) -> None:
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config["camera"]["source_mode"] = "file"
    config["camera"]["source"] = source
    config["camera"]["loop_file"] = False
    config["server"]["enabled"] = False
    if processing_resolution is not None:
        config["camera"]["processing_resolution"] = [processing_resolution[0], processing_resolution[1]]

    preprocessor = FramePreprocessor(config)
    extractor = FeatureExtractor()
    classifier = ActionClassifier(config)
    tier_classifier = TierClassifier(config)

    config["model"]["backend"] = "onnxruntime"
    pose_estimator = YoloPoseEstimator(config)

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        LOGGER.error("Cannot open: %s", source)
        return

    cap_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    LOGGER.info("Video: %.1f fps, %d total frames", cap_fps, total_frames)

    prev_hip_y: dict[int, tuple[float, int]] = {}
    prev_torso: dict[int, float] = {}
    frame_idx = 0
    paused = False
    save_count = 0
    combined = None
    playback_start = time.perf_counter()

    cv2.namedWindow("Elderly Care AI - Visual Test", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Elderly Care AI - Visual Test", 1600, 760)

    while True:
        if not paused:
            if max_frames is not None and frame_idx >= max_frames:
                LOGGER.info("Reached max_frames=%d.", max_frames)
                break

            ret, frame = cap.read()
            if not ret:
                LOGGER.info("End of video.")
                break

            ts_ms = int(frame_idx / cap_fps * 1000)
            frame_idx += 1

            proc_w, proc_h = config["camera"].get("processing_resolution", [0, 0])
            if proc_w and proc_h:
                frame = cv2.resize(frame, (int(proc_w), int(proc_h)), interpolation=cv2.INTER_AREA)

            prep = preprocessor.apply(frame)
            proc = prep.frame

            detections = pose_estimator.predict(proc)
            display = proc.copy()
            features_last: dict = {}
            action_label = "NO_PERSON"
            action_conf = 0.0
            risk_label = "NORMAL"
            risk_conf = 0.0

            if detections:
                for pidx, det in enumerate(detections[:1]):
                    bbox = det.bbox
                    keypoints = det.keypoints
                    bconf = det.bbox_confidence

                    feature_map, feature_vector = extractor.extract(
                        track_id=pidx, bbox=bbox, keypoints=keypoints,
                        frame_shape=proc.shape, timestamp_ms=ts_ms,
                    )

                    lhy = keypoints[11][1]
                    rhy = keypoints[12][1]
                    hip_mid_y = (lhy + rhy) / 2
                    nose_y = keypoints[0][1]

                    vert_vel = 0.0
                    if pidx in prev_hip_y:
                        ph, pt = prev_hip_y[pidx]
                        dt = max((ts_ms - pt) / 1000.0, 1e-3)
                        vert_vel = (hip_mid_y - ph) / dt
                    prev_hip_y[pidx] = (hip_mid_y, ts_ms)

                    cur_ta = feature_map.get("torso_angle_deg", 0.0)
                    ta_delta = abs(cur_ta - prev_torso.get(pidx, cur_ta))
                    prev_torso[pidx] = cur_ta

                    feature_map["vertical_velocity_px_s"] = vert_vel
                    feature_map["head_hip_y_diff"] = nose_y - hip_mid_y
                    feature_map["torso_angle_delta"] = ta_delta

                    action_label, action_conf = classifier.predict(pidx, feature_map, feature_vector)
                    risk_label, risk_conf = tier_classifier.update(pidx, feature_map)
                    features_last = feature_map

                    color = LABEL_COLORS.get(action_label, DEFAULT_COLOR)
                    draw_skeleton(display, keypoints)
                    draw_bbox(display, bbox, action_label, action_conf, color)

            # progress bar
            prog = frame_idx / max(total_frames, 1)
            bar_w = int(display.shape[1] * prog)
            cv2.rectangle(display, (0, display.shape[0] - 8), (bar_w, display.shape[0]), (0, 200, 50), -1)
            cv2.putText(display,
                        f"Frame {frame_idx}/{total_frames}  t={frame_idx/cap_fps:.1f}s",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            combined = draw_feature_panel(display, features_last, action_label, action_conf, risk_label, risk_conf)

            if paused:
                cv2.putText(combined, "[ PAUSED ]",
                            (combined.shape[1] // 2 - 70, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 80, 255), 3)

        if combined is not None:
            cv2.imshow("Elderly Care AI - Visual Test", combined)

        if paused:
            wait_ms = 50
        else:
            speed = max(float(playback_speed), 0.1)
            target_elapsed = frame_idx / cap_fps / speed
            actual_elapsed = time.perf_counter() - playback_start
            wait_ms = max(1, int((target_elapsed - actual_elapsed) * 1000))
        key = cv2.waitKey(wait_ms) & 0xFF
        if key in (ord('q'), 27):
            break
        elif key == ord(' '):
            paused = not paused
            LOGGER.info("Paused=%s", paused)
        elif key == ord('s'):
            save_dir = Path("tools/visual_test_frames")
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / f"frame_{frame_idx:06d}_{save_count:03d}.png"
            cv2.imwrite(str(save_path), combined)
            LOGGER.info("Saved: %s", save_path)
            save_count += 1

    cap.release()
    cv2.destroyAllWindows()


def main():
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Visual test with skeleton + feature overlay")
    parser.add_argument("--source", default="C:/Users/jju03/Downloads/test.mp4")
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--processing-resolution", type=parse_resolution, default=None,
                        help="Override processing/display resolution, for example 640x360")
    parser.add_argument("--playback-speed", type=float, default=1.0,
                        help="Target playback speed. 1.0 means original video speed.")
    parser.add_argument("--max-frames", type=int, default=None,
                        help="Optional frame limit for smoke tests.")
    args = parser.parse_args()
    run(
        source=args.source,
        config_path=args.config,
        processing_resolution=args.processing_resolution,
        playback_speed=args.playback_speed,
        max_frames=args.max_frames,
    )


if __name__ == "__main__":
    main()
