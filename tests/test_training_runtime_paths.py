from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.training_runtime_paths import resolve_bundle_model_path


class TrainingRuntimePathTests(unittest.TestCase):
    def test_resolves_deployment_relative_model_from_config_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = Path(temp_dir) / "camera1"
            config = bundle / "edge" / "config.raspi_cam01.yaml"
            model = bundle / "edge" / "models" / "fixture.onnx"
            config.parent.mkdir(parents=True)
            model.parent.mkdir(parents=True)
            config.write_text("model: {}\n", encoding="utf-8")
            model.write_bytes(b"fixture")

            resolved = resolve_bundle_model_path(config, Path("edge/models/fixture.onnx"))

            self.assertEqual(resolved, model.resolve())


if __name__ == "__main__":
    unittest.main()
