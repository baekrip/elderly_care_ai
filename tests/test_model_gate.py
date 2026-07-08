from __future__ import annotations

import unittest

from tools.check_model_gate import check_production_gate


class ModelGateTests(unittest.TestCase):
    def test_model_gate_accepts_report_that_meets_thresholds(self) -> None:
        report = {
            "class_metrics": {
                "DANGER": {"recall": 0.91},
                "LYING_BED": {"f1": 0.82},
                "LYING_FLOOR": {"f1": 0.81},
                "NEAR_FALL_STUMBLE": {"precision": 0.72},
            },
            "normal_adl": {"fp_per_hour": 1.5},
        }

        result = check_production_gate(report)

        self.assertTrue(result.ok)
        self.assertEqual(result.failures, [])

    def test_model_gate_rejects_danger_recall_below_threshold(self) -> None:
        report = {
            "class_metrics": {
                "DANGER": {"recall": 0.89},
                "LYING_BED": {"f1": 0.82},
                "LYING_FLOOR": {"f1": 0.81},
                "NEAR_FALL_STUMBLE": {"precision": 0.72},
            },
            "normal_adl": {"fp_per_hour": 1.5},
        }

        result = check_production_gate(report)

        self.assertFalse(result.ok)
        self.assertIn("DANGER recall 0.8900 < 0.9000", result.failures)


if __name__ == "__main__":
    unittest.main()
