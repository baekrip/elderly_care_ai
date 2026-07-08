from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from server.api.activity import _analyze_timeline_patterns
from server.result_archive import ResultArchive
from server.services.pattern_analyzer import PatternAnalyzer
from shared.protocol import TimelineBatch, TimelineSegment


class PatternAnalyzerIntegrationTests(unittest.TestCase):
    def test_timeline_batch_writes_pattern_analysis_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = ResultArchive(
                {
                    "local_archive": {
                        "enabled": True,
                        "save_dir": tmpdir,
                        "pattern_results_file": "pattern_results.jsonl",
                    },
                    "patient": {"patient_id": "P001"},
                }
            )
            request = SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        pattern_analyzer=PatternAnalyzer({"inactivity_threshold_sec": 60}),
                        result_archive=archive,
                        config={"patient": {"patient_id": "P001"}},
                    )
                )
            )
            payload = TimelineBatch(
                segments=[
                    TimelineSegment(
                        camera_id="raspi_cam01",
                        track_id=1,
                        action_label="LYING",
                        action_confidence=0.8,
                        started_at_ms=1_000,
                        ended_at_ms=181_000,
                        duration_ms=180_000,
                    )
                ]
            )

            anomaly_count = _analyze_timeline_patterns(payload, request)

            self.assertEqual(anomaly_count, 1)
            pattern_file = Path(tmpdir) / "pattern_results.jsonl"
            self.assertTrue(pattern_file.exists())

    def test_timeline_pattern_result_is_submitted_to_normal_batcher(self) -> None:
        class _Batcher:
            def __init__(self) -> None:
                self.calls: list[tuple[dict, bool]] = []

            def submit(self, event: dict, *, force: bool = False) -> object:
                self.calls.append((event, force))
                return SimpleNamespace(response=None, queued=len(self.calls), forwarded=0)

        batcher = _Batcher()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    pattern_analyzer=PatternAnalyzer(
                        {"inactivity_threshold_sec": 999, "night_start_hour": 25, "night_end_hour": -1}
                    ),
                    result_archive=ResultArchive({"local_archive": {"enabled": False}}),
                    backend_normal_batcher=batcher,
                    config={
                        "patient": {"patient_id": "P001"},
                        "backend": {
                            "enabled": True,
                            "base_url": "http://backend.example:5000",
                            "device_key": "orin-edge",
                            "token": "dummy_token",
                        },
                    },
                )
            )
        )
        payload = TimelineBatch(
            segments=[
                TimelineSegment(
                    camera_id="raspi_cam01",
                    track_id=1,
                    action_label="WALKING",
                    action_confidence=0.8,
                    started_at_ms=1_714_838_100_000,
                    ended_at_ms=1_714_838_400_000,
                    duration_ms=300_000,
                )
            ]
        )

        anomaly_count = _analyze_timeline_patterns(payload, request)

        self.assertEqual(anomaly_count, 0)
        self.assertEqual(len(batcher.calls), 1)
        event, force = batcher.calls[0]
        self.assertFalse(force)
        self.assertEqual(event["event_type"], "normal_activity_summary")
        self.assertEqual(event["device_key"], "orin-edge")

    def test_timeline_pattern_anomaly_forces_batcher_flush(self) -> None:
        class _Batcher:
            def __init__(self) -> None:
                self.calls: list[tuple[dict, bool]] = []

            def submit(self, event: dict, *, force: bool = False) -> object:
                self.calls.append((event, force))
                return SimpleNamespace(response=None, queued=len(self.calls), forwarded=0)

        batcher = _Batcher()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    pattern_analyzer=PatternAnalyzer({"inactivity_threshold_sec": 60}),
                    result_archive=ResultArchive({"local_archive": {"enabled": False}}),
                    backend_normal_batcher=batcher,
                    config={
                        "patient": {"patient_id": "P001"},
                        "backend": {"enabled": True, "base_url": "http://backend.example:5000", "token": "dummy_token"},
                    },
                )
            )
        )
        payload = TimelineBatch(
            segments=[
                TimelineSegment(
                    camera_id="raspi_cam01",
                    track_id=1,
                    action_label="LYING",
                    action_confidence=0.8,
                    started_at_ms=1_714_838_100_000,
                    ended_at_ms=1_714_838_400_000,
                    duration_ms=300_000,
                )
            ]
        )

        anomaly_count = _analyze_timeline_patterns(payload, request)

        self.assertEqual(anomaly_count, 1)
        event, force = batcher.calls[0]
        self.assertTrue(force)
        self.assertEqual(event["event_type"], "pattern_anomaly_detected")


if __name__ == "__main__":
    unittest.main()
