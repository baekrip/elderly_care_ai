from __future__ import annotations

import unittest

from tools.overlay_ws_probe import summarize_overlay_frames, validate_overlay_frame


class OverlayWsProbeTests(unittest.TestCase):
    def test_validate_overlay_frame_requires_plan_fields(self) -> None:
        frame = {
            "schema_version": "2.1.0",
            "camera_id": "raspi_cam01",
            "frame_id": 1,
            "capture_ts": 1_748_736_000_000,
            "analysis_ts": 1_748_736_000_180,
            "source_width": 640,
            "source_height": 360,
            "tracks": [],
        }

        self.assertEqual(validate_overlay_frame(frame), [])

        invalid = dict(frame)
        invalid.pop("analysis_ts")
        self.assertIn("missing:analysis_ts", validate_overlay_frame(invalid))

    def test_summary_reports_count_latency_and_schema_errors(self) -> None:
        frames = [
            {
                "schema_version": "2.1.0",
                "camera_id": "raspi_cam01",
                "frame_id": 1,
                "capture_ts": 1000,
                "analysis_ts": 1180,
                "source_width": 640,
                "source_height": 360,
                "tracks": [],
            },
            {"camera_id": "raspi_cam01"},
        ]

        summary = summarize_overlay_frames(frames, duration_sec=10.0)

        self.assertEqual(summary["frame_count"], 2)
        self.assertEqual(summary["avg_latency_ms"], 180.0)
        self.assertEqual(summary["valid_frame_count"], 1)
        self.assertGreater(summary["schema_error_count"], 0)

    def test_summary_accepts_iso_timestamps_from_overlay_stream(self) -> None:
        frames = [
            {
                "schema_version": "2.1.0",
                "camera_id": "raspi_cam01",
                "frame_id": 1,
                "capture_ts": "2026-06-12T00:00:00.000Z",
                "analysis_ts": "2026-06-12T00:00:00.180Z",
                "source_width": 640,
                "source_height": 360,
                "tracks": [],
            },
        ]

        summary = summarize_overlay_frames(frames, duration_sec=1.0)

        self.assertEqual(validate_overlay_frame(frames[0]), [])
        self.assertEqual(summary["avg_latency_ms"], 180.0)
        self.assertEqual(summary["schema_error_count"], 0)

    def test_validate_overlay_frame_rejects_unparseable_timestamp(self) -> None:
        frame = {
            "schema_version": "2.1.0",
            "camera_id": "raspi_cam01",
            "frame_id": 1,
            "capture_ts": "not-a-timestamp",
            "analysis_ts": "2026-06-12T00:00:00.180Z",
            "source_width": 640,
            "source_height": 360,
            "tracks": [],
        }

        self.assertIn("invalid_timestamp:capture_ts", validate_overlay_frame(frame))


if __name__ == "__main__":
    unittest.main()
