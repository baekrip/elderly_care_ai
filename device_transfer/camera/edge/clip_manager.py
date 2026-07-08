from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import traceback

from edge.clip_policy import resolve_clip_window
from edge.clip_blur import blur_video_file
from edge.sender import ServerClient
from edge.video_buffer import RollingVideoBuffer
from shared.api_key_auth import read_api_key_auth_config
from shared.jsonl_rotation import JsonlRotationPolicy, append_jsonl_rows
from shared.protocol import ClipRequestEvent


class ClipManager:
    def __init__(self, config: dict[str, Any], buffer: RollingVideoBuffer, client: ServerClient) -> None:
        self.camera_id = config["camera"]["camera_id"]
        self.buffer = buffer
        self.client = client
        self.clip_request_auth = read_api_key_auth_config(config, "clip_request_server")
        buffer_cfg = config.get("buffer", {})
        self.storage_policy = str(buffer_cfg.get("storage_mode", "segment_ring"))
        max_duration_ms = buffer_cfg.get("danger_clip_max_duration_ms")
        self.max_duration_ms = int(max_duration_ms) if max_duration_ms is not None else None
        self.upload_enabled = bool(config.get("server", {}).get("upload_clip_enabled", True))
        privacy_cfg = config.get("privacy", {})
        self.face_blur_enabled = bool(privacy_cfg.get("face_blur_enabled", False))
        self.face_blur_location = str(privacy_cfg.get("face_blur_location", "local")).lower()
        output_cfg = config.get("local_output", {})
        self.local_enabled = bool(output_cfg.get("enabled", False))
        self.clip_json_dir = Path(output_cfg.get("clip_json_dir", "clip_json"))
        self.clip_requests_file = self.clip_json_dir / output_cfg.get("clip_requests_file", "edge_clip_requests.jsonl")
        self.clip_results_file = self.clip_json_dir / output_cfg.get("clip_results_file", "edge_clip_results.jsonl")
        self.clip_upload_pending_file = self.clip_json_dir / output_cfg.get("clip_upload_pending_file", "clip_upload_pending.jsonl")
        self.rotation = JsonlRotationPolicy(output_cfg.get("rotation", {}))
        if self.local_enabled:
            self.clip_json_dir.mkdir(parents=True, exist_ok=True)

    def handle_clip_request(self, event: ClipRequestEvent) -> Path | None:
        clip_window = resolve_clip_window(
            center_ts_ms=event.timestamp_ms,
            pre_ms=event.pre_clip_ms,
            post_ms=event.post_clip_ms,
            explicit_start_ms=event.clip_start_ms,
            explicit_end_ms=event.clip_end_ms,
            max_duration_ms=self.max_duration_ms,
        )
        storage_policy = event.storage_policy or self.storage_policy
        self._append(
            self.clip_requests_file,
            {
                "event_id": event.event_id,
                "camera_id": event.camera_id,
                "timestamp_ms": event.timestamp_ms,
                "capture_ts": event.capture_ts,
                "analysis_ts": event.analysis_ts,
                "label": event.label,
                "pre_clip_ms": event.pre_clip_ms,
                "post_clip_ms": event.post_clip_ms,
                "clip_start_ms": clip_window.start_ms,
                "clip_end_ms": clip_window.end_ms,
                "clip_duration_ms": clip_window.duration_ms,
                "storage_policy": storage_policy,
                "reason": event.reason,
            },
        )
        output_path = self.buffer.export_clip(
            event_id=event.event_id,
            center_ts_ms=event.timestamp_ms,
            pre_ms=clip_window.pre_ms,
            post_ms=clip_window.post_ms,
        )
        if output_path is None:
            self._append(
                self.clip_results_file,
                {
                    "event_id": event.event_id,
                    "camera_id": self.camera_id,
                    "status": "missing_clip",
                    "timestamp_ms": event.timestamp_ms,
                    "capture_ts": event.capture_ts,
                    "analysis_ts": event.analysis_ts,
                    "clip_start_ms": clip_window.start_ms,
                    "clip_end_ms": clip_window.end_ms,
                    "clip_duration_ms": clip_window.duration_ms,
                    "storage_policy": storage_policy,
                    "label": event.label,
                },
            )
            return None
        metadata = {
            "event_id": event.event_id,
            "camera_id": self.camera_id,
            "timestamp_ms": str(event.timestamp_ms),
            "started_at_ms": str(clip_window.start_ms),
            "ended_at_ms": str(clip_window.end_ms),
            "duration_ms": str(clip_window.duration_ms),
            "capture_ts": event.capture_ts or "",
            "analysis_ts": event.analysis_ts or "",
            "storage_policy": storage_policy,
            "label": event.label,
        }
        if self.face_blur_enabled:
            metadata["privacy_blur_requested"] = self.face_blur_location
        self._append(
            self.clip_results_file,
            {
                "event_id": event.event_id,
                "camera_id": self.camera_id,
                "status": "saved_local",
                "clip_path": str(output_path),
                "timestamp_ms": event.timestamp_ms,
                "capture_ts": event.capture_ts,
                "analysis_ts": event.analysis_ts,
                "started_at_ms": clip_window.start_ms,
                "ended_at_ms": clip_window.end_ms,
                "duration_ms": clip_window.duration_ms,
                "clip_start_ms": clip_window.start_ms,
                "clip_end_ms": clip_window.end_ms,
                "clip_duration_ms": clip_window.duration_ms,
                "storage_policy": storage_policy,
                "label": event.label,
            },
        )
        upload_path = output_path
        local_blur_enabled = self.face_blur_enabled and self.face_blur_location in {"local", "edge", "pi5", "auto"}
        if self.upload_enabled and local_blur_enabled:
            blurred_path = output_path.with_name(f"{output_path.stem}_blurred{output_path.suffix}")
            try:
                upload_path = blur_video_file(output_path, blurred_path)
                metadata["privacy_blur"] = "face_blur"
                metadata["raw_clip_path"] = str(output_path)
            except Exception as exc:
                self._append(
                    self.clip_upload_pending_file,
                    {
                        "event_id": event.event_id,
                        "camera_id": self.camera_id,
                        "status": "blur_failed",
                        "clip_path": str(output_path),
                        "metadata": metadata,
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=3),
                    },
                    rotate=False,
                )
                return output_path

        if self.upload_enabled:
            try:
                self.client.upload_clip(upload_path, metadata)
            except Exception as exc:
                self._append(
                    self.clip_upload_pending_file,
                    {
                        "event_id": event.event_id,
                        "camera_id": self.camera_id,
                        "status": "upload_failed",
                        "clip_path": str(output_path),
                        "metadata": metadata,
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=3),
                    },
                    rotate=False,
                )
        return output_path

    def retry_pending_uploads(self, *, max_items: int = 20) -> dict[str, int]:
        if not self.upload_enabled or not self.clip_upload_pending_file.exists():
            return {"attempted": 0, "uploaded": 0, "remaining": 0}
        rows = self._read_pending_rows()
        attempted = 0
        uploaded = 0
        remaining: list[dict[str, Any]] = []
        for row in rows:
            if attempted >= max_items:
                remaining.append(row)
                continue
            status = str(row.get("status", ""))
            if status not in {"upload_failed", "retry_failed"}:
                remaining.append(row)
                continue
            clip_path = Path(str(row.get("clip_path", "")))
            metadata = dict(row.get("metadata", {}))
            if not clip_path.exists():
                row["status"] = "missing_clip_on_retry"
                remaining.append(row)
                continue
            attempted += 1
            try:
                self.client.upload_clip(clip_path, metadata)
                uploaded += 1
                self._append(
                    self.clip_results_file,
                    {
                        "event_id": row.get("event_id"),
                        "camera_id": row.get("camera_id", self.camera_id),
                        "status": "uploaded_retry",
                        "clip_path": str(clip_path),
                    },
                )
            except Exception as exc:
                row["status"] = "retry_failed"
                row["error"] = str(exc)
                row["traceback"] = traceback.format_exc(limit=3)
                remaining.append(row)
        self._write_pending_rows(remaining)
        return {"attempted": attempted, "uploaded": uploaded, "remaining": len(remaining)}

    def _append(self, path: Path, payload: dict[str, Any], *, rotate: bool = True) -> None:
        if not self.local_enabled:
            return
        if rotate:
            append_jsonl_rows(path, [payload], self.rotation)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _read_pending_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in self.clip_upload_pending_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"status": "invalid_pending_json", "raw": line})
        return rows

    def _write_pending_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            self.clip_upload_pending_file.unlink(missing_ok=True)
            return
        self.clip_upload_pending_file.parent.mkdir(parents=True, exist_ok=True)
        self.clip_upload_pending_file.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
