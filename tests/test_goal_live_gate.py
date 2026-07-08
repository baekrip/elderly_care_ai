from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.goal_live_gate import build_goal_live_gate_report


class GoalLiveGateTests(unittest.TestCase):
    def test_report_marks_all_gates_ready_without_leaking_env_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            clip = root / "danger.mp4"
            clip.write_bytes(b"mp4")
            (root / ".env").write_text(
                "\n".join(
                    [
                        "AI2_SERVER_URL=http://54.180.121.39:5000",
                        "APP_TOKEN=secret-token",
                        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE",
                        "AWS_SECRET_ACCESS_KEY=secret-value",
                        "ORIN_IP=192.168.45.241",
                        "ORIN_OVERLAY_WS_URL=ws://192.168.45.241:8000/ws/overlay/raspi_cam01",
                    ]
                ),
                encoding="utf-8",
            )

            report = build_goal_live_gate_report(root=root, clip_file=clip)
            dumped = json.dumps(report, ensure_ascii=False, sort_keys=True)

        self.assertTrue(report["all_ready"])
        self.assertTrue(report["gates"]["backend_json"]["ready"])
        self.assertTrue(report["gates"]["backend_clip"]["ready"])
        self.assertTrue(report["gates"]["overlay_ws"]["ready"])
        self.assertIn("tools/test_backend_batch.py", report["gates"]["backend_json"]["command_template"])
        self.assertIn("tools/test_backend_clip_smoke.py", report["gates"]["backend_clip"]["command_template"])
        self.assertIn("tools/overlay_ws_probe.py", report["gates"]["overlay_ws"]["command_template"])
        self.assertNotIn("54.180.121.39", dumped)
        self.assertNotIn("secret-token", dumped)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", dumped)
        self.assertNotIn("secret-value", dumped)
        self.assertNotIn("192.168.45.241", dumped)

    def test_report_blocks_missing_live_prerequisites(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = build_goal_live_gate_report(root=Path(tmpdir))

        self.assertFalse(report["all_ready"])
        self.assertFalse(report["gates"]["backend_json"]["ready"])
        self.assertFalse(report["gates"]["backend_clip"]["ready"])
        self.assertFalse(report["gates"]["overlay_ws"]["ready"])
        self.assertIn("backend_json", report["blocked_gates"])
        self.assertIn("backend_clip", report["blocked_gates"])
        self.assertIn("overlay_ws", report["blocked_gates"])


if __name__ == "__main__":
    unittest.main()
