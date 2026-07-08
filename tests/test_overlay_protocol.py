from __future__ import annotations

import unittest

from shared.protocol import PROTOCOL_VERSION, OverlayFrame, OverlayTrack


class OverlayProtocolTests(unittest.TestCase):
    def test_overlay_frame_uses_source_pixel_coordinates_and_defaults(self) -> None:
        frame = OverlayFrame(
            camera_id="raspi_cam01",
            frame_id="f-1",
            sequence_id="seq-1",
            capture_ts=1000,
            analysis_ts=1200,
            source_width=640,
            source_height=360,
            latency_ms=200,
            fps=5.0,
            tracks=[
                OverlayTrack(
                    track_id="1",
                    bbox=[10, 20, 100, 200],
                    keypoints=[[10.0, 20.0, 0.9]],
                    action_label="WALKING",
                    risk_label="NORMAL",
                    risk_score=1,
                    event_state="NORMAL",
                )
            ],
        )

        dumped = frame.model_dump(mode="json")

        self.assertEqual(dumped["schema_version"], PROTOCOL_VERSION)
        self.assertEqual(dumped["source_width"], 640)
        self.assertEqual(dumped["source_height"], 360)
        self.assertFalse(dumped["stale"])
        self.assertEqual(dumped["drop_count"], 0)
        self.assertEqual(dumped["tracks"][0]["bbox"], [10, 20, 100, 200])
        self.assertEqual(dumped["tracks"][0]["risk_score"], 1)


if __name__ == "__main__":
    unittest.main()
