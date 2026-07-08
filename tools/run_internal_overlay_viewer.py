from __future__ import annotations

import argparse
import functools
import json
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final


ROOT: Final = Path(__file__).resolve().parents[1]


def _json_script(data: list[dict]) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def build_viewer_html(
    *,
    title: str,
    camera_id: str,
    frames: list[dict],
    report_name: str,
) -> str:
    frame_count = len(frames)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
body {{ margin: 0; font-family: Segoe UI, sans-serif; background: #101216; color: #e5e7eb; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 18px; }}
h1 {{ font-size: 20px; margin: 0 0 12px; }}
.meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
.pill {{ border: 1px solid #374151; border-radius: 6px; padding: 6px 10px; background: #171b22; font-size: 13px; }}
.viewer {{ position: relative; width: 100%; aspect-ratio: 16 / 9; background: #05070b; border: 1px solid #334155; overflow: hidden; }}
#frame {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
#overlay {{ position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }}
.controls {{ display: flex; gap: 8px; align-items: center; margin-top: 12px; }}
button {{ height: 34px; border: 1px solid #475569; background: #1f2937; color: #e5e7eb; border-radius: 6px; padding: 0 12px; }}
input[type=range] {{ flex: 1; }}
pre {{ white-space: pre-wrap; background: #171b22; border: 1px solid #334155; padding: 12px; border-radius: 6px; font-size: 12px; }}
</style>
</head>
<body>
<main>
<h1>YOLO + Action Internal Viewer</h1>
<div class="meta">
  <span class="pill">camera: {camera_id}</span>
  <span class="pill">frames: {frame_count}</span>
  <span class="pill">report: {report_name}</span>
  <span class="pill">internal server only</span>
</div>
<section class="viewer">
  <img id="frame" alt="internal overlay frame">
  <canvas id="overlay"></canvas>
</section>
<div class="controls">
  <button id="prev">이전</button>
  <input id="scrub" type="range" min="0" max="{max(frame_count - 1, 0)}" value="0">
  <button id="next">다음</button>
  <span id="label" class="pill">blocked</span>
</div>
<pre id="payload"></pre>
</main>
<script>
window.OVERLAY_FRAMES = {_json_script(frames)};
const frames = window.OVERLAY_FRAMES;
const img = document.getElementById("frame");
const canvas = document.getElementById("overlay");
const ctx = canvas.getContext("2d");
const scrub = document.getElementById("scrub");
const label = document.getElementById("label");
const payload = document.getElementById("payload");
const edges = [[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]];
let index = 0;
function resizeCanvas() {{
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}}
function draw(frame) {{
  resizeCanvas();
  const width = canvas.clientWidth || 1;
  const height = canvas.clientHeight || 1;
  ctx.clearRect(0, 0, width, height);
  const sx = width / Math.max(Number(frame.source_width || 640), 1);
  const sy = height / Math.max(Number(frame.source_height || 360), 1);
  for (const track of frame.tracks || []) {{
    const box = track.bbox;
    if (Array.isArray(box)) {{
      const [x1, y1, x2, y2] = box.map(Number);
      const danger = String(track.risk_label || "").toUpperCase() === "DANGER";
      ctx.strokeStyle = danger ? "#ef4444" : "#22c55e";
      ctx.lineWidth = 2;
      ctx.strokeRect(x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy);
      const text = `${{track.action_label || "UNKNOWN"}} / ${{track.risk_label || "NORMAL"}}`;
      ctx.font = "13px Segoe UI, sans-serif";
      ctx.fillStyle = danger ? "rgba(127, 29, 29, 0.92)" : "rgba(15, 23, 42, 0.92)";
      ctx.fillRect(x1 * sx, Math.max(0, y1 * sy - 23), ctx.measureText(text).width + 12, 21);
      ctx.fillStyle = "#f8fafc";
      ctx.fillText(text, x1 * sx + 6, Math.max(14, y1 * sy - 8));
    }}
    const points = (track.keypoints || []).map((p) => [Number(p[0]), Number(p[1]), Number(p[2] ?? 1)]);
    ctx.strokeStyle = "#60a5fa";
    for (const [a, b] of edges) {{
      if (!points[a] || !points[b] || points[a][2] < 0.2 || points[b][2] < 0.2) continue;
      ctx.beginPath();
      ctx.moveTo(points[a][0] * sx, points[a][1] * sy);
      ctx.lineTo(points[b][0] * sx, points[b][1] * sy);
      ctx.stroke();
    }}
    ctx.fillStyle = "#86efac";
    for (const point of points) {{
      if (point[2] < 0.2) continue;
      ctx.beginPath();
      ctx.arc(point[0] * sx, point[1] * sy, 3.5, 0, Math.PI * 2);
      ctx.fill();
    }}
  }}
}}
function show(nextIndex) {{
  if (!frames.length) return;
  index = Math.max(0, Math.min(frames.length - 1, nextIndex));
  const frame = frames[index];
  img.src = frame.image || "";
  scrub.value = String(index);
  const first = (frame.tracks || [])[0] || {{}};
  label.textContent = `#${{frame.frame_id}} ${{first.action_label || "UNKNOWN"}} ${{first.risk_label || "NORMAL"}}`;
  payload.textContent = JSON.stringify(frame, null, 2);
  draw(frame);
}}
img.addEventListener("load", () => draw(frames[index] || {{}}));
window.addEventListener("resize", () => draw(frames[index] || {{}}));
document.getElementById("prev").addEventListener("click", () => show(index - 1));
document.getElementById("next").addEventListener("click", () => show(index + 1));
scrub.addEventListener("input", () => show(Number(scrub.value)));
show(0);
</script>
</body>
</html>
"""


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_html(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def _blocked(video_path: Path, report_path: Path, html_path: Path, camera_id: str, reason: str) -> int:
    report = {"status": "blocked", "reason": reason, "video": str(video_path), "viewer_html": str(html_path)}
    _write_json(report_path, report)
    _write_html(
        html_path,
        build_viewer_html(
            title="internal overlay blocked",
            camera_id=camera_id,
            frames=[{"frame_id": "blocked", "image": "", "source_width": 640, "source_height": 360, "tracks": []}],
            report_name=report_path.name,
        ),
    )
    print(json.dumps({"status": "blocked", "report_out": str(report_path), "html_out": str(html_path)}, ensure_ascii=False))
    return 2


def run_viewer(
    *,
    video_path: Path,
    config_path: Path,
    report_path: Path,
    html_path: Path,
    max_frames: int,
    camera_id: str,
    patient_id: str,
) -> int:
    if not video_path.exists():
        return _blocked(video_path, report_path, html_path, camera_id, f"video not found: {video_path}")
    if not config_path.exists():
        return _blocked(video_path, report_path, html_path, camera_id, f"config not found: {config_path}")

    import cv2

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from edge.action_classifier import ActionClassifier
    from edge.config import load_config
    from edge.feature_extractor import FeatureExtractor
    from edge.pose_estimator import YoloPoseEstimator
    from edge.tier_classifier import TierClassifier
    from tools.run_integrated_video_test import event_level, prepare_replay_config

    config = prepare_replay_config(load_config(config_path), video_path=video_path)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return _blocked(video_path, report_path, html_path, camera_id, f"cannot open video: {video_path}")

    frames_dir = html_path.parent / f"{html_path.stem}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    pose_estimator = YoloPoseEstimator(config)
    extractor = FeatureExtractor()
    action_classifier = ActionClassifier(config)
    tier_classifier = TierClassifier(config)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    frames: list[dict] = []
    events: list[dict] = []
    frame_index = 0
    try:
        while frame_index < max_frames:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            detections = pose_estimator.predict(frame)
            image_name = f"frame_{frame_index:06d}.jpg"
            cv2.imwrite(str(frames_dir / image_name), frame)
            tracks: list[dict] = []
            if detections:
                detection = detections[0]
                capture_ms = int((frame_index / fps) * 1000.0)
                feature_map, feature_vector = extractor.extract(
                    1,
                    detection.bbox,
                    detection.keypoints,
                    frame.shape,
                    capture_ms,
                )
                action_label, action_confidence = action_classifier.predict(1, feature_map, feature_vector)
                risk_label, risk_confidence = tier_classifier.update(1, feature_map)
                track = {
                    "track_id": "1",
                    "bbox": [int(value) for value in detection.bbox],
                    "keypoints": detection.keypoints,
                    "action_label": action_label,
                    "action_confidence": round(float(action_confidence), 6),
                    "risk_label": risk_label,
                    "risk_confidence": round(float(risk_confidence), 6),
                    "risk_score": round(float(risk_confidence), 6),
                }
                tracks.append(track)
                events.append(
                    {
                        "frame_id": frame_index,
                        "capture_ts": round(frame_index / fps, 6),
                        "analysis_ts": round(time.time(), 6),
                        "camera_id": camera_id,
                        "patient_id": patient_id,
                        "action_label": action_label,
                        "risk_label": risk_label,
                        "event_level": event_level(risk_label, False),
                    }
                )
            frames.append(
                {
                    "frame_id": frame_index,
                    "image": f"{frames_dir.name}/{image_name}",
                    "source_width": int(frame.shape[1]),
                    "source_height": int(frame.shape[0]),
                    "tracks": tracks,
                }
            )
            frame_index += 1
    finally:
        capture.release()

    report = {
        "status": "pass",
        "video": str(video_path),
        "config": str(config_path),
        "viewer_html": str(html_path),
        "frames_processed": frame_index,
        "overlay_frames": len(frames),
        "detected_frames": sum(1 for frame in frames if frame["tracks"]),
        "events": events,
    }
    _write_json(report_path, report)
    _write_html(
        html_path,
        build_viewer_html(title="internal overlay viewer", camera_id=camera_id, frames=frames, report_name=report_path.name),
    )
    print(json.dumps({"status": "pass", "report_out": str(report_path), "html_out": str(html_path)}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create an internal-only YOLO pose + action overlay HTML viewer from MP4.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--report-out", required=True)
    parser.add_argument("--html-out", required=True)
    parser.add_argument("--max-frames", type=int, default=30)
    parser.add_argument("--camera-id", default="offline_cam01")
    parser.add_argument("--patient-id", default="P001")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def serve_viewer(html_path: Path, host: str, port: int) -> None:
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(html_path.parent))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"viewer_url=http://{host}:{server.server_port}/{html_path.name}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    html_path = Path(args.html_out)
    rc = run_viewer(
        video_path=Path(args.video),
        config_path=Path(args.config),
        report_path=Path(args.report_out),
        html_path=html_path,
        max_frames=int(args.max_frames),
        camera_id=str(args.camera_id),
        patient_id=str(args.patient_id),
    )
    if rc == 0 and bool(args.serve):
        serve_viewer(html_path=html_path, host=str(args.host), port=int(args.port))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
