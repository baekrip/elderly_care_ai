from __future__ import annotations

import unittest

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.security.ingest_auth import IngestAuthMiddleware


GUARDIAN_HTTP_ROUTES: tuple[str, ...] = (
    "/api/risk-events",
    "/api/activity/timeline/raspi_cam01",
    "/api/streams/raspi_cam01",
    "/api/clips",
    "/api/clips/clip01",
)


class IngestAuthMiddlewareTests(unittest.TestCase):
    def _client(self) -> TestClient:
        app = FastAPI()
        app.add_middleware(
            IngestAuthMiddleware,
            config={
                "ingest_auth": {
                    "enabled": True,
                    "api_key": "edge-secret",
                    "api_key_header": "X-Edge-API-Key",
                }
            },
        )

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/api/risk-events")
        async def risk_events() -> dict[str, str]:
            return {"status": "accepted"}

        @app.get("/api/risk-events")
        async def list_risk_events() -> dict[str, str]:
            return {"status": "listed"}

        @app.get("/api/activity/timeline/{camera_id}")
        async def get_timeline(camera_id: str) -> dict[str, str]:
            return {"camera_id": camera_id}

        @app.get("/api/streams/{camera_id}")
        async def get_stream(camera_id: str) -> dict[str, str]:
            return {"camera_id": camera_id}

        @app.get("/api/clips")
        async def list_clips() -> dict[str, str]:
            return {"status": "listed"}

        @app.get("/api/clips/{clip_id}")
        async def get_clip(clip_id: str) -> dict[str, str]:
            return {"clip_id": clip_id}

        @app.websocket("/ws/skeleton/{camera_id}")
        async def skeleton(websocket: WebSocket, camera_id: str) -> None:
            await websocket.accept()
            await websocket.send_json({"camera_id": camera_id})

        @app.websocket("/ws/overlay/{camera_id}")
        async def overlay(websocket: WebSocket, camera_id: str) -> None:
            await websocket.accept()
            await websocket.send_json({"camera_id": camera_id, "type": "overlay"})

        return TestClient(app)

    def test_protected_ingest_http_rejects_missing_api_key(self) -> None:
        client = self._client()

        response = client.post("/api/risk-events", json={"event_id": "evt01"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "invalid_ingest_api_key")

    def test_protected_ingest_http_accepts_matching_api_key(self) -> None:
        client = self._client()

        response = client.post(
            "/api/risk-events",
            json={"event_id": "evt01"},
            headers={"X-Edge-API-Key": "edge-secret", "X-Device-ID": "raspi_cam01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")

    def test_health_route_does_not_require_ingest_api_key(self) -> None:
        client = self._client()

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)

    def test_protected_guardian_http_routes_reject_missing_api_key(self) -> None:
        client = self._client()

        for path in GUARDIAN_HTTP_ROUTES:
            with self.subTest(path=path):
                response = client.get(path)

                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"], "invalid_ingest_api_key")

    def test_protected_guardian_http_routes_accept_matching_api_key(self) -> None:
        client = self._client()

        for path in GUARDIAN_HTTP_ROUTES:
            with self.subTest(path=path):
                response = client.get(
                    path,
                    headers={"X-Edge-API-Key": "edge-secret", "X-Device-ID": "raspi_cam01"},
                )

                self.assertEqual(response.status_code, 200)

    def test_protected_ingest_websocket_rejects_missing_api_key(self) -> None:
        client = self._client()

        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/ws/skeleton/raspi_cam01"):
                pass

    def test_protected_ingest_websocket_accepts_matching_api_key(self) -> None:
        client = self._client()

        with client.websocket_connect(
            "/ws/skeleton/raspi_cam01",
            headers={"X-Edge-API-Key": "edge-secret", "X-Device-ID": "raspi_cam01"},
        ) as websocket:
            message = websocket.receive_json()

        self.assertEqual(message["camera_id"], "raspi_cam01")

    def test_overlay_websocket_rejects_missing_api_key(self) -> None:
        client = self._client()

        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/ws/overlay/raspi_cam01"):
                pass

    def test_overlay_websocket_accepts_matching_api_key(self) -> None:
        client = self._client()

        with client.websocket_connect(
            "/ws/overlay/raspi_cam01",
            headers={"X-Edge-API-Key": "edge-secret", "X-Device-ID": "raspi_cam01"},
        ) as websocket:
            message = websocket.receive_json()

        self.assertEqual(message["camera_id"], "raspi_cam01")
        self.assertEqual(message["type"], "overlay")

    def test_overlay_websocket_accepts_browser_query_api_key(self) -> None:
        client = self._client()

        with client.websocket_connect(
            "/ws/overlay/raspi_cam01?edge_api_key=edge-secret&device_id=raspi_cam01",
        ) as websocket:
            message = websocket.receive_json()

        self.assertEqual(message["camera_id"], "raspi_cam01")
        self.assertEqual(message["type"], "overlay")

    def test_skeleton_websocket_does_not_accept_query_api_key(self) -> None:
        client = self._client()

        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/skeleton/raspi_cam01?edge_api_key=edge-secret&device_id=raspi_cam01",
            ):
                pass

    def test_overlay_websocket_rejects_device_id_mismatch(self) -> None:
        client = self._client()

        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/overlay/raspi_cam01",
                headers={"X-Edge-API-Key": "edge-secret", "X-Device-ID": "other_cam"},
            ):
                pass


if __name__ == "__main__":
    unittest.main()
