from __future__ import annotations

import unittest
from types import SimpleNamespace

from server.api.candidates import _parse_candidate_batch_payload, submit_candidates


class CandidateIngestCompatTests(unittest.TestCase):
    def test_legacy_bbox_keypoints_and_timestamp_fields_are_normalized(self) -> None:
        payload = {
            "windows": [
                {
                    "device_id": "pi5-01",
                    "camera_id": "raspi_cam01",
                    "track_id": 7,
                    "candidate_type": "TIER_DROP_SUSPECT",
                    "candidate_category": "ABNORMAL",
                    "composite_condition": "legacy sender",
                    "coarse_action": "LYING",
                    "coarse_confidence": 0.82,
                    "capture_ts": "not-a-date",
                    "trigger_flags": ["torso_angle_spike"],
                    "sequence": [
                        {
                            "frame_idx": 0,
                            "timestamp_ms": 1_714_838_400_000,
                            "pose_confidence_mean": 0.91,
                            "bbox": {"x1": 10, "y1": 20, "x2": 110, "y2": 220},
                            "keypoints": [
                                {"x": 1.0, "y": 2.0, "confidence": 0.9}
                                for _ in range(17)
                            ],
                            "features": {
                                "center_velocity_px_s": 180.0,
                                "ignored_text": "not numeric",
                            },
                        }
                    ],
                }
            ]
        }

        batch = _parse_candidate_batch_payload(payload)
        window = batch.windows[0]
        frame = window.sequence[0]

        self.assertEqual(frame.ts_ms, 1_714_838_400_000)
        self.assertEqual(frame.bbox_xyxy, [10, 20, 110, 220])
        self.assertEqual(frame.keypoints_17[0], [1.0, 2.0, 0.9])
        self.assertEqual(frame.features, {"center_velocity_px_s": 180.0})
        self.assertEqual(window.capture_ts, "2024-05-04T16:00:00.000Z")

    def test_abnormal_candidate_can_queue_backend_event_without_alert_response(self) -> None:
        import asyncio

        class _Classifier:
            model = None

            def classify(self, window: object) -> tuple[str, float, str]:
                return "WALKING", 0.7, "test"

        class _Archive:
            def write_candidate_request(self, payload: dict) -> None:
                pass

            def write_stgcn_result(self, payload: dict) -> None:
                pass

            def write_clip_request(self, payload: dict) -> None:
                pass

        class _Hub:
            async def send_to_camera(self, camera_id: str, payload: dict) -> bool:
                return False

        class _Batcher:
            def submit(self, event: dict) -> object:
                return SimpleNamespace(response=None, pending_replay=None, forwarded=0)

        class _Session:
            def add(self, row: object) -> None:
                pass

            async def commit(self) -> None:
                pass

        payload = {
            "windows": [
                {
                    "device_id": "pi5-01",
                    "camera_id": "raspi_cam01",
                    "track_id": 7,
                    "candidate_type": "FAST_MOTION",
                    "candidate_category": "ABNORMAL",
                    "composite_condition": "motion spike",
                    "coarse_action": "WALKING",
                    "coarse_confidence": 0.82,
                    "trigger_flags": ["center_velocity_spike"],
                    "sequence": [
                        {
                            "frame_idx": 0,
                            "ts_ms": 1_714_838_400_000,
                            "pose_conf_mean": 0.91,
                            "bbox_xyxy": [10, 20, 110, 220],
                            "keypoints_17": [[1.0, 2.0, 0.9] for _ in range(17)],
                            "features": {"center_velocity_px_s": 180.0},
                        }
                    ],
                }
            ]
        }
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    stgcn_classifier=_Classifier(),
                    edge_hub=_Hub(),
                    result_archive=_Archive(),
                    backend_event_batcher=_Batcher(),
                    config={
                        "patient": {"patient_id": "P001"},
                        "events": {"danger_labels": ["DROP", "FALL"]},
                        "backend": {
                            "enabled": True,
                            "base_url": "http://backend.example:5000",
                            "token": "token",
                        },
                    },
                )
            )
        )

        result = asyncio.run(submit_candidates(payload, request, _Session()))

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["effective_level"], "abnormal")
        self.assertEqual(result["results"][0]["backend_forward"]["event_queued"], True)

    def test_normal_final_label_overrides_danger_candidate_and_skips_clip(self) -> None:
        import asyncio

        class _Classifier:
            model = None

            def classify(self, window: object) -> tuple[str, float, str]:
                return "NORMAL", 0.9, "test"

        class _Archive:
            def write_candidate_request(self, payload: dict) -> None:
                pass

            def write_stgcn_result(self, payload: dict) -> None:
                pass

            def write_clip_request(self, payload: dict) -> None:
                pass

        class _Hub:
            async def send_to_camera(self, camera_id: str, payload: dict) -> bool:
                return False

        class _Batcher:
            def __init__(self) -> None:
                self.submitted: list[dict] = []

            def submit(self, event: dict) -> object:
                self.submitted.append(event)
                return SimpleNamespace(response=None, pending_replay=None, forwarded=0)

        class _Session:
            def add(self, row: object) -> None:
                pass

            async def commit(self) -> None:
                pass

        normal_batcher = _Batcher()
        event_batcher = _Batcher()
        payload = {
            "windows": [
                {
                    "device_id": "pi5-01",
                    "camera_id": "raspi_cam01",
                    "track_id": 7,
                    "candidate_type": "DANGER_DROP",
                    "candidate_category": "DANGER",
                    "composite_condition": "normal flow",
                    "coarse_action": "WALKING",
                    "coarse_confidence": 0.82,
                    "sequence": [
                        {
                            "frame_idx": 0,
                            "ts_ms": 1_714_838_400_000,
                            "pose_conf_mean": 0.91,
                            "bbox_xyxy": [10, 20, 110, 220],
                            "keypoints_17": [[1.0, 2.0, 0.9] for _ in range(17)],
                            "features": {"center_velocity_px_s": 80.0},
                        }
                    ],
                }
            ]
        }
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    stgcn_classifier=_Classifier(),
                    edge_hub=_Hub(),
                    result_archive=_Archive(),
                    backend_event_batcher=event_batcher,
                    backend_normal_batcher=normal_batcher,
                    config={
                        "patient": {"patient_id": "P001"},
                        "events": {"danger_labels": ["DROP", "FALL"]},
                        "backend": {
                            "enabled": True,
                            "base_url": "http://backend.example:5000",
                            "token": "token",
                        },
                    },
                )
            )
        )

        result = asyncio.run(submit_candidates(payload, request, _Session()))

        self.assertEqual(result["results"][0]["effective_level"], "normal")
        self.assertFalse(result["results"][0]["clip_requested"])
        self.assertEqual(len(normal_batcher.submitted), 1)
        self.assertEqual(len(event_batcher.submitted), 0)


if __name__ == "__main__":
    unittest.main()
