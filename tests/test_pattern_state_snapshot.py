from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from server.services.pattern_analyzer import PatternAnalyzer


class PatternStateSnapshotTests(unittest.TestCase):
    def test_snapshot_round_trip_restores_daily_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "pattern_state_snapshot.json"
            analyzer = PatternAnalyzer({"inactivity_threshold_sec": 999})
            analyzer.analyze_window(
                "P001",
                [{"action_label": "WALKING", "duration_ms": 60_000}],
                1_714_838_100_000,
                1_714_838_160_000,
            )

            analyzer.save_snapshot(snapshot_path, camera_id="raspi_cam01", window_start_ts=1, window_end_ts=2)
            restored = PatternAnalyzer({"inactivity_threshold_sec": 999})
            result = restored.restore_snapshot(snapshot_path, now_ts_ms=2, max_gap_sec=300)

            self.assertEqual(result["status"], "restored")
            self.assertEqual(restored.snapshot_state(), analyzer.snapshot_state())

    def test_broken_snapshot_is_ignored_without_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "pattern_state_snapshot.json"
            snapshot_path.write_text("{broken", encoding="utf-8")

            analyzer = PatternAnalyzer()
            with self.assertLogs("server.services.pattern_analyzer", level="WARNING"):
                result = analyzer.restore_snapshot(snapshot_path, now_ts_ms=2, max_gap_sec=300)

            self.assertEqual(result["status"], "invalid_json")
            self.assertEqual(analyzer.snapshot_state()["daily_patterns"], {})

    def test_missing_snapshot_starts_empty_without_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "missing_snapshot.json"
            analyzer = PatternAnalyzer()

            result = analyzer.restore_snapshot(snapshot_path, now_ts_ms=2, max_gap_sec=300)

            self.assertEqual(result["status"], "missing")
            self.assertEqual(analyzer.snapshot_state()["daily_patterns"], {})

    def test_snapshot_schema_mismatch_is_not_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "pattern_state_snapshot.json"
            snapshot_path.write_text(
                json.dumps({"schema_version": "0.1", "state": {"daily_patterns": {"P001_h1": []}}}),
                encoding="utf-8",
            )

            analyzer = PatternAnalyzer()
            result = analyzer.restore_snapshot(snapshot_path, now_ts_ms=2, max_gap_sec=300)

            self.assertEqual(result["status"], "schema_mismatch")
            self.assertEqual(analyzer.snapshot_state()["daily_patterns"], {})

    def test_snapshot_gap_over_threshold_returns_partial_restore_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "pattern_state_snapshot.json"
            analyzer = PatternAnalyzer({"inactivity_threshold_sec": 999})
            analyzer.analyze_window(
                "patient-1",
                [{"action_label": "WALKING", "duration_ms": 60_000}],
                1_714_838_100_000,
                1_714_838_160_000,
            )
            analyzer.save_snapshot(snapshot_path, camera_id="raspi_cam01", window_start_ts=1_000, window_end_ts=2_000)
            restored = PatternAnalyzer()

            result = restored.restore_snapshot(snapshot_path, now_ts_ms=303_001, max_gap_sec=300)

            self.assertEqual(result["status"], "partial_restored_gap")
            self.assertEqual(restored.snapshot_state(), analyzer.snapshot_state())

    def test_snapshot_write_uses_tmp_then_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "pattern_state_snapshot.json"
            analyzer = PatternAnalyzer()
            analyzer.save_snapshot(snapshot_path, camera_id="raspi_cam01", window_start_ts=1, window_end_ts=2)

            self.assertTrue(snapshot_path.exists())
            self.assertFalse(snapshot_path.with_suffix(snapshot_path.suffix + ".tmp").exists())

    def test_snapshot_size_limit_prunes_oldest_pattern_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_path = Path(tmpdir) / "pattern_state_snapshot.json"
            analyzer = PatternAnalyzer()
            analyzer.restore_state(
                {
                    "daily_patterns": {
                        "P001_h1": [{"WALKING": 1.0, "SITTING": 0.0} for _ in range(200)],
                    }
                }
            )

            analyzer.save_snapshot(
                snapshot_path,
                camera_id="raspi_cam01",
                window_start_ts=1,
                window_end_ts=2,
                max_snapshot_size_kb=2,
            )
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

            self.assertLessEqual(snapshot_path.stat().st_size, 2 * 1024)
            self.assertLess(len(payload["state"]["daily_patterns"]["P001_h1"]), 200)


if __name__ == "__main__":
    unittest.main()
