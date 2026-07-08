from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from edge.video_buffer import RollingVideoBuffer


class RollingVideoBufferTests(unittest.TestCase):
    def test_export_clip_does_not_close_current_segment_with_past_center_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            buffer = RollingVideoBuffer(
                save_dir=tmpdir,
                fps=2,
                resolution=(32, 24),
                segment_duration_sec=10,
                max_segments=5,
                codec="mp4v",
            )
            frame = np.zeros((24, 32, 3), dtype=np.uint8)
            buffer.write(frame, 10_000)
            buffer.write(frame, 10_500)

            clip = buffer.export_clip("past-request", center_ts_ms=1_000, pre_ms=100, post_ms=100)

            self.assertIsNone(clip)
            index_path = Path(tmpdir) / "segments_index.jsonl"
            rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[-1]["started_at_ms"], 10_000)
            self.assertEqual(rows[-1]["ended_at_ms"], 10_500)
            self.assertGreaterEqual(rows[-1]["ended_at_ms"], rows[-1]["started_at_ms"])

    def test_write_enabled_false_skips_file_creation(self) -> None:
        """OPT-POSE-2: write_enabled=False 시 mp4 세그먼트 파일이 생성되지 않아야 한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            buffer = RollingVideoBuffer(
                save_dir=tmpdir,
                fps=2,
                resolution=(32, 24),
                segment_duration_sec=10,
                max_segments=5,
                codec="mp4v",
                write_enabled=False,
            )
            frame = np.zeros((24, 32, 3), dtype=np.uint8)
            for ts in [1_000, 1_500, 2_000, 2_500]:
                buffer.write(frame, ts)

            # 파일 쓰기가 비활성화됐으므로 mp4 세그먼트 파일이 없어야 한다
            mp4_files = list(Path(tmpdir).glob("segment_*.mp4"))
            self.assertEqual(len(mp4_files), 0, f"write_enabled=False 인데 세그먼트 파일 생성됨: {mp4_files}")
            # timestamp는 정상 추적되어야 한다
            self.assertEqual(buffer._last_timestamp_ms, 2_500)

    def test_write_enabled_true_creates_segment_file(self) -> None:
        """write_enabled=True(기본값) 시 기존처럼 세그먼트 파일이 생성되어야 한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            buffer = RollingVideoBuffer(
                save_dir=tmpdir,
                fps=2,
                resolution=(32, 24),
                segment_duration_sec=10,
                max_segments=5,
                codec="mp4v",
                write_enabled=True,
            )
            frame = np.zeros((24, 32, 3), dtype=np.uint8)
            for ts in [1_000, 1_500, 2_000, 2_500]:
                buffer.write(frame, ts)

            mp4_files = list(Path(tmpdir).glob("segment_*.mp4"))
            self.assertGreater(len(mp4_files), 0, "write_enabled=True 인데 세그먼트 파일이 없음")
            # Windows 파일 잠금 해제 (cv2.VideoWriter 명시 release)
            if buffer._writer is not None:
                buffer._writer.release()
                buffer._writer = None


if __name__ == "__main__":
    unittest.main()
