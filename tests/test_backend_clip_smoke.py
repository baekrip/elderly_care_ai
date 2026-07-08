from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.test_backend_clip_smoke import (
    backend_base_url_from_env,
    build_live_clip_smoke_report,
    build_mock_clip_smoke_report,
)


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
    def __init__(self) -> None:
        self.responses = [
            _Response(
                201,
                {
                    "clip_id": "clip-1",
                    "s3_key": "clips/P001/clip-1.mp4",
                    "presigned_url": "https://bucket.s3.amazonaws.com/clips/P001/clip-1.mp4?X-Amz-Signature=secret",
                },
            ),
            _Response(200, ""),
            _Response(200, {"stored": True}),
        ]

    def __call__(self, request, timeout: float) -> _Response:
        del request, timeout
        return self.responses.pop(0)


class BackendClipSmokeTests(unittest.TestCase):
    def test_backend_base_url_from_env_accepts_ai2_server_url_alias(self) -> None:
        self.assertEqual(
            backend_base_url_from_env({"AI2_SERVER_URL": "http://backend.example:5000/"}),
            "http://backend.example:5000",
        )

    def test_mock_clip_smoke_masks_upload_url_and_credentials(self) -> None:
        report = build_mock_clip_smoke_report(
            upload_url="https://bucket.s3.amazonaws.com/clips/sample.mp4?X-Amz-Signature=abcdef",
            app_token="test-app-token",
            backend_base_url="http://54.180.121.39:5000",
            file_size_bytes=1234,
        )
        dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)

        self.assertTrue(report["mock"])
        self.assertFalse(report["live_backend_sent"])
        self.assertEqual(report["steps"]["upload_url"]["status_code"], 200)
        self.assertEqual(report["steps"]["upload_url"]["s3_key"], "clips/sample.mp4")
        self.assertEqual(report["steps"]["s3_put"]["content_type"], "video/mp4")
        self.assertEqual(report["file_size_bytes"], 1234)
        self.assertNotIn("https://bucket.s3.amazonaws.com/clips/sample.mp4", dumped)
        self.assertNotIn("X-Amz-Signature", dumped)
        self.assertNotIn("test-app-token", dumped)
        self.assertNotIn("54.180.121.39", dumped)

    def test_mock_upload_url_4xx_is_contract_error_not_retry_pending(self) -> None:
        report = build_mock_clip_smoke_report(upload_url_status=400)

        self.assertEqual(report["status"], "blocked_contract_error")
        self.assertEqual(report["error_code"], "upload_url_contract_error")
        self.assertFalse(report["retry_pending"])

    def test_mock_s3_put_failure_keeps_pending_metadata_without_presigned_url(self) -> None:
        report = build_mock_clip_smoke_report(s3_put_status=503)
        dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)

        self.assertEqual(report["status"], "pending_retryable_upload")
        self.assertTrue(report["retry_pending"])
        self.assertEqual(report["pending"]["s3_key"], "clips/sample.mp4")
        self.assertEqual(report["pending"]["status"], "s3_put_failed")
        self.assertNotIn("X-Amz-Signature", dumped)
        self.assertNotIn("https://bucket.s3.amazonaws.com", dumped)

    def test_live_clip_smoke_report_masks_token_and_presigned_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "clip.mp4"
            clip_path.write_bytes(b"mp4")

            report = build_live_clip_smoke_report(
                backend_base_url="http://backend.example:5000",
                app_token="secret-token",
                clip_file=clip_path,
                patient_id="P001",
                device_key="pi5-home001-cam1",
                event_type="fall_detected",
                occurred_at="2026-06-09T08:18:13Z",
                duration_sec=3.0,
                urlopen=_Urlopen(),
                pending_file=Path(tmpdir) / "pending.jsonl",
            )
            dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)

            self.assertFalse(report["mock"])
            self.assertTrue(report["live_backend_sent"])
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["steps"]["upload_url"]["status_code"], 201)
            self.assertEqual(report["steps"]["s3_put"]["status_code"], 200)
            self.assertEqual(report["steps"]["confirm"]["status_code"], 200)
            self.assertNotIn("secret-token", dumped)
            self.assertNotIn("X-Amz-Signature", dumped)
            self.assertNotIn("https://bucket.s3.amazonaws.com", dumped)


if __name__ == "__main__":
    unittest.main()
