from __future__ import annotations

import argparse
import unittest
from pathlib import Path

from edge.main import _build_perf_stats_row, apply_cli_overrides

ROOT = Path(__file__).resolve().parents[1]


class EdgeRuntimeMetricsTests(unittest.TestCase):
    def test_build_perf_stats_row_uses_window_counts(self) -> None:
        row = _build_perf_stats_row(
            camera_id="cam01",
            started_at=100.0,
            ended_at=130.0,
            frame_count=150,
            pose_confidence_sum=120.0,
            pose_confidence_count=150,
            candidate_count=3,
            target_fps=30.0,
            inference_frame_count=50,
            pose_latency_ms=[8.0, 10.0, 12.0, 40.0],
            loop_latency_ms=[20.0, 30.0, 50.0],
            frame_interval_ms=[90.0, 100.0, 150.0],
        )

        self.assertEqual(row["camera_id"], "cam01")
        self.assertEqual(row["window_duration_sec"], 30.0)
        self.assertEqual(row["frame_count"], 150)
        self.assertEqual(row["avg_fps"], 5.0)
        self.assertEqual(row["avg_pose_confidence"], 0.8)
        self.assertEqual(row["candidate_count"], 3)
        self.assertEqual(row["candidate_rate"], 0.02)
        self.assertEqual(row["target_fps"], 30.0)
        self.assertEqual(row["dropped_frame_estimate"], 750)
        self.assertEqual(row["drop_rate_estimate"], 0.8333)
        self.assertEqual(row["inference_frame_count"], 50)
        self.assertEqual(row["inference_fps"], 1.6667)
        self.assertEqual(row["pose_inference_avg_ms"], 17.5)
        self.assertEqual(row["pose_inference_p95_ms"], 40.0)
        self.assertEqual(row["loop_avg_ms"], 33.3333)
        self.assertEqual(row["loop_p95_ms"], 50.0)
        self.assertEqual(row["frame_interval_p95_ms"], 150.0)

    def test_perf_stats_row_includes_stage_timings_and_resolution_contract(self) -> None:
        row = _build_perf_stats_row(
            camera_id="cam01",
            started_at=100.0,
            ended_at=110.0,
            frame_count=30,
            pose_confidence_sum=20.0,
            pose_confidence_count=30,
            candidate_count=1,
            stage_metrics_ms={
                "capture_ms": [1.0, 2.0, 3.0],
                "buffer_write_ms": [0.5, 1.0],
                "stream_write_ms": [2.0, 4.0],
                "processing_resize_ms": [3.0],
                "preprocess_ms": [4.0, 6.0],
                "onnx_session_ms": [20.0, 30.0],
                "decode_ms": [1.0, 2.0],
                "pose_total_ms": [25.0, 40.0],
            },
            worker_busy_skip_count=2,
            worker_submit_count=5,
            worker_result_count=4,
            source_resolution=[640, 360],
            stream_resolution=[1280, 720],
            processing_resolution=[640, 360],
            model_imgsz=320,
        )

        self.assertEqual(row["capture_ms"], 2.0)
        self.assertEqual(row["capture_p95_ms"], 3.0)
        self.assertEqual(row["onnx_session_ms"], 25.0)
        self.assertEqual(row["onnx_session_p95_ms"], 30.0)
        self.assertEqual(row["pose_total_ms"], 32.5)
        self.assertEqual(row["worker_busy_skip_count"], 2)
        self.assertEqual(row["worker_submit_count"], 5)
        self.assertEqual(row["worker_result_count"], 4)
        self.assertEqual(row["source_resolution"], [640, 360])
        self.assertEqual(row["stream_resolution"], [1280, 720])
        self.assertEqual(row["processing_resolution"], [640, 360])
        self.assertEqual(row["model_imgsz"], 320)

    def test_cli_can_override_camera_backend_for_file_replay(self) -> None:
        config = {
            "camera": {"backend": "picamera2", "source_mode": "device", "source": 0},
            "server": {"enabled": True},
        }
        args = argparse.Namespace(
            source_mode="file",
            source="~/elderly_care_ai/test.mp4",
            camera_backend="opencv",
            disable_server=False,
        )

        updated = apply_cli_overrides(config, args)

        self.assertEqual(updated["camera"]["backend"], "opencv")
        self.assertEqual(updated["camera"]["source_mode"], "file")
        self.assertEqual(updated["camera"]["source"], "~/elderly_care_ai/test.mp4")

    def test_stream_frame_is_written_after_latest_pose_overlay_is_updated(self) -> None:
        source = (ROOT / "device_transfer" / "camera1" / "edge" / "main.py").read_text(encoding="utf-8")

        overlay_update_index = source.index("latest_stream_overlays = processed_people")
        stream_write_index = source.find("streamer.write_frame", overlay_update_index)

        self.assertGreater(
            stream_write_index,
            overlay_update_index,
            "RTSP frame must be written after pose_result updates latest_stream_overlays, "
            "otherwise live bbox/keypoint overlays can miss the frame that just received YOLO results.",
        )


if __name__ == "__main__":
    unittest.main()
