from __future__ import annotations
import logging
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

import numpy as np
import cv2


LOGGER = logging.getLogger(__name__)


def _rects_overlap(rect_a: tuple[int, int, int, int], rect_b: tuple[int, int, int, int]) -> bool:
    return not (
        rect_a[2] <= rect_b[0]
        or rect_b[2] <= rect_a[0]
        or rect_a[3] <= rect_b[1]
        or rect_b[3] <= rect_a[1]
    )


class RTSPStreamer:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.enabled = bool(config["stream"].get("enabled", False))
        self.mode = str(config["stream"].get("mode", "external")).strip().lower()
        self.command = config["stream"].get("command")
        self.output_url = str(config["stream"].get("output_url", "")).strip()
        self.fps = int(config["stream"].get("fps", config.get("camera", {}).get("fps", 10)))
        self.encoder = str(config["stream"].get("encoder", "libx264"))
        self.preset = str(config["stream"].get("preset", "ultrafast"))
        self.overlay_enabled = bool(config["stream"].get("overlay_enabled", False))
        self.overlay_show_labels = bool(config["stream"].get("overlay_show_labels", False))
        self.overlay_keypoint_threshold = float(config["stream"].get("overlay_keypoint_threshold", 0.2))
        self.input_pix_fmt = self._resolve_input_pix_fmt(config["stream"].get("input_pix_fmt", "bgr24"))
        self.output_pix_fmt = self._resolve_output_pix_fmt(config["stream"].get("output_pix_fmt", ""))
        self.rtsp_transport = str(config["stream"].get("rtsp_transport", "tcp")).strip().lower()
        
        self._queue: queue.Queue = queue.Queue(maxsize=30)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._started = False

    def start(self) -> None:
        if not self.enabled:
            return
        self._stop_event.clear()
        if self.mode == "frame_pipe":
            self._started = True
            self._thread = threading.Thread(target=self._stream_worker, daemon=True, name="rtsp-streamer")
            self._thread.start()
            return
        if not self.command:
            return
        self._process = subprocess.Popen(self.command, shell=True)

    def write_frame(self, frame: np.ndarray, overlays: list[dict[str, Any]] | None = None) -> None:
        if not self.enabled or self.mode != "frame_pipe" or not self._started:
            return
        if frame.ndim != 3 or frame.shape[2] != 3:
            return
        if self.overlay_enabled:
            frame = self._draw_overlays(frame, overlays or [])
            
        try:
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
            self._queue.put_nowait(frame.copy())
        except Exception as exc:
            LOGGER.warning("failed to queue frame for streaming: %s", exc)

    def _stream_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            
            try:
                if self._process is None or self._process.poll() is not None:
                    self._start_frame_pipe(frame)
                if self._process is None or self._process.stdin is None:
                    continue
                
                stream_frame = np.ascontiguousarray(frame)
                self._process.stdin.write(stream_frame.tobytes())
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                LOGGER.warning("frame pipe stream stopped: %s", exc)
                self._process = None
            except Exception as exc:
                LOGGER.warning("unexpected error in stream worker: %s", exc)
            finally:
                self._queue.task_done()

    def _draw_overlays(self, frame: np.ndarray, overlays: list[dict[str, Any]]) -> np.ndarray:
        output = np.ascontiguousarray(frame.copy())
        occupied_labels: list[tuple[int, int, int, int]] = []
        status_rect = self._draw_status_overlay(output)
        if status_rect is not None:
            occupied_labels.append(status_rect)
        for overlay in overlays:
            detection = overlay.get("detection")
            bbox = getattr(detection, "bbox", None) or overlay.get("bbox")
            keypoints = getattr(detection, "keypoints", None) or overlay.get("keypoints") or []
            if bbox and len(bbox) >= 4:
                x1, y1, x2, y2 = [int(round(float(value))) for value in bbox[:4]]
                cv2.rectangle(output, (x1, y1), (x2, y2), (34, 197, 94), 2)
                if self.overlay_show_labels:
                    label_parts = [
                        str(overlay.get("action_label") or "UNKNOWN"),
                        str(overlay.get("risk_label") or "NORMAL"),
                    ]
                    risk_confidence = overlay.get("risk_confidence")
                    if risk_confidence is not None:
                        try:
                            label_parts.append(f"{float(risk_confidence):.2f}")
                        except (TypeError, ValueError):
                            pass
                    label_rect = self._draw_label(output, " ".join(label_parts), x1, y1, occupied_labels)
                    if label_rect is not None:
                        occupied_labels.append(label_rect)
            self._draw_keypoints(output, keypoints)
        return output

    def _draw_status_overlay(self, frame: np.ndarray) -> tuple[int, int, int, int] | None:
        timestamp = time.strftime("%H:%M:%S")
        return self._draw_label(frame, f"OVERLAY ON  {self.fps}FPS  {timestamp}", 8, 24)

    def _draw_keypoints(self, frame: np.ndarray, keypoints: Any) -> None:
        edges = (
            (5, 6),
            (5, 7),
            (7, 9),
            (6, 8),
            (8, 10),
            (5, 11),
            (6, 12),
            (11, 12),
            (11, 13),
            (13, 15),
            (12, 14),
            (14, 16),
        )
        points: list[tuple[int, int, float]] = []
        for keypoint in keypoints:
            if isinstance(keypoint, dict):
                x, y, confidence = keypoint.get("x"), keypoint.get("y"), keypoint.get("confidence", 1.0)
            else:
                x, y, confidence = keypoint[0], keypoint[1], keypoint[2] if len(keypoint) > 2 else 1.0
            try:
                points.append((int(round(float(x))), int(round(float(y))), float(confidence)))
            except (TypeError, ValueError):
                points.append((0, 0, 0.0))
        for start_idx, end_idx in edges:
            if start_idx >= len(points) or end_idx >= len(points):
                continue
            start = points[start_idx]
            end = points[end_idx]
            if start[2] < self.overlay_keypoint_threshold or end[2] < self.overlay_keypoint_threshold:
                continue
            cv2.line(frame, (start[0], start[1]), (end[0], end[1]), (59, 130, 246), 2)
        for x, y, confidence in points:
            if confidence >= self.overlay_keypoint_threshold:
                cv2.circle(frame, (x, y), 3, (134, 239, 172), -1)

    def _draw_label(
        self,
        frame: np.ndarray,
        text: str,
        x: int,
        y: int,
        occupied: list[tuple[int, int, int, int]] | None = None,
    ) -> tuple[int, int, int, int] | None:
        label = str(text or "").strip()
        if not label:
            return None
        occupied = occupied or []
        frame_h, frame_w = frame.shape[:2]
        (width, height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        origin_x = max(0, min(int(x), max(0, frame_w - width - 8)))
        origin_y = max(14, int(y) - 8)
        for _ in range(8):
            top = max(0, origin_y - height - baseline - 4)
            bottom = min(frame_h - 1, origin_y + baseline + 2)
            rect = (origin_x, top, min(frame_w - 1, origin_x + width + 8), bottom)
            if not any(_rects_overlap(rect, other) for other in occupied):
                break
            origin_y = min(frame_h - 2, origin_y + height + baseline + 8)
        else:
            top = max(0, origin_y - height - baseline - 4)
            bottom = min(frame_h - 1, origin_y + baseline + 2)
            rect = (origin_x, top, min(frame_w - 1, origin_x + width + 8), bottom)
        origin = (origin_x, origin_y)
        top_left = (rect[0], rect[1])
        bottom_right = (rect[2], rect[3])
        cv2.rectangle(frame, top_left, bottom_right, (15, 23, 42), -1)
        cv2.putText(frame, label, (origin[0] + 4, origin[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (248, 250, 252), 1)
        return rect

    def stop(self) -> None:
        try:
            self._queue.join()
        except Exception:
            pass
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except (queue.Empty, ValueError):
                break
                
        if self._process and self._process.poll() is None:
            if self._process.stdin is not None:
                try:
                    self._process.stdin.close()
                except OSError:
                    pass
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def _start_frame_pipe(self, frame: np.ndarray) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            LOGGER.warning("ffmpeg not found; frame_pipe stream disabled")
            return
        if not self.output_url:
            LOGGER.warning("stream.output_url is required for frame_pipe mode")
            return
        height, width = int(frame.shape[0]), int(frame.shape[1])
        command = [
            ffmpeg,
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-pix_fmt",
            self.input_pix_fmt,
            "-s",
            f"{width}x{height}",
            "-r",
            str(self.fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            self.encoder,
        ]
        if self.encoder in {"libx264", "libx264rgb"}:
            command.extend(["-preset", self.preset, "-tune", "zerolatency"])
        if self.output_pix_fmt:
            command.extend(["-pix_fmt", self.output_pix_fmt])
        command.extend(
            [
                "-rtsp_transport",
                self.rtsp_transport,
                "-f",
                "rtsp",
                self.output_url,
            ]
        )
        self._process = subprocess.Popen(command, stdin=subprocess.PIPE)

    def _resolve_input_pix_fmt(self, value: object) -> str:
        pix_fmt = str(value or "bgr24").strip().lower()
        if pix_fmt in {"bgr24", "rgb24"}:
            return pix_fmt
        LOGGER.warning("unsupported stream.input_pix_fmt=%s; using bgr24", pix_fmt)
        return "bgr24"

    def _resolve_output_pix_fmt(self, value: object) -> str:
        pix_fmt = str(value or "").strip().lower()
        if not pix_fmt:
            return ""
        if pix_fmt in {"yuv420p", "yuv444p"}:
            return pix_fmt
        LOGGER.warning("unsupported stream.output_pix_fmt=%s; ignoring", pix_fmt)
        return ""
