from __future__ import annotations

import unittest

from shared.protocol import ActivityFrame, CandidateWindowFrame, ClipRequestEvent, PosePerson


class ProtocolTimingTests(unittest.TestCase):
    def test_activity_frame_accepts_capture_and_analysis_timestamps(self) -> None:
        frame = ActivityFrame(
            camera_id="cam01",
            frame_id="cam01-1",
            timestamp_ms=1_714_838_400_000,
            capture_ts="2024-05-04T16:00:00.000Z",
            analysis_ts="2024-05-04T16:00:00.020Z",
            persons=[],
        )

        self.assertEqual(frame.capture_ts, "2024-05-04T16:00:00.000Z")
        self.assertEqual(frame.analysis_ts, "2024-05-04T16:00:00.020Z")

    def test_pose_person_accepts_xgboost_runtime_metadata(self) -> None:
        person = PosePerson(
            track_id=1,
            bbox={"x1": 0, "y1": 1, "x2": 2, "y2": 3},
            bbox_confidence=0.9,
            keypoints=[{"x": 1.0, "y": 2.0, "confidence": 0.8}],
            pose_confidence_mean=0.8,
            features={},
            action_label="LYING",
            action_confidence=0.7,
            risk_label="DROP",
            risk_confidence=0.91,
            label_source="xgboost",
            xgboost_prob=0.91,
            xgboost_tier_ms=1.25,
            pose_model="yolo26s-pose",
            classifier_model="xgboost_action.json",
        )

        dumped = person.model_dump(mode="json")

        self.assertEqual(dumped["label_source"], "xgboost")
        self.assertEqual(dumped["xgboost_prob"], 0.91)
        self.assertEqual(dumped["xgboost_tier_ms"], 1.25)

    def test_candidate_frame_accepts_per_model_timing(self) -> None:
        frame = CandidateWindowFrame(
            frame_idx=0,
            ts_ms=1_714_838_400_000,
            capture_ts="2024-05-04T16:00:00.000Z",
            analysis_ts="2024-05-04T16:00:00.020Z",
            pose_conf_mean=0.9,
            bbox_xyxy=[0, 1, 2, 3],
            keypoints_17=[[0.0, 0.0, 0.9] for _ in range(17)],
            features={},
            inference_timing={"xgboost_action_ms": 1.2, "xgboost_tier_ms": 2.4},
        )

        self.assertEqual(frame.inference_timing["xgboost_action_ms"], 1.2)
        self.assertEqual(frame.inference_timing["xgboost_tier_ms"], 2.4)

    def test_clip_request_accepts_explicit_clip_interval(self) -> None:
        event = ClipRequestEvent(
            event_id="evt01",
            camera_id="cam01",
            timestamp_ms=1_000_000,
            label="DROP",
            clip_start_ms=910_000,
            clip_end_ms=1_090_000,
            clip_duration_ms=180_000,
            storage_policy="segment_ring",
        )

        self.assertEqual(event.clip_start_ms, 910_000)
        self.assertEqual(event.clip_end_ms, 1_090_000)
        self.assertEqual(event.clip_duration_ms, 180_000)
        self.assertEqual(event.storage_policy, "segment_ring")


if __name__ == "__main__":
    unittest.main()
