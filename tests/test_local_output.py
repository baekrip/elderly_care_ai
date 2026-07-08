from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from edge.local_output import LocalOutputWriter


class LocalOutputWriterTests(unittest.TestCase):
    def test_write_trigger_debug_records_trigger_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "local_output": {
                    "enabled": True,
                    "save_dir": tmpdir,
                    "trigger_debug_file": "trigger_debug.jsonl",
                }
            }
            writer = LocalOutputWriter(config)

            writer.write_trigger_debug(
                [
                    {
                        "camera_id": "cam01",
                        "track_id": 1,
                        "timestamp_ms": 123,
                        "trigger_flags": ["knee_angle_collapse"],
                    }
                ]
            )

            rows = (Path(tmpdir) / "trigger_debug.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])["trigger_flags"], ["knee_angle_collapse"])

    def test_write_perf_stats_records_runtime_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "local_output": {
                    "enabled": True,
                    "save_dir": tmpdir,
                    "perf_stats_file": "perf_stats.jsonl",
                }
            }
            writer = LocalOutputWriter(config)

            writer.write_perf_stats(
                [
                    {
                        "camera_id": "cam01",
                        "window_duration_sec": 30.0,
                        "avg_fps": 7.5,
                        "avg_pose_confidence": 0.82,
                        "candidate_rate": 0.1,
                    }
                ]
            )

            rows = (Path(tmpdir) / "perf_stats.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            row = json.loads(rows[0])
            self.assertEqual(row["avg_fps"], 7.5)
            self.assertEqual(row["avg_pose_confidence"], 0.82)
            self.assertEqual(row["candidate_rate"], 0.1)

    def test_daily_rotation_writes_under_date_directory_and_prunes_old_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            today = date.today().isoformat()
            old_day = (date.today() - timedelta(days=8)).isoformat()
            old_dir = Path(tmpdir) / "daily" / old_day
            old_dir.mkdir(parents=True)
            old_file = old_dir / "activity_frames.jsonl"
            old_file.write_text('{"old": true}\n', encoding="utf-8")

            writer = LocalOutputWriter(
                {
                    "local_output": {
                        "enabled": True,
                        "save_dir": tmpdir,
                        "frames_file": "activity_frames.jsonl",
                        "rotation": {
                            "enabled": True,
                            "retention_days": 7,
                        },
                    }
                }
            )

            writer.write_frames([{"camera_id": "cam01", "frame_id": "f1"}])

            rotated_file = Path(tmpdir) / "daily" / today / "activity_frames.jsonl"
            self.assertTrue(rotated_file.exists())
            self.assertEqual(json.loads(rotated_file.read_text(encoding="utf-8").splitlines()[0])["frame_id"], "f1")
            self.assertFalse(old_file.exists())


if __name__ == "__main__":
    unittest.main()
