from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any

import websockets

from shared.api_key_auth import build_api_key_headers, read_api_key_auth_config
from shared.protocol import ClipRequestEvent


class EdgeEventListener:
    def __init__(self, config: dict[str, Any], on_clip_request: Callable[[ClipRequestEvent], Any]) -> None:
        self.config = config
        self.on_clip_request = on_clip_request
        self._thread: threading.Thread | None = None
        self.enabled = bool(config["server"].get("enabled", True)) and bool(config["server"].get("ws_url"))
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

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.run(self._listen())

    async def _listen(self) -> None:
        ws_url = self.config["server"]["ws_url"].format(camera_id=self.config["camera"]["camera_id"])
        while True:
            try:
                async with websockets.connect(
                    ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    additional_headers=build_api_key_headers(self.ingest_auth),
                ) as websocket:
                    async for raw_message in websocket:
                        event = ClipRequestEvent.model_validate(json.loads(raw_message))
                        self.on_clip_request(event)
            except Exception:
                await asyncio.sleep(5)
