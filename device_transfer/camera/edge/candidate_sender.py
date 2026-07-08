"""Edge Candidate Sender — Phase 3.

Sends CandidateWindow payloads to server with retry and local backup.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

from shared.api_key_auth import build_api_key_headers, read_api_key_auth_config
from shared.protocol import CandidateWindow, CandidateWindowBatch

logger = logging.getLogger(__name__)


class CandidateSender:
    """HTTP sender for candidate windows with retry and local fallback."""

    def __init__(self, config: dict[str, Any]) -> None:
        server_cfg = config.get("server", {})
        runtime_role = str(config.get("runtime", {}).get("role", "full_edge"))
        self.enabled = (
            bool(server_cfg.get("enabled", True))
            and bool(server_cfg.get("candidate_http_enabled", True))
            and runtime_role != "skeleton_sender"
        )
        self.base_url = server_cfg.get("base_url", "").rstrip("/")
        self.timeout = float(server_cfg.get("request_timeout_sec", 10))
        self.max_retries = int(server_cfg.get("candidate_max_retries", 3))
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
        self.session = requests.Session()

        # local backup for failed sends
        self.pending_dir = Path(config.get("storage", {}).get(
            "pending_candidates_dir", "edge/storage/pending_candidates"
        ))
        self.pending_dir.mkdir(parents=True, exist_ok=True)

    def send(self, window: CandidateWindow) -> dict[str, Any] | None:
        """Send a single candidate window. Returns server response or None."""
        batch = CandidateWindowBatch(windows=[window])
        return self.send_batch(batch)

    def send_batch(self, batch: CandidateWindowBatch) -> dict[str, Any] | None:
        """Send a batch of candidate windows."""
        if not self.enabled or not self.base_url:
            logger.debug("Candidate sender disabled or no base_url, skipping.")
            return None

        payload = batch.model_dump(mode="json")
        return self._post_with_retry(
            "/api/candidates/submit", payload, batch
        )

    def _post_with_retry(self, path: str, payload: dict[str, Any],
                         batch: CandidateWindowBatch) -> dict[str, Any] | None:
        """POST with retry. On final failure, save to local backup."""
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers=build_api_key_headers(self.ingest_auth),
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                result = resp.json()
                logger.info(
                    "Candidate batch sent OK (%d windows, attempt %d)",
                    len(batch.windows), attempt,
                )
                return result
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Candidate send attempt %d/%d failed: %s",
                    attempt, self.max_retries, exc,
                )

        # all retries exhausted — save locally
        logger.error(
            "All %d retries failed. Saving %d windows to pending dir.",
            self.max_retries, len(batch.windows),
        )
        self._save_pending(payload)
        return None

    def _save_pending(self, payload: dict[str, Any]) -> None:
        """Save failed payload as JSON for later retry."""
        import time
        filename = f"pending_{int(time.time() * 1000)}.json"
        filepath = self.pending_dir / filename
        filepath.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved pending candidate to %s", filepath)

    def retry_pending(self) -> int:
        """Retry all pending candidate files. Returns number of successful sends."""
        if not self.enabled or not self.base_url:
            return 0

        sent = 0
        for filepath in sorted(self.pending_dir.glob("pending_*.json")):
            try:
                payload = json.loads(filepath.read_text(encoding="utf-8"))
                resp = self.session.post(
                    f"{self.base_url}/api/candidates/submit",
                    json=payload,
                    headers=build_api_key_headers(self.ingest_auth),
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                filepath.unlink()
                sent += 1
                logger.info("Retried pending candidate %s OK", filepath.name)
            except Exception as exc:
                logger.warning("Retry of %s failed: %s", filepath.name, exc)
                break  # stop on first failure to preserve order
        return sent
