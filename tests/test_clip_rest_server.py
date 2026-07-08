from __future__ import annotations

import unittest
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from edge.clip_rest_server import ClipRequestHandler


class _Manager:
    def __init__(self) -> None:
        self.payloads: list[object] = []

    def handle_clip_request(self, event: object) -> str:
        self.payloads.append(event)
        return "edge/storage/buffer/clip_evt01.mp4"


class ClipRestServerTests(unittest.TestCase):
    def test_handler_converts_json_to_clip_request_event(self) -> None:
        manager = _Manager()
        handler = ClipRequestHandler(manager)

        result = handler.handle_json(
            {
                "event_id": "evt01",
                "camera_id": "raspi_cam01",
                "timestamp_ms": 1000,
                "label": "fall_detected",
                "clip_start_ms": 0,
                "clip_end_ms": 2000,
            }
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["clip_path"], "edge/storage/buffer/clip_evt01.mp4")
        self.assertEqual(manager.payloads[0].event_id, "evt01")

    def test_http_endpoint_rejects_missing_api_key_when_configured(self) -> None:
        manager = _Manager()
        handler = ClipRequestHandler(
            manager,
            api_key="clip-secret",
            api_key_header="X-Edge-Clip-Key",
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler._make_request_handler())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = json.dumps(
                {
                    "event_id": "evt01",
                    "camera_id": "raspi_cam01",
                    "timestamp_ms": 1000,
                    "label": "fall_detected",
                    "clip_start_ms": 0,
                    "clip_end_ms": 2000,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/clip/request",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=2)

            self.assertEqual(ctx.exception.code, 401)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_http_endpoint_accepts_matching_api_key_when_configured(self) -> None:
        manager = _Manager()
        handler = ClipRequestHandler(
            manager,
            api_key="clip-secret",
            api_key_header="X-Edge-Clip-Key",
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler._make_request_handler())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = json.dumps(
                {
                    "event_id": "evt01",
                    "camera_id": "raspi_cam01",
                    "timestamp_ms": 1000,
                    "label": "fall_detected",
                    "clip_start_ms": 0,
                    "clip_end_ms": 2000,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/clip/request",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Edge-Clip-Key": "clip-secret",
                },
                method="POST",
            )

            with urllib.request.urlopen(request, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(response.status, 200)
            self.assertEqual(payload["status"], "ok")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
