from __future__ import annotations

import unittest

from edge.main import _build_perf_stats_row


class ResolutionContractTests(unittest.TestCase):
    def test_high_res_stream_candidate_keeps_processing_resolution_explicit(self) -> None:
        row = _build_perf_stats_row(
            camera_id="raspi_cam01",
            started_at=1.0,
            ended_at=2.0,
            frame_count=30,
            pose_confidence_sum=1.0,
            pose_confidence_count=1,
            candidate_count=0,
            source_resolution=[640, 360],
            stream_resolution=[1280, 720],
            processing_resolution=[640, 360],
            model_imgsz=320,
        )

        self.assertEqual(row["source_resolution"], [640, 360])
        self.assertEqual(row["stream_resolution"], [1280, 720])
        self.assertEqual(row["processing_resolution"], [640, 360])
        self.assertEqual(row["model_imgsz"], 320)


if __name__ == "__main__":
    unittest.main()
