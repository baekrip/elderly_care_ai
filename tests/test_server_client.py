from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from edge.sender import ServerClient
from shared.protocol import CameraRegistration


class _Response:
    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _Response()


class ServerClientTests(unittest.TestCase):
    def test_upload_clip_uses_configured_path_and_api_key_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            clip_path = Path(tmpdir) / "clip.mp4"
            clip_path.write_bytes(b"fake mp4")
            client = ServerClient(
                {
                    "server": {
                        "enabled": True,
                        "base_url": "http://orin.local:8000",
                        "request_timeout_sec": 3,
                        "clip_upload_path": "/media/clips/upload",
                        "clip_upload_api_key": "clip-secret",
                        "clip_upload_api_key_header": "X-API-Key",
                    }
                }
            )
            session = _Session()
            client.session = session

            client.upload_clip(clip_path, {"event_id": "e1", "camera_id": "cam01"})

            self.assertEqual(session.calls[0]["url"], "http://orin.local:8000/media/clips/upload")
            self.assertEqual(session.calls[0]["headers"], {"X-API-Key": "clip-secret"})

    def test_json_ingest_posts_use_configured_ingest_api_key_header(self) -> None:
        client = ServerClient(
            {
                "camera": {"camera_id": "raspi_cam01"},
                "server": {
                    "enabled": True,
                    "base_url": "http://orin.local:8000",
                    "request_timeout_sec": 3,
                    "ingest_api_key": "edge-secret",
                    "ingest_api_key_header": "X-Edge-API-Key",
                }
            }
        )
        session = _Session()
        client.session = session

        client.register_camera(
            CameraRegistration(
                camera_id="raspi_cam01",
                room_id="room_01",
                display_name="Pi5",
                stream_url="rtsp://pi5/raspi_cam01",
            )
        )

        self.assertEqual(session.calls[0]["url"], "http://orin.local:8000/api/cameras/register")
        self.assertEqual(
            session.calls[0]["headers"],
            {"X-Edge-API-Key": "edge-secret", "X-Device-ID": "raspi_cam01"},
        )


if __name__ == "__main__":
    unittest.main()
