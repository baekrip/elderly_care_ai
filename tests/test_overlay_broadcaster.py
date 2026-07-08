from __future__ import annotations

import unittest

from server.services.overlay_broadcaster import OverlayBroadcaster
from shared.protocol import BoundingBox, Keypoint, SkeletonFrame


class OverlayBroadcasterTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_subscriber_receives_latest_frame_only(self) -> None:
        broadcaster = OverlayBroadcaster()
        queue = broadcaster.subscribe("raspi_cam01")

        broadcaster.publish({"camera_id": "raspi_cam01", "frame_id": "old"})
        broadcaster.publish({"camera_id": "raspi_cam01", "frame_id": "new"})

        payload = await queue.get()

        self.assertEqual(payload["frame_id"], "new")
        self.assertEqual(broadcaster.latest("raspi_cam01")["frame_id"], "new")

    async def test_publish_normalizes_legacy_float_risk_score(self) -> None:
        broadcaster = OverlayBroadcaster()
        queue = broadcaster.subscribe("raspi_cam01")

        broadcaster.publish(
            {
                "camera_id": "raspi_cam01",
                "frame_id": "f-1",
                "tracks": [{"track_id": "1", "risk_score": 0.73}],
            }
        )

        payload = await queue.get()
        self.assertEqual(payload["tracks"][0]["risk_score"], 4)
        self.assertIsInstance(payload["tracks"][0]["risk_score"], int)

    def test_overlay_frame_from_skeleton_includes_track_and_latency(self) -> None:
        skeleton = SkeletonFrame(
            camera_id="raspi_cam01",
            frame_id="f-1",
            timestamp_ms=1000,
            capture_ts="2026-06-01T00:00:00.000Z",
            analysis_ts="2026-06-01T00:00:00.200Z",
            track_id=1,
            bbox=BoundingBox(x1=10, y1=20, x2=100, y2=200),
            bbox_confidence=0.9,
            keypoints=[Keypoint(x=1.0, y=2.0, confidence=0.8)],
            pose_confidence_mean=0.8,
            features={"source_width": 640, "source_height": 360},
            risk={"ema_score": 0.72},
            event_state="DANGEROUS",
        )

        frame = OverlayBroadcaster.frame_from_skeleton(
            skeleton,
            event={"risk_label": "danger", "risk_score": 4, "state": "DANGEROUS"},
        )

        self.assertEqual(frame["camera_id"], "raspi_cam01")
        self.assertEqual(frame["tracks"][0]["track_id"], "1")
        self.assertEqual(frame["tracks"][0]["risk_label"], "DANGER")
        self.assertEqual(frame["tracks"][0]["event_state"], "DANGEROUS")
        self.assertEqual(frame["tracks"][0]["risk_score"], 4)
        self.assertEqual(frame["source_width"], 640)
        self.assertEqual(frame["source_height"], 360)

    def test_overlay_frame_from_skeleton_normalizes_zero_float_event_risk_score(self) -> None:
        skeleton = SkeletonFrame(
            camera_id="raspi_cam01",
            frame_id="f-normal",
            timestamp_ms=1000,
            track_id=1,
            bbox=BoundingBox(x1=10, y1=20, x2=100, y2=200),
            bbox_confidence=0.9,
            keypoints=[Keypoint(x=1.0, y=2.0, confidence=0.8)],
            pose_confidence_mean=0.8,
            features={"source_width": 640, "source_height": 360},
            risk={"ema_score": 0.0},
            event_state="NORMAL",
        )

        frame = OverlayBroadcaster.frame_from_skeleton(
            skeleton,
            event={"risk_label": "NORMAL", "risk_score": 0.0, "state": "NORMAL"},
        )

        self.assertEqual(frame["tracks"][0]["risk_score"], 1)
        self.assertIsInstance(frame["tracks"][0]["risk_score"], int)


if __name__ == "__main__":
    unittest.main()
