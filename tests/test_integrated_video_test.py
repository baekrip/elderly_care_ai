from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.run_integrated_video_test import build_blocked_report, build_parser, prepare_replay_config


ROOT = Path(__file__).resolve().parents[1]


class IntegratedVideoTestTests(unittest.TestCase):
    def test_parser_defaults_patient_id_to_p001(self) -> None:
        args = build_parser().parse_args(["--video", "missing.mp4", "--report-out", "report.json"])

        self.assertEqual(args.patient_id, "P001")

    def test_blocked_report_names_pipeline_and_missing_video(self) -> None:
        report = build_blocked_report(
            video_path=Path("missing.mp4"),
            config_path=Path("edge/config.yaml"),
            label_path=None,
            reason="video not found: missing.mp4",
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(
            report["pipeline"],
            ["YOLO26s-pose", "XGBoost action", "XGBoost fall", "TriggerEngine", "ST-GCN"],
        )
        self.assertEqual(report["required_fields"][0], "capture_ts")
        self.assertIn("video not found", report["reason"])

    def test_cli_writes_blocked_report_when_video_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "integrated_report.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.run_integrated_video_test",
                    "--video",
                    str(Path(temp_dir) / "missing.mp4"),
                    "--report-out",
                    str(report_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, msg=result.stderr)
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "blocked")
            self.assertIn("video not found", report["reason"])

    def test_prepare_replay_config_preserves_model_backend(self) -> None:
        config = {
            "camera": {"source_mode": "device", "source": 0},
            "server": {"enabled": True},
            "model": {"backend": "auto", "model_path": "edge/models/yolo26s-pose.pt"},
        }

        replay_config = prepare_replay_config(config, video_path=Path("sample.mp4"))

        self.assertEqual(replay_config["camera"]["source_mode"], "file")
        self.assertEqual(replay_config["camera"]["source"], "sample.mp4")
        self.assertFalse(replay_config["server"]["enabled"])
        self.assertEqual(replay_config["model"]["backend"], "auto")


if __name__ == "__main__":
    unittest.main()
