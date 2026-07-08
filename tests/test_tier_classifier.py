from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from edge.tier_classifier import TierClassifier


class TierClassifierTests(unittest.TestCase):
    def test_missing_model_disables_classifier_with_reason_and_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_model = Path(tmpdir) / "missing.json"
            config = {
                "tier_classification": {
                    "enabled": True,
                    "model_path": str(missing_model),
                    "meta_path": str(Path(tmpdir) / "missing_meta.json"),
                }
            }

            with self.assertLogs("edge.tier_classifier", level="WARNING") as logs:
                classifier = TierClassifier(config)

            self.assertFalse(classifier.enabled)
            self.assertEqual(classifier.disabled_reason, f"model not found: {missing_model}")
            self.assertIn("XGBoost disabled", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
