from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.run_internal_overlay_viewer import build_parser, build_viewer_html


ROOT = Path(__file__).resolve().parents[1]


class InternalOverlayViewerTests(unittest.TestCase):
    def test_parser_defaults_patient_id_to_p001(self) -> None:
        args = build_parser().parse_args(
            ["--video", "missing.mp4", "--report-out", "report.json", "--html-out", "index.html"],
        )

        self.assertEqual(args.patient_id, "P001")

    def test_viewer_html_contains_internal_only_overlay_contract(self) -> None:
        frame = {
            "frame_id": 1,
            "image": "frames/frame_000001.jpg",
            "source_width": 640,
            "source_height": 360,
            "tracks": [
                {
                    "track_id": "1",
                    "bbox": [10, 20, 100, 200],
                    "keypoints": [[12.0, 24.0, 0.9]],
                    "action_label": "WALKING",
                    "risk_label": "NORMAL",
                    "risk_score": 0.2,
                }
            ],
        }

        html = build_viewer_html(
            title="internal test",
            camera_id="offline_cam01",
            frames=[frame],
            report_name="report.json",
        )

        self.assertIn("YOLO + Action Internal Viewer", html)
        self.assertIn("window.OVERLAY_FRAMES", html)
        self.assertIn("action_label", html)
        self.assertIn("bbox", html)
        self.assertIn("keypoints", html)
        self.assertNotIn("13.209.", html)
        self.assertNotIn("54.180.", html)
        self.assertNotIn("/api/v1/ws", html)

    def test_cli_writes_blocked_report_and_html_when_video_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            report_path = temp_path / "viewer_report.json"
            html_path = temp_path / "index.html"

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.run_internal_overlay_viewer",
                    "--video",
                    str(temp_path / "missing.mp4"),
                    "--report-out",
                    str(report_path),
                    "--html-out",
                    str(html_path),
                    "--max-frames",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2, msg=result.stderr)
            self.assertTrue(report_path.exists())
            self.assertTrue(html_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            html = html_path.read_text(encoding="utf-8")
            self.assertEqual(report["status"], "blocked")
            self.assertIn("video not found", report["reason"])
            self.assertIn("blocked", html)

    def test_cli_exposes_internal_server_options(self) -> None:
        help_text = build_parser().format_help()

        self.assertIn("--serve", help_text)
        self.assertIn("--host", help_text)
        self.assertIn("--port", help_text)


if __name__ == "__main__":
    unittest.main()
