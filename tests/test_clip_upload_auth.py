from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from server.api.clips import (
    _existing_clip_response_for_event_id,
    _forward_clip_to_backend_if_enabled,
    validate_clip_upload_api_key,
)


class ClipUploadAuthTests(unittest.TestCase):
    def test_api_key_is_required_when_configured(self) -> None:
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config={
                        "media_server": {
                            "clip_upload_api_key": "clip-secret",
                            "clip_upload_api_key_header": "X-API-Key",
                        }
                    }
                )
            ),
            headers={"X-API-Key": "wrong"},
        )

        with self.assertRaises(HTTPException) as ctx:
            validate_clip_upload_api_key(request)

        self.assertEqual(ctx.exception.status_code, 401)

    def test_api_key_accepts_matching_header(self) -> None:
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config={
                        "media_server": {
                            "clip_upload_api_key": "clip-secret",
                            "clip_upload_api_key_header": "X-API-Key",
                        }
                    }
                )
            ),
            headers={"X-API-Key": "clip-secret"},
        )

        validate_clip_upload_api_key(request)

    def test_backend_clip_forward_skips_when_backend_is_disabled(self) -> None:
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config={
                        "media_server": {"backend_clip_forward_enabled": True},
                        "backend": {"enabled": False},
                    }
                )
            )
        )

        result = _forward_clip_to_backend_if_enabled(
            request,
            file_path=Path("clip.mp4"),
            event_id="e1",
            camera_id="cam01",
            timestamp_ms=1_714_838_400_000,
            duration_ms=3000,
            label="fall_detected",
        )

        self.assertEqual(result, {"enabled": False})

    def test_backend_clip_forward_calls_uploader_when_enabled(self) -> None:
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config={
                        "patient": {"patient_id": "P001"},
                        "media_server": {"backend_clip_forward_enabled": True},
                        "backend": {
                            "enabled": True,
                            "base_url": "http://backend.example:5000",
                            "token": "token",
                            "device_key": "pi5-home001-cam1",
                        },
                    }
                )
            )
        )

        with patch("server.api.clips.BackendClipUploader") as uploader_cls:
            uploader = uploader_cls.return_value
            uploader.upload_clip.return_value = SimpleNamespace(
                ok=True,
                step="confirm",
                clip_id="clip-1",
                s3_key="clips/P001/clip-1.mp4",
                upload_url_status=201,
                s3_put_status=200,
                confirm_status=200,
                retry_pending=False,
            )

            result = _forward_clip_to_backend_if_enabled(
                request,
                file_path=Path("clip.mp4"),
                event_id="e1",
                camera_id="cam01",
                timestamp_ms=1_714_838_400_000,
                duration_ms=3000,
                label="fall_detected",
                related_alert_id=9,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["clip_id"], "clip-1")
        sent_request = uploader.upload_clip.call_args.args[0]
        self.assertEqual(sent_request.patient_id, "P001")
        self.assertEqual(sent_request.device_key, "pi5-home001-cam1")
        self.assertEqual(sent_request.event_type, "fall_detected")
        self.assertEqual(sent_request.duration_sec, 3.0)
        self.assertEqual(sent_request.related_alert_id, 9)


class ClipUploadIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_event_id_returns_existing_clip_without_new_forward(self) -> None:
        existing = SimpleNamespace(
            clip_id="local-clip-1",
            event_id="event-1",
            camera_id="raspi_cam01",
            file_path="/tmp/local-clip-1.mp4",
        )

        class _Result:
            def scalar_one_or_none(self):
                return existing

        class _Session:
            def __init__(self) -> None:
                self.execute_calls = 0

            async def execute(self, _statement):
                self.execute_calls += 1
                return _Result()

        session = _Session()

        response = await _existing_clip_response_for_event_id(session, "event-1")

        self.assertEqual(session.execute_calls, 1)
        self.assertEqual(response["clip_id"], "local-clip-1")
        self.assertEqual(response["file_path"], "/tmp/local-clip-1.mp4")
        self.assertEqual(response["backend_forward"]["duplicate"], True)
        self.assertEqual(response["backend_forward"]["local_event_id"], "event-1")


if __name__ == "__main__":
    unittest.main()
