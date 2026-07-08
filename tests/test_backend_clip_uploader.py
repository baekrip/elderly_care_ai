from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from server.services.backend_clip_uploader import BackendClipUploader, ClipBackendUploadRequest
from server.services.backend_forwarder import BackendConfig


class _Response:
    def __init__(self, status: int, body: dict | str = "") -> None:
        self.status = status
        self._body = json.dumps(body).encode("utf-8") if isinstance(body, dict) else str(body).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _Urlopen:
    def __init__(self, responses: list[_Response | HTTPError]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def __call__(self, request, timeout: float) -> _Response:
        self.calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "headers": dict(request.header_items()),
                "data": request.data,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, HTTPError):
            raise response
        return response


class _StdlibStyleUrlopen:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses

    def __call__(self, request, data=None, timeout=None) -> _Response:
        if data is not None:
            raise TypeError(f"message_body should be bytes-like, got {type(data)!r}")
        response = self.responses.pop(0)
        return response


class BackendClipUploaderTests(unittest.TestCase):
    def test_upload_clip_runs_upload_url_put_and_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "clip.mp4"
            clip_path.write_bytes(b"mp4")
            urlopen = _Urlopen(
                [
                    _Response(
                        201,
                        {
                            "clip_id": "clip-1",
                            "s3_key": "clips/P001/clip-1.mp4",
                            "presigned_url": "https://bucket.s3.amazonaws.com/clips/P001/clip-1.mp4?X-Amz-Signature=abc",
                        },
                    ),
                    _Response(200, ""),
                    _Response(200, {"stored": True}),
                ]
            )
            uploader = BackendClipUploader(
                BackendConfig(
                    enabled=True,
                    base_url="http://backend.example:5000",
                    token="token",
                    pending_file=str(Path(tmpdir) / "pending.jsonl"),
                ),
                urlopen=urlopen,
            )

            result = uploader.upload_clip(
                ClipBackendUploadRequest(
                    file_path=clip_path,
                    patient_id="P001",
                    device_key="pi5-home001-cam1",
                    event_type="fall_detected",
                    occurred_at="2026-06-09T08:18:13Z",
                    duration_sec=3.0,
                    related_alert_id=9,
                )
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.clip_id, "clip-1")
            self.assertEqual([call["method"] for call in urlopen.calls], ["POST", "PUT", "POST"])
            upload_url_payload = json.loads(urlopen.calls[0]["data"].decode("utf-8"))
            self.assertEqual(upload_url_payload["related_alert_id"], 9)
            self.assertEqual(urlopen.calls[1]["headers"]["Content-type"], "video/mp4")
            confirm_payload = json.loads(urlopen.calls[2]["data"].decode("utf-8"))
            self.assertEqual(confirm_payload["clip_id"], "clip-1")
            self.assertEqual(confirm_payload["file_size_bytes"], 3)

    def test_upload_clip_passes_timeout_as_keyword_for_stdlib_urlopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "clip.mp4"
            clip_path.write_bytes(b"mp4")
            urlopen = _StdlibStyleUrlopen(
                [
                    _Response(
                        201,
                        {
                            "clip_id": "clip-1",
                            "s3_key": "clips/P001/clip-1.mp4",
                            "presigned_url": "https://bucket.s3.amazonaws.com/clips/P001/clip-1.mp4",
                        },
                    ),
                    _Response(200, ""),
                    _Response(200, {"stored": True}),
                ]
            )
            uploader = BackendClipUploader(
                BackendConfig(
                    enabled=True,
                    base_url="http://backend.example:5000",
                    token="token",
                    pending_file=str(Path(tmpdir) / "pending.jsonl"),
                ),
                urlopen=urlopen,
            )

            result = uploader.upload_clip(
                ClipBackendUploadRequest(
                    file_path=clip_path,
                    patient_id="P001",
                    device_key="pi5-home001-cam1",
                    event_type="fall_detected",
                    occurred_at="2026-06-09T08:18:13Z",
                    duration_sec=3.0,
                )
            )

            self.assertTrue(result.ok)

    def test_s3_put_retryable_failure_keeps_pending_without_presigned_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pending_file = Path(tmpdir) / "pending.jsonl"
            clip_path = Path(tmpdir) / "clip.mp4"
            clip_path.write_bytes(b"mp4")
            urlopen = _Urlopen(
                [
                    _Response(
                        201,
                        {
                            "clip_id": "clip-1",
                            "s3_key": "clips/P001/clip-1.mp4",
                            "presigned_url": "https://bucket.s3.amazonaws.com/clips/P001/clip-1.mp4?X-Amz-Signature=secret",
                        },
                    ),
                    HTTPError(
                        "https://bucket.s3.amazonaws.com/clips/P001/clip-1.mp4?X-Amz-Signature=secret",
                        503,
                        "unavailable",
                        {},
                        None,
                    ),
                ]
            )
            uploader = BackendClipUploader(
                BackendConfig(
                    enabled=True,
                    base_url="http://backend.example:5000",
                    token="token",
                    pending_file=str(pending_file),
                ),
                urlopen=urlopen,
            )

            result = uploader.upload_clip(
                ClipBackendUploadRequest(
                    file_path=clip_path,
                    patient_id="P001",
                    device_key="pi5-home001-cam1",
                    event_type="fall_detected",
                    occurred_at="2026-06-09T08:18:13Z",
                    duration_sec=3.0,
                )
            )

            self.assertFalse(result.ok)
            self.assertTrue(result.retry_pending)
            pending_text = pending_file.read_text(encoding="utf-8")
            self.assertIn("backend_clip_upload", pending_text)
            self.assertNotIn("X-Amz-Signature", pending_text)
            self.assertNotIn("https://bucket.s3.amazonaws.com", pending_text)


if __name__ == "__main__":
    unittest.main()
