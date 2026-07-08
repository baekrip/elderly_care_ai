from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.remaining_plan_preflight import build_preflight_report


class RemainingPlanPreflightTests(unittest.TestCase):
    def test_preflight_keeps_live_work_manual_and_masks_secret_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "APP_TOKEN=test-app-token",
                        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
                        "AWS_SECRET_ACCESS_KEY=secret-value",
                        "AI2_SERVER_URL=http://54.180.121.39:5000",
                        "UPLOAD_URL=https://bucket.s3.amazonaws.com/clip.mp4?X-Amz-Signature=abcdef",
                    ]
                ),
                encoding="utf-8",
            )

            report = build_preflight_report(root)
            dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)

        required_can_run = {
            "pose_replay_gate",
            "overlay_mock_browser",
            "overlay_live_ws",
            "backend_mock",
            "backend_live_manual",
            "label_gate",
            "stability_short_validation",
            "offload_decision",
        }
        self.assertEqual("1.0", report["schema_version"])
        self.assertTrue(required_can_run.issubset(report["can_run"]))
        self.assertTrue(report["can_run"]["backend_mock"])
        self.assertTrue(report["can_run"]["overlay_mock_browser"])
        self.assertTrue(report["can_run"]["stability_short_validation"])
        self.assertFalse(report["can_run"]["backend_live_manual"])
        self.assertFalse(report["can_run"]["overlay_live_ws"])
        self.assertFalse(report["can_run"]["offload_decision"])
        self.assertTrue(report["prerequisites"]["backend_url_configured"])
        self.assertTrue(report["prerequisites"]["backend_token_configured"])
        self.assertTrue(report["prerequisites"]["backend_credentials_present"])
        self.assertIn("backend_live_send", report["manual_or_out_of_scope"])
        self.assertIn("12_24h_soak", report["manual_or_out_of_scope"])
        self.assertIn("model_training", report["manual_or_out_of_scope"])
        self.assertIn("model_replacement", report["manual_or_out_of_scope"])
        self.assertIn("orin_pose_offload_implementation", report["manual_or_out_of_scope"])
        self.assertTrue(report["secret_scan"]["masked"])
        self.assertEqual([], report["secret_scan"]["leaks_detected"])
        self.assertNotIn("test-app-token", dumped)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", dumped)
        self.assertNotIn("secret-value", dumped)
        self.assertNotIn("X-Amz-Signature", dumped)
        self.assertNotIn("54.180.121.39", dumped)

    def test_missing_prerequisites_are_blocked_with_concrete_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = build_preflight_report(Path(temp_dir))

        self.assertFalse(report["can_run"]["pose_replay_gate"])
        self.assertFalse(report["can_run"]["label_gate"])
        self.assertIn("pose_replay_gate", report["blocked_reasons"])
        self.assertIn("label_gate", report["blocked_reasons"])
        self.assertIn("replay", report["blocked_reasons"]["pose_replay_gate"])
        self.assertIn("label", report["blocked_reasons"]["label_gate"])

    def test_backend_credentials_require_url_and_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text("APP_TOKEN=token-only\n", encoding="utf-8")

            report = build_preflight_report(root)

        self.assertFalse(report["prerequisites"]["backend_url_configured"])
        self.assertTrue(report["prerequisites"]["backend_token_configured"])
        self.assertFalse(report["prerequisites"]["backend_credentials_present"])


if __name__ == "__main__":
    unittest.main()
