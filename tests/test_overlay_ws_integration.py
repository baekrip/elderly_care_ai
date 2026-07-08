from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.overlay_ws import router as overlay_router
from server.api.skeleton_ws import router as skeleton_router
from server.db import database as db_module
from server.services.overlay_broadcaster import OverlayBroadcaster


class _Pipeline:
    def handle_batch(self, batch):
        frame = batch.frames[0]
        return {
            "processed": len(batch.frames),
            "events": [
                {
                    "frame_id": frame.frame_id,
                    "risk_label": "danger",
                    "risk_score": 5,
                    "state": "DANGEROUS",
                }
            ],
        }


class OverlayWsIntegrationTests(unittest.TestCase):
    def test_skeleton_ingest_publishes_bbox_keypoints_to_overlay_ws(self) -> None:
        app = FastAPI()
        app.state.pi5_pipeline = _Pipeline()
        app.state.overlay_broadcaster = OverlayBroadcaster()
        app.include_router(skeleton_router)
        app.include_router(overlay_router)

        previous_session = db_module.AsyncSessionLocal
        db_module.AsyncSessionLocal = None
        try:
            client = TestClient(app)
            with client.websocket_connect("/ws/overlay/raspi_cam01") as overlay_ws:
                with client.websocket_connect("/ws/skeleton/raspi_cam01") as skeleton_ws:
                    skeleton_ws.send_json(
                        {
                            "camera_id": "raspi_cam01",
                            "frame_id": "f-1",
                            "timestamp_ms": 1000,
                            "capture_ts": "2026-06-12T00:00:00.000Z",
                            "analysis_ts": "2026-06-12T00:00:00.180Z",
                            "track_id": 1,
                            "bbox": {"x1": 10, "y1": 20, "x2": 100, "y2": 200},
                            "bbox_confidence": 0.9,
                            "keypoints": [{"x": 1.0, "y": 2.0, "confidence": 0.8}],
                            "pose_confidence_mean": 0.8,
                            "features": {"source_width": 640, "source_height": 360, "action_label": "WALKING"},
                            "event_state": "DANGEROUS",
                        }
                    )
                    ack = skeleton_ws.receive_json()
                    payload = overlay_ws.receive_json()
        finally:
            db_module.AsyncSessionLocal = previous_session

        self.assertEqual(ack["status"], "ok")
        self.assertEqual(payload["camera_id"], "raspi_cam01")
        self.assertEqual(payload["source_width"], 640)
        self.assertEqual(payload["source_height"], 360)
        self.assertEqual(payload["tracks"][0]["bbox"], [10, 20, 100, 200])
        self.assertEqual(payload["tracks"][0]["keypoints"], [[1.0, 2.0, 0.8]])
        self.assertEqual(payload["tracks"][0]["action_label"], "WALKING")
        self.assertEqual(payload["tracks"][0]["risk_label"], "DANGER")
        self.assertEqual(payload["tracks"][0]["risk_score"], 5)
        self.assertIsInstance(payload["tracks"][0]["risk_score"], int)
        self.assertEqual(payload["tracks"][0]["event_state"], "DANGEROUS")


if __name__ == "__main__":
    unittest.main()
