from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

try:
    from picamera2 import Picamera2
except ImportError:  # pragma: no cover
    Picamera2 = None

from edge.phone_bridge import PhoneBridge


@dataclass
class FramePacket:
    frame: np.ndarray
    timestamp_ms: int
    source_mode: str = "device"
    source_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class CameraSource:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        camera_cfg = config["camera"]
        self.backend = camera_cfg.get("backend", "opencv")
        self.resolution = tuple(camera_cfg.get("resolution", [1280, 720]))
        self.fps = int(camera_cfg.get("fps", 10))
        self.source_mode = self._infer_source_mode(camera_cfg)
        self.source = camera_cfg.get("source", 0)
        self.loop_file = bool(camera_cfg.get("loop_file", True))
        self.reconnect_interval_sec = float(camera_cfg.get("reconnect_interval_sec", 2.0))
        self._camera: Any | None = None
        self._resolved_source: str | int | None = None
        self._phone_bridge: PhoneBridge | None = None
        self._last_open_attempt = 0.0
        self._exhausted = False

    def open(self) -> None:
        if self.backend == "picamera2":
            if Picamera2 is None:
                raise RuntimeError("picamera2 backend requested but package is not installed")
            self._camera = Picamera2()
            camera_config = self._camera.create_video_configuration(
                main={"size": self.resolution, "format": "RGB888"},
                controls={"FrameRate": self.fps},
            )
            self._camera.configure(camera_config)
            self._camera.start()
            self._resolved_source = "picamera2"
            self._exhausted = False
            self._last_open_attempt = time.time()
            return

        source = self._resolve_source()
        self._camera = cv2.VideoCapture(source)
        if not self._camera.isOpened():
            self._camera.release()
            self._camera = None
            raise RuntimeError(f"failed to open source: {source}")
        if self.source_mode != "file":
            self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            self._camera.set(cv2.CAP_PROP_FPS, self.fps)
        self._resolved_source = source
        self._last_open_attempt = time.time()
        self._exhausted = False

    def read(self) -> FramePacket | None:
        if self._camera is None:
            raise RuntimeError("Camera is not opened")

        timestamp_ms = int(time.time() * 1000)
        if self.backend == "picamera2":
            frame = self._camera.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return FramePacket(
                frame=frame,
                timestamp_ms=timestamp_ms,
                source_mode=self.source_mode,
                source_ref=str(self._resolved_source or "picamera2"),
            )

        ok, frame = self._camera.read()
        if not ok:
            if self.source_mode == "file":
                if self.loop_file:
                    self._reopen()
                    ok, frame = self._camera.read()
                    if not ok:
                        self._exhausted = True
                        return None
                else:
                    self._exhausted = True
                    return None
            elif self.source_mode in {"url", "phone_usb"}:
                if time.time() - self._last_open_attempt >= self.reconnect_interval_sec:
                    self._reopen()
                return None
            return None
        return FramePacket(
            frame=frame,
            timestamp_ms=timestamp_ms,
            source_mode=self.source_mode,
            source_ref=str(self._resolved_source or self.source),
        )

    def release(self) -> None:
        if self._camera is None:
            if self._phone_bridge is not None:
                self._phone_bridge.cleanup()
            return
        if self.backend == "picamera2":
            self._camera.stop()
        else:
            self._camera.release()
        self._camera = None
        if self._phone_bridge is not None:
            self._phone_bridge.cleanup()

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    def _reopen(self) -> None:
        if self._camera is not None and self.backend != "picamera2":
            self._camera.release()
        if self._phone_bridge is not None:
            self._phone_bridge.cleanup()
            self._phone_bridge = None
        self._camera = None
        self.open()

    def _resolve_source(self) -> str | int:
        if self.source_mode == "device":
            if isinstance(self.source, int):
                return self.source
            if isinstance(self.source, str) and self.source.isdigit():
                return int(self.source)
            return 0
        if self.source_mode == "phone_usb":
            self._phone_bridge = PhoneBridge(self.config["camera"])
            result = self._phone_bridge.prepare()
            return result.source
        return str(self.source)

    def _infer_source_mode(self, camera_cfg: dict[str, Any]) -> str:
        configured = str(camera_cfg.get("source_mode", "auto")).strip().lower()
        if configured != "auto":
            return configured

        source = camera_cfg.get("source", 0)
        if isinstance(source, int):
            return "device"
        source_str = str(source).strip()
        if source_str.isdigit():
            return "device"
        if "://" in source_str:
            return "url"
        if source_str.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
            return "file"
        return "device"
