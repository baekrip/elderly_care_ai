from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools import evaluate_label_segment


class EvaluateLabelSegmentTests(unittest.TestCase):
    def test_evaluate_segment_uses_matching_historical_fixture_backend(self) -> None:
        configured = {
            "server": {"enabled": True},
            "camera": {},
            "model": {
                "backend": "onnxruntime",
                "model_path": "edge/models/yolo26s-pose.onnx",
            },
        }
        captured_model: dict[str, str] = {}

        class _StopAfterCapture(RuntimeError):
            pass

        def _capture(config: dict) -> None:
            captured_model.update(config["model"])
            raise _StopAfterCapture

        with (
            patch.object(evaluate_label_segment, "load_config", return_value=configured),
            patch.object(evaluate_label_segment, "YoloPoseEstimator", side_effect=_capture),
            self.assertRaises(_StopAfterCapture),
        ):
            evaluate_label_segment.evaluate_segment({}, Path("fixture.mp4"), "config.yaml", 1)

        self.assertEqual(captured_model["backend"], "ultralytics")
        self.assertEqual(Path(captured_model["model_path"]).name, "yolo26s-pose.pt")


if __name__ == "__main__":
    unittest.main()
