from __future__ import annotations

import unittest

from shared.protocol import BoundingBox, Keypoint, SkeletonFrame
from server.services.sequence_buffer import SkeletonSequenceBuffer


def _frame(ts_ms: int) -> SkeletonFrame:
    return SkeletonFrame(
        camera_id="raspi_cam01",
        frame_id=f"f-{ts_ms}",
        timestamp_ms=ts_ms,
        capture_ts="2026-05-24T00:00:00.000Z",
        track_id=1,
        bbox=BoundingBox(x1=0, y1=0, x2=100, y2=200),
        bbox_confidence=0.9,
        keypoints=[Keypoint(x=float(i), y=float(i + 1), confidence=0.8) for i in range(17)],
        pose_confidence_mean=0.8,
        features={},
    )


class SequenceBufferTests(unittest.TestCase):
    def test_returns_recent_five_second_window(self) -> None:
        buffer = SkeletonSequenceBuffer(window_ms=5000, min_frames=3)
        for ts in [0, 1000, 3000, 6000]:
            buffer.add(_frame(ts))

        window = buffer.latest_window("raspi_cam01", 1)

        self.assertTrue(window.ready)
        self.assertEqual([frame.timestamp_ms for frame in window.frames], [1000, 3000, 6000])


if __name__ == "__main__":
    unittest.main()
