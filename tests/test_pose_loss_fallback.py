from __future__ import annotations

import unittest
from types import SimpleNamespace

from edge.main import build_pose_loss_fallback_skeleton_frames


class PoseLossFallbackTests(unittest.TestCase):
    def test_builds_recent_fallback_skeleton_frame_when_detection_temporarily_drops(self) -> None:
        detection = SimpleNamespace(
            bbox=[10, 20, 110, 220],
            bbox_confidence=0.8,
            keypoints=[[float(index), float(index + 1), 0.6] for index in range(17)],
            pose_confidence_mean=0.6,
        )
        people = [
            {
                "detection": detection,
                "track_id": 7,
                "feature_map": {"pose_confidence_mean": 0.6, "center_velocity_px_s": 42.0},
            }
        ]

        frames = build_pose_loss_fallback_skeleton_frames(
            camera_id="raspi_cam01",
            room_id="room_01",
            pose_model="yolo26s-pose",
            people=people,
            timestamp_ms=2000,
            capture_ts="2026-06-24T00:00:02Z",
            analysis_ts="2026-06-24T00:00:02.100Z",
            age_ms=300,
            confidence_scale=0.5,
        )

        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.frame_id, "raspi_cam01-2000-7-pose-lost")
        self.assertEqual(frame.track_id, 7)
        self.assertEqual(frame.bbox.as_list(), [10, 20, 110, 220])
        self.assertAlmostEqual(frame.pose_confidence_mean, 0.3, places=4)
        self.assertAlmostEqual(frame.keypoints[0].confidence, 0.3, places=4)
        self.assertEqual(frame.features["pose_lost_fallback"], 1.0)
        self.assertEqual(frame.features["pose_lost_duration_ms"], 300.0)


if __name__ == "__main__":
    unittest.main()
