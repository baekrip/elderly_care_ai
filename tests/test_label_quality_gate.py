from __future__ import annotations

import unittest

from tools.validate_label_quality import build_label_quality_report, validate_label_record


class LabelQualityGateTests(unittest.TestCase):
    def test_validate_label_record_requires_common_schema(self) -> None:
        record = {
            "camera_id": "raspi_cam01",
            "track_id": "track-1",
            "start_ts_ms": 1000,
            "end_ts_ms": 2000,
            "label": "LYING_BED",
            "source": "manual",
            "confidence": 0.9,
            "notes": "",
        }

        self.assertEqual(validate_label_record(record), [])

        invalid = dict(record)
        invalid["end_ts_ms"] = 900
        invalid.pop("source")
        errors = validate_label_record(invalid)

        self.assertIn("missing:source", errors)
        self.assertIn("end_before_start", errors)

    def test_quality_report_flags_overlap_low_confidence_and_near_fall_count(self) -> None:
        records = [
            {
                "camera_id": "raspi_cam01",
                "track_id": "track-1",
                "start_ts_ms": 1000,
                "end_ts_ms": 2000,
                "label": "LYING_BED",
                "source": "manual",
                "confidence": 0.5,
                "notes": "",
            },
            {
                "camera_id": "raspi_cam01",
                "track_id": "track-1",
                "start_ts_ms": 1500,
                "end_ts_ms": 2500,
                "label": "LYING_FLOOR",
                "source": "manual",
                "confidence": 0.7,
                "notes": "",
            },
            {
                "camera_id": "raspi_cam01",
                "track_id": "track-2",
                "start_ts_ms": 3000,
                "end_ts_ms": 4000,
                "label": "NEAR_FALL_STUMBLE",
                "source": "manual",
                "confidence": 0.9,
                "notes": "",
            },
        ]

        report = build_label_quality_report(records, min_near_fall_windows=50)

        self.assertFalse(report["passed"])
        self.assertEqual(report["label_counts"]["NEAR_FALL_STUMBLE"], 1)
        self.assertIn("near_fall_stumble_count_below_minimum", report["warnings"])
        self.assertIn("lying_bed_floor_overlap", report["errors"])
        self.assertGreater(report["low_confidence_ratio"], 0.2)

    def test_missing_label_inputs_are_expected_fail_and_never_training_allowed(self) -> None:
        report = build_label_quality_report(
            [],
            missing_inputs=("action_state_labels.jsonl", "risk_event_labels.jsonl"),
        )

        self.assertFalse(report["passed"])
        self.assertFalse(report["training_allowed"])
        self.assertTrue(report["expected_fail"])
        self.assertIn("missing_label_inputs", report["errors"])
        self.assertEqual(report["missing_inputs"], ["action_state_labels.jsonl", "risk_event_labels.jsonl"])


if __name__ == "__main__":
    unittest.main()
