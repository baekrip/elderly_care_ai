from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api.skeleton_ws import router as skeleton_router
from server.db import database as db_module
from server.services.backend_forwarder import BackendBatchSubmitResult, BackendResponse


class _Pipeline:
    def handle_batch(self, batch):
        frame = batch.frames[0]
        return {
            "processed": len(batch.frames),
            "events": [
                {
                    "event_id": "evt-local-1",
                    "camera_id": frame.camera_id,
                    "frame_id": frame.frame_id,
                    "track_id": frame.track_id,
                    "event_type": "running_over_speed",
                    "state": "DANGEROUS",
                    "risk_label": "danger",
                    "risk_score": 5,
                    "risk_confidence": 0.91,
                    "timestamp_ms": frame.timestamp_ms,
                    "capture_ts": frame.capture_ts,
                    "analysis_ts": "2026-06-12T00:00:00.200Z",
                    "model_outputs": {
                        "fusion": {
                            "final_label": "fall_confirmed",
                            "final_fall_probability": 0.82,
                        }
                    },
                }
            ],
        }


class _BackendBatcher:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.force_values: list[bool] = []

    def submit(self, event: dict, *, force: bool = False) -> BackendBatchSubmitResult:
        self.events.append(event)
        self.force_values.append(force)
        return BackendBatchSubmitResult(
            queued=0,
            forwarded=1,
            response=BackendResponse(
                ok=True,
                status_code=201,
                text='{"results":[{"event_id":123,"stored":true}]}',
                json_body={"results": [{"event_id": 123, "stored": True}]},
            ),
        )


class SkeletonBackendForwardTests(unittest.TestCase):
    def test_zero_risk_confidence_is_not_replaced_by_raw_score(self) -> None:
        from server.api.skeleton_ws import _build_backend_event_from_skeleton_event

        event = _build_backend_event_from_skeleton_event(
            {"patient": {"patient_id": "P001"}, "backend": {"device_key": "device-1"}},
            {
                "event_type": "normal_activity",
                "risk_label": "normal",
                "risk_score": 1,
                "risk_confidence": 0.0,
                "raw_score": 0.8,
                "timestamp_ms": 1000,
            },
        )

        self.assertEqual(event["confidence"], 0.0)

    def test_skeleton_ack_includes_backend_event_id_when_danger_event_is_forwarded(self) -> None:
        app = FastAPI()
        app.state.config = {
            "patient": {"patient_id": "P001"},
            "backend": {
                "enabled": True,
                "device_key": "pi5-home001-cam1",
            },
        }
        app.state.pi5_pipeline = _Pipeline()
        app.state.backend_event_batcher = _BackendBatcher()
        app.include_router(skeleton_router)

        previous_session = db_module.AsyncSessionLocal
        db_module.AsyncSessionLocal = None
        try:
            client = TestClient(app)
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
                        "features": {"source_width": 640, "source_height": 360},
                        "event_state": "DANGEROUS",
                    }
                )
                ack = skeleton_ws.receive_json()
        finally:
            db_module.AsyncSessionLocal = previous_session

        self.assertEqual(ack["status"], "ok")
        self.assertEqual(ack["backend_forward"][0]["local_event_id"], "evt-local-1")
        self.assertEqual(ack["backend_forward"][0]["ref_event_id"], 123)
        self.assertEqual(ack["backend_forward"][0]["event_status_code"], 201)
        self.assertTrue(ack["backend_forward"][0]["event_forwarded"])
        self.assertTrue(app.state.backend_event_batcher.force_values[0])
        sent_event = app.state.backend_event_batcher.events[0]
        self.assertEqual(sent_event["device_key"], "pi5-home001-cam1")
        self.assertEqual(sent_event["patient_id"], "P001")
        self.assertEqual(sent_event["event_type"], "fall_detected")
        self.assertEqual(sent_event["confidence"], 0.91)
        self.assertEqual(sent_event["payload"]["risk_score"], 5)
        self.assertEqual(sent_event["payload"]["local_event_id"], "evt-local-1")

    def test_skeleton_backend_forward_sends_one_backend_event_per_local_event_id(self) -> None:
        app = FastAPI()
        app.state.config = {
            "patient": {"patient_id": "P001"},
            "backend": {
                "enabled": True,
                "device_key": "pi5-home001-cam1",
            },
        }
        app.state.pi5_pipeline = _Pipeline()
        app.state.backend_event_batcher = _BackendBatcher()
        app.include_router(skeleton_router)

        original_handle_batch = app.state.pi5_pipeline.handle_batch

        def duplicate_events(batch):
            result = original_handle_batch(batch)
            first = dict(result["events"][0])
            second = dict(first)
            second["frame_id"] = "f-2"
            second["risk_confidence"] = 0.96
            result["events"] = [first, second]
            return result

        app.state.pi5_pipeline.handle_batch = duplicate_events
        previous_session = db_module.AsyncSessionLocal
        db_module.AsyncSessionLocal = None
        try:
            client = TestClient(app)
            with client.websocket_connect("/ws/skeleton/raspi_cam01") as skeleton_ws:
                skeleton_ws.send_json(
                    {
                        "camera_id": "raspi_cam01",
                        "frame_id": "f-1",
                        "timestamp_ms": 1000,
                        "capture_ts": "2026-06-12T00:00:00.000Z",
                        "track_id": 1,
                        "bbox": {"x1": 10, "y1": 20, "x2": 100, "y2": 200},
                        "bbox_confidence": 0.9,
                        "keypoints": [{"x": 1.0, "y": 2.0, "confidence": 0.8}],
                        "pose_confidence_mean": 0.8,
                    }
                )
                ack = skeleton_ws.receive_json()
        finally:
            db_module.AsyncSessionLocal = previous_session

        self.assertEqual(ack["event_count"], 2)
        self.assertEqual(len(ack["backend_forward"]), 1)
        self.assertEqual(len(app.state.backend_event_batcher.events), 1)
        self.assertEqual(app.state.backend_event_batcher.events[0]["payload"]["frame_id"], "f-2")


if __name__ == "__main__":
    unittest.main()
