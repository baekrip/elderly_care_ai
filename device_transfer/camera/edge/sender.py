from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from shared.api_key_auth import build_api_key_headers, read_api_key_auth_config
from shared.protocol import ActivityFrameBatch, CameraRegistration, TimelineBatch


class ServerClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.enabled = bool(config["server"].get("enabled", True))
        self.base_url = config["server"].get("base_url", "").rstrip("/")
        self.timeout = float(config["server"].get("request_timeout_sec", 10))
        self.clip_upload_path = str(config["server"].get("clip_upload_path", "/api/clips/upload"))
        camera_cfg = config.get("camera", {}) or {}
        device_id = str(camera_cfg.get("device_id") or camera_cfg.get("camera_id") or "")
        self.ingest_auth = read_api_key_auth_config(
            config,
            "server",
            api_key_field="ingest_api_key",
            api_key_env_field="ingest_api_key_env",
            api_key_header_field="ingest_api_key_header",
            default_api_key_header="X-Edge-API-Key",
            device_id=device_id,
            require_device_id_default=True,
        )
        self.clip_upload_auth = read_api_key_auth_config(
            config,
            "server",
            api_key_field="clip_upload_api_key",
            api_key_env_field="clip_upload_api_key_env",
            api_key_header_field="clip_upload_api_key_header",
            device_id=device_id,
        )
        self.session = requests.Session()

    def register_camera(self, payload: CameraRegistration) -> None:
        if not self.enabled or not self.base_url:
            return
        self._post_json("/api/cameras/register", payload.model_dump(mode="json"))

    def send_activity_frames(self, payload: ActivityFrameBatch) -> None:
        if not self.enabled or not self.base_url:
            return
        if payload.frames:
            self._post_json("/api/activity/frames", payload.model_dump(mode="json"))

    def send_timeline_segments(self, payload: TimelineBatch) -> None:
        if not self.enabled or not self.base_url:
            return
        if payload.segments:
            self._post_json("/api/activity/timeline", payload.model_dump(mode="json"))

    def upload_clip(self, file_path: Path, metadata: dict[str, Any]) -> None:
        if not self.enabled or not self.base_url:
            return
        headers = build_api_key_headers(self.clip_upload_auth)
        with file_path.open("rb") as clip_file:
            response = self.session.post(
                f"{self.base_url}{self.clip_upload_path}",
                files={"file": (file_path.name, clip_file, "video/mp4")},
                data=metadata,
                headers=headers,
                timeout=self.timeout,
            )
        response.raise_for_status()

    def _post_json(self, path: str, payload: dict[str, Any]) -> None:
        response = self.session.post(
            f"{self.base_url}{path}",
            json=payload,
            headers=build_api_key_headers(self.ingest_auth),
            timeout=self.timeout,
        )
        response.raise_for_status()
