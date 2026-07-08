from __future__ import annotations

import unittest

from server.services.risk_smoothing import RiskSmoother, coerce_risk_score, to_risk_score


class RiskSmoothingTests(unittest.TestCase):
    def test_normalized_confidence_maps_to_integer_risk_score(self) -> None:
        cases = [
            (0.0, 1),
            (0.2, 1),
            (0.2001, 2),
            (0.4, 2),
            (0.8, 4),
            (1.0, 5),
        ]

        for confidence, expected in cases:
            with self.subTest(confidence=confidence):
                self.assertEqual(to_risk_score(confidence), expected)

    def test_non_finite_confidence_is_rejected(self) -> None:
        for confidence in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValueError):
                    to_risk_score(confidence)

    def test_external_risk_score_accepts_integer_or_normalized_legacy_float(self) -> None:
        self.assertEqual(coerce_risk_score(5), 5)
        self.assertEqual(coerce_risk_score(0.73), 4)

        for invalid in (0, 6, -0.1, 1.1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    coerce_risk_score(invalid)

    def test_single_spike_does_not_become_dangerous(self) -> None:
        smoother = RiskSmoother(alpha=0.3, suspicious_threshold=0.45, danger_threshold=0.70, vote_window=10)

        result = smoother.update("cam01:1", 0.95)

        self.assertLess(result.ema_risk, 0.70)
        self.assertEqual(result.level, "normal")

    def test_sustained_risk_becomes_dangerous(self) -> None:
        smoother = RiskSmoother(alpha=0.3, suspicious_threshold=0.45, danger_threshold=0.70, vote_window=10)
        result = None
        for _ in range(8):
            result = smoother.update("cam01:1", 0.9)

        self.assertIsNotNone(result)
        self.assertEqual(result.level, "danger")
        self.assertGreaterEqual(result.ema_risk, 0.70)


if __name__ == "__main__":
    unittest.main()
