from __future__ import annotations

import unittest

from shared.protocol import BoundingBox, Keypoint, SkeletonFrame, SkeletonFrameBatch


class SkeletonProtocolTests(unittest.TestCase):
    def test_skeleton_frame_accepts_feature_and_state_fields(self) -> None:
        frame = SkeletonFrame(
            camera_id="raspi_cam01",
            room_id="living_room",
            frame_id="raspi_cam01-1",
            timestamp_ms=1,
            capture_ts="2026-05-24T00:00:00.000Z",
            track_id=1,
            bbox=BoundingBox(x1=0, y1=0, x2=100, y2=200),
            bbox_confidence=0.9,
            keypoints=[Keypoint(x=1.0, y=2.0, confidence=0.8) for _ in range(17)],
            pose_confidence_mean=0.8,
            features={"center_velocity_px_s": 12.5, "motion_energy": 2.0},
            risk={"raw_score": 0.2, "ema_score": 0.1},
            event_state="NORMAL",
        )
        batch = SkeletonFrameBatch(frames=[frame])

        dumped = batch.model_dump(mode="json")

        self.assertEqual(dumped["frames"][0]["features"]["center_velocity_px_s"], 12.5)
        self.assertEqual(dumped["frames"][0]["event_state"], "NORMAL")


if __name__ == "__main__":
    unittest.main()
