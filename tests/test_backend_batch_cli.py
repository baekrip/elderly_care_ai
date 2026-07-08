from __future__ import annotations

import subprocess
import sys
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BackendBatchCliTests(unittest.TestCase):
    def test_dry_run_prints_batch_payload_without_network(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.test_backend_batch",
                "--dry-run",
                "--base-url",
                "http://13.209.89.107:5000",
                "--token",
                "test-app-token",
                "--device-key",
                "cam_livingroom_01",
                "--patient-id",
                "P001",
                "--event-type",
                "fall_detected",
                "--danger-alert",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertIn("http://13.209.89.107:5000/api/v1/events/batch", result.stdout)
        self.assertIn('"events"', result.stdout)
        self.assertIn('"alert_type": "fall_detected"', result.stdout)

    def test_dry_run_loads_ai2_server_url_from_dotenv_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workdir = Path(tmpdir)
            (workdir / ".env").write_text(
                "\n".join(
                    [
                        "AI2_SERVER_URL=http://ai2-backend.example:5000",
                        "APP_TOKEN=test-app-token",
                    ]
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.test_backend_batch",
                    "--dry-run",
                    "--event-type",
                    "fall_detected",
                ],
                cwd=workdir,
                env={**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "device_transfer" / "Edge")])},
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertIn("http://ai2-backend.example:5000/api/v1/events/batch", result.stdout)


if __name__ == "__main__":
    unittest.main()
