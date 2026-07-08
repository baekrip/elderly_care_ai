from __future__ import annotations

import unittest
from pathlib import Path


class StreamingOverlayHtmlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = Path("docs/스트리밍.html").read_text(encoding="utf-8")

    def test_canvas_overlay_verifier_controls_are_present(self) -> None:
        required_ids = [
            'id="overlay-viewer"',
            'id="webrtc-url"',
            'id="overlay-ws-url"',
            'id="overlay-api-key"',
            'id="overlay-device-id"',
            'id="connect-overlay"',
            'id="overlay-canvas"',
            'id="overlay-status"',
        ]

        for marker in required_ids:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_overlay_script_handles_schema_scale_and_stale_frames(self) -> None:
        required_markers = [
            "function normalizeOverlayFrame",
            "function drawOverlayFrame",
            "function resizeOverlayCanvas",
            "const staleThresholdMs = 200",
            "frame.source_width",
            "frame.source_height",
            "tracks.forEach",
            'searchParams.set("edge_api_key"',
            'searchParams.set("device_id"',
            "WebSocket",
        ]

        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_backend_alert_api_panel_is_present(self) -> None:
        required_markers = [
            'id="backend-status-panel"',
            'id="backend-alert-url"',
            'id="backend-app-token"',
            'id="post-backend-alert"',
            'id="backend-alert-status"',
            'id="backend-event-log"',
            'Authorization": `Bearer ${token}`',
            'method: "POST"',
            "/api/v1/alerts/immediate",
            '"user_facing_alert_sent": false',
        ]

        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

        self.assertNotIn("new EventSource", self.html)
        self.assertNotIn("response.body.getReader()", self.html)
        self.assertNotIn("parseBackendSseChunk", self.html)
        self.assertNotIn("/api/v1/alerts/stream", self.html)

    def test_page_keeps_backend_websocket_out_of_the_flow(self) -> None:
        self.assertIn("Orin", self.html)
        self.assertNotIn("/api/v1/ws", self.html)

    def test_verdict_prefers_pi5_overlay_stream_with_backend_alert_panel(self) -> None:
        self.assertIn("방안 3, Pi5 직접 Overlay Stream", self.html)
        self.assertIn("Pi5 로컬 렌더링", self.html)
        self.assertIn("backend alert API panel", self.html)
        self.assertIn("http://54.116.119.98:8889/P001/", self.html)
        self.assertIn("Orin overlay WebSocket은 최종 경로가 아니다", self.html)
        self.assertIn("행동/위험 판단은 backend event/alert API 결과 또는 JSON 패널", self.html)


if __name__ == "__main__":
    unittest.main()
