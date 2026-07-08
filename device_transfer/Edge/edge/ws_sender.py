from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from shared.api_key_auth import build_api_key_headers, read_api_key_auth_config
from shared.protocol import SkeletonFrameBatch


class SkeletonWebSocketSender:
    def __init__(self, config: dict[str, Any]) -> None:
        server_cfg = config.get("server", {})
        camera_cfg = config.get("camera", {}) or {}
        device_id = str(camera_cfg.get("device_id") or camera_cfg.get("camera_id") or "")
        self.enabled = bool(server_cfg.get("skeleton_ws_enabled", server_cfg.get("enabled", True)))
        self.url = str(server_cfg.get("skeleton_ws_url", server_cfg.get("ws_url", "")))
        self.timeout_sec = float(server_cfg.get("request_timeout_sec", 10.0))
        output_cfg = config.get("local_output", {})
        default_pending_file = Path(output_cfg.get("save_dir", "edge/storage/results")) / "skeleton_ws_pending.jsonl"
        self.pending_file = Path(server_cfg.get("skeleton_ws_pending_file", default_pending_file))
        self.max_pending_batches = max(0, int(server_cfg.get("skeleton_ws_max_pending_batches", 300)))
        self.max_reconnect_attempts = max(0, int(server_cfg.get("skeleton_ws_max_reconnect_attempts", 2)))
        self.reconnect_backoff_initial_sec = max(
            0.0,
            float(server_cfg.get("skeleton_ws_reconnect_backoff_initial_sec", 0.25)),
        )
        self.reconnect_backoff_max_sec = max(
            self.reconnect_backoff_initial_sec,
            float(server_cfg.get("skeleton_ws_reconnect_backoff_max_sec", 2.0)),
        )
        self.dropped_pending_batch_count = 0
        self.dropped_pending_frame_count = 0
        self.reconnect_attempt_count = 0
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
        self._next_sequence_id = 1

    def build_message(self, batch: SkeletonFrameBatch) -> str:
        return json.dumps(batch.model_dump(mode="json"), ensure_ascii=False)

    def pending_count(self) -> int:
        if not self.pending_file.exists():
            return 0
        return len([line for line in self.pending_file.read_text(encoding="utf-8").splitlines() if line.strip()])

    def transport_diagnostics(self) -> dict[str, int]:
        return {
            "pending_batch_count": self.pending_count(),
            "dropped_pending_batch_count": self.dropped_pending_batch_count,
            "dropped_pending_frame_count": self.dropped_pending_frame_count,
            "reconnect_attempt_count": self.reconnect_attempt_count,
        }

    async def send_batch(self, batch: SkeletonFrameBatch) -> None:
        if not self.enabled or not self.url or not batch.frames:
            return
        self._assign_sequence_ids(batch)
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("websockets package is required for skeleton streaming") from exc

        current_message = self.build_message(batch)
        messages = self._read_pending_messages() + [current_message]
        remaining: list[str] = []
        try:
            for index, message in enumerate(messages):
                await self._send_message_with_retry(websockets, message)
                if index == 0 and self.pending_file.exists():
                    self._write_pending_messages(messages[index + 1 :])
        except Exception:
            remaining = messages[index:]
            self._write_pending_messages(remaining)
            raise
        self._write_pending_messages([])

    def send_batch_sync(self, batch: SkeletonFrameBatch) -> None:
        asyncio.run(self.send_batch(batch))

    def _assign_sequence_ids(self, batch: SkeletonFrameBatch) -> None:
        for frame in batch.frames:
            if frame.sequence_id is None:
                frame.sequence_id = self._next_sequence_id
                self._next_sequence_id += 1
            else:
                self._next_sequence_id = max(self._next_sequence_id, int(frame.sequence_id) + 1)

    async def _send_message(self, websockets_module: Any, message: str) -> None:
        async with websockets_module.connect(
            self.url,
            open_timeout=self.timeout_sec,
            additional_headers=build_api_key_headers(self.ingest_auth),
        ) as websocket:
            await websocket.send(message)

    async def _send_message_with_retry(self, websockets_module: Any, message: str) -> None:
        first_error: Exception | None = None
        for attempt in range(self.max_reconnect_attempts + 1):
            try:
                await self._send_message(websockets_module, message)
                return
            except Exception as exc:  # noqa: BLE001 - provider transport errors are re-raised after bounded retries
                if first_error is None:
                    first_error = exc
                if attempt >= self.max_reconnect_attempts:
                    raise first_error
                self.reconnect_attempt_count += 1
                delay = min(
                    self.reconnect_backoff_initial_sec * (2**attempt),
                    self.reconnect_backoff_max_sec,
                )
                await asyncio.sleep(delay)

    def _read_pending_messages(self) -> list[str]:
        if not self.pending_file.exists():
            return []
        return [line for line in self.pending_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _write_pending_messages(self, messages: list[str]) -> None:
        overflow = max(0, len(messages) - self.max_pending_batches)
        dropped = messages[:overflow]
        kept = messages[overflow:]
        self.dropped_pending_batch_count += len(dropped)
        self.dropped_pending_frame_count += sum(self._message_frame_count(message) for message in dropped)
        if not kept:
            self.pending_file.unlink(missing_ok=True)
            return
        self.pending_file.parent.mkdir(parents=True, exist_ok=True)
        self.pending_file.write_text("\n".join(kept) + "\n", encoding="utf-8")

    @staticmethod
    def _message_frame_count(message: str) -> int:
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return 0
        frames = payload.get("frames") if isinstance(payload, dict) else None
        return len(frames) if isinstance(frames, list) else 0
