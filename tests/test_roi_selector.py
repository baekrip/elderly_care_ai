from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
import yaml

from tools.roi_selector import RoiContractError, merge_roi_config, validate_polygon


class RoiSelectorTests(unittest.TestCase):
    def test_polygon_validation_rejects_invalid_shapes(self) -> None:
        invalid_cases = (
            ([(0, 0), (1, 1)], "three unique"),
            ([(0, 0), (100, 0), (0, 100)], "bounds"),
            ([(0, 0), (9, 9), (0, 9), (9, 0)], "self-intersection"),
        )
        for points, message in invalid_cases:
            with self.subTest(points=points):
                with self.assertRaisesRegex(RoiContractError, message):
                    validate_polygon(points, width=10, height=10)

    def test_merge_preserves_unrelated_yaml_and_derives_runtime_rectangles(self) -> None:
        config = {"patient": {"patient_id": "P001"}, "stream": {"fps": 30}}
        polygons = {
            "door": [(0, 0), (10, 0), (10, 20), (0, 20)],
            "bed": [(20, 10), (80, 10), (80, 50), (20, 50)],
            "floor": [(0, 50), (99, 50), (99, 99), (0, 99)],
        }

        merged = merge_roi_config(config, polygons, width=100, height=100)

        self.assertEqual(merged["patient"], config["patient"])
        self.assertEqual(merged["stream"], config["stream"])
        self.assertEqual(merged["room_rois"]["bed"], [0.2, 0.1, 0.8, 0.5])
        self.assertEqual(merged["room_roi_polygons"]["floor"][0], [0.0, 0.5])

    def test_fixture_cli_round_trips_yaml_and_writes_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "frame.png"
            config = root / "input.yaml"
            fixture = root / "rois.json"
            output = root / "output.yaml"
            preview = root / "preview.png"
            cv2.imwrite(str(image), np.zeros((100, 100, 3), dtype=np.uint8))
            config.write_text("patient:\n  patient_id: P001\n", encoding="utf-8")
            fixture.write_text(
                json.dumps(
                    {
                        "door": [[0, 0], [20, 0], [20, 40], [0, 40]],
                        "bed": [[20, 10], [80, 10], [80, 50], [20, 50]],
                        "floor": [[0, 50], [99, 50], [99, 99], [0, 99]],
                    },
                ),
                encoding="utf-8-sig",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.roi_selector",
                    "--image",
                    str(image),
                    "--config",
                    str(config),
                    "--output-config",
                    str(output),
                    "--fixture-json",
                    str(fixture),
                    "--preview-out",
                    str(preview),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            written = yaml.safe_load(output.read_text(encoding="utf-8"))
            self.assertEqual(written["patient"]["patient_id"], "P001")
            self.assertEqual(set(written["room_rois"]), {"door", "bed", "floor"})
            self.assertTrue(preview.is_file())
            self.assertIn("saved_rois=3", result.stdout)

    def test_corrupt_yaml_and_cancelled_fixture_preserve_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "frame.png"
            cv2.imwrite(str(image), np.zeros((10, 10, 3), dtype=np.uint8))
            output = root / "output.yaml"
            output.write_text("stale: true\n", encoding="utf-8")
            for config_text, fixture_value in (("[broken", {}), ("{}\n", {"cancelled": True})):
                config = root / "input.yaml"
                fixture = root / "rois.json"
                config.write_text(config_text, encoding="utf-8")
                fixture.write_text(json.dumps(fixture_value), encoding="utf-8")
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "tools.roi_selector",
                        "--image",
                        str(image),
                        "--config",
                        str(config),
                        "--output-config",
                        str(output),
                        "--fixture-json",
                        str(fixture),
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(output.read_text(encoding="utf-8"), "stale: true\n")


if __name__ == "__main__":
    unittest.main()
