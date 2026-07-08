from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from server.services.backend_forwarder import (
    BackendConfig,
    BackendResponse,
    append_contract_error,
    append_pending,
    is_retryable_response,
)


@dataclass(frozen=True)
class ClipBackendUploadRequest:
    file_path: Path
    patient_id: str
    device_key: str
    event_type: str
    occurred_at: str
    duration_sec: float
    related_alert_id: int | None = None


@dataclass(frozen=True)
class ClipBackendUploadResult:
    ok: bool
    step: str
    clip_id: str | None = None
    s3_key: str | None = None
    upload_url_status: int | None = None
    s3_put_status: int | None = None
    confirm_status: int | None = None
    retry_pending: bool = False
    error_text: str | None = None


class BackendClipUploader:
    def __init__(
        self,
        config: BackendConfig,
        *,
        urlopen: Callable[[urllib.request.Request, float], Any] | None = None,
    ) -> None:
        self.config = config
        self.urlopen = urlopen or urllib.request.urlopen

    def upload_clip(self, request: ClipBackendUploadRequest) -> ClipBackendUploadResult:
        upload_payload = self._build_upload_url_payload(request)
        upload_response = self._post_json(self.config.clip_upload_url, upload_payload)
        if not upload_response.ok:
            self._record_backend_failure("backend_clip_upload_url", self.config.clip_upload_url, upload_payload, upload_response)
            return ClipBackendUploadResult(
                ok=False,
                step="upload_url",
                upload_url_status=upload_response.status_code,
                retry_pending=is_retryable_response(upload_response),
                error_text=upload_response.text,
            )

        upload_body = upload_response.json_body if isinstance(upload_response.json_body, dict) else {}
        clip_id = upload_body.get("clip_id")
        s3_key = upload_body.get("s3_key")
        presigned_url = upload_body.get("presigned_url")
        if not clip_id or not s3_key or not presigned_url:
            response = BackendResponse(ok=False, status_code=422, text="missing clip upload-url fields")
            self._record_backend_failure("backend_clip_upload_url_contract", self.config.clip_upload_url, upload_payload, response)
            return ClipBackendUploadResult(
                ok=False,
                step="upload_url_contract",
                upload_url_status=upload_response.status_code,
                error_text=response.text,
            )

        put_response = self._put_mp4(str(presigned_url), request.file_path)
        if not put_response.ok:
            pending_payload = {
                "upload_url_request": upload_payload,
                "file_path": str(request.file_path),
                "clip_id": str(clip_id),
                "s3_key": str(s3_key),
                "retry_action": "request_new_presigned_url_then_put",
            }
            self._record_backend_failure("backend_clip_upload", self.config.clip_upload_url, pending_payload, put_response)
            return ClipBackendUploadResult(
                ok=False,
                step="s3_put",
                clip_id=str(clip_id),
                s3_key=str(s3_key),
                upload_url_status=upload_response.status_code,
                s3_put_status=put_response.status_code,
                retry_pending=is_retryable_response(put_response),
                error_text=put_response.text,
            )

        confirm_payload = {
            "clip_id": str(clip_id),
            "actual_duration_sec": float(request.duration_sec),
            "file_size_bytes": int(request.file_path.stat().st_size),
        }
        confirm_response = self._post_json(self.config.clip_confirm_url, confirm_payload)
        if not confirm_response.ok:
            pending_payload = {
                "confirm": confirm_payload,
                "clip_id": str(clip_id),
                "s3_key": str(s3_key),
            }
            self._record_backend_failure("backend_clip_confirm", self.config.clip_confirm_url, pending_payload, confirm_response)
            return ClipBackendUploadResult(
                ok=False,
                step="confirm",
                clip_id=str(clip_id),
                s3_key=str(s3_key),
                upload_url_status=upload_response.status_code,
                s3_put_status=put_response.status_code,
                confirm_status=confirm_response.status_code,
                retry_pending=is_retryable_response(confirm_response),
                error_text=confirm_response.text,
            )

        return ClipBackendUploadResult(
            ok=True,
            step="confirm",
            clip_id=str(clip_id),
            s3_key=str(s3_key),
            upload_url_status=upload_response.status_code,
            s3_put_status=put_response.status_code,
            confirm_status=confirm_response.status_code,
        )

    def _build_upload_url_payload(self, request: ClipBackendUploadRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "patient_id": request.patient_id,
            "device_key": request.device_key,
            "event_type": request.event_type,
            "occurred_at": request.occurred_at,
            "duration_sec": max(1, min(int(round(request.duration_sec)), 600)),
        }
        if request.related_alert_id is not None:
            payload["related_alert_id"] = int(request.related_alert_id)
        return payload

    def _post_json(self, url: str, payload: dict[str, Any]) -> BackendResponse:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        return self._send(http_request)

    def _put_mp4(self, url: str, file_path: Path) -> BackendResponse:
        http_request = urllib.request.Request(
            url,
            data=file_path.read_bytes(),
            headers={"Content-Type": "video/mp4"},
            method="PUT",
        )
        return self._send(http_request)

    def _send(self, request: urllib.request.Request) -> BackendResponse:
        try:
            with self.urlopen(request, timeout=self.config.request_timeout_sec) as response:
                text = response.read().decode("utf-8", errors="replace")
                return BackendResponse(
                    ok=200 <= int(response.status) < 300,
                    status_code=int(response.status),
                    text=text,
                    json_body=self._parse_json(text),
                )
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else str(exc.reason)
            return BackendResponse(ok=False, status_code=int(exc.code), text=text, json_body=self._parse_json(text))
        except urllib.error.URLError as exc:
            return BackendResponse(ok=False, status_code=0, text=str(exc.reason))
        except TimeoutError as exc:
            return BackendResponse(ok=False, status_code=0, text=str(exc))

    def _record_backend_failure(self, kind: str, url: str, payload: dict[str, Any], response: BackendResponse) -> None:
        if is_retryable_response(response):
            append_pending(
                self.config.pending_file,
                kind=kind,
                url=url,
                payload=payload,
                response=response,
                max_records=self.config.max_pending_events,
                max_age_hours=self.config.max_pending_age_hours,
            )
            return
        append_contract_error(
            self.config.contract_error_file,
            kind=kind,
            url=url,
            payload=payload,
            response=response,
        )

    @staticmethod
    def _parse_json(text: str) -> Any | None:
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
