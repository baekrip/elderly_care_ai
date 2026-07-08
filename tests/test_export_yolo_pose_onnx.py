from __future__ import annotations

import unittest

from tools.export_yolo_pose_onnx import build_export_kwargs


class ExportYoloPoseOnnxTests(unittest.TestCase):
    def test_build_export_kwargs_uses_onnx_format_and_optional_device(self) -> None:
        self.assertEqual(
            build_export_kwargs(imgsz=640, device="cpu", simplify=True),
            {"format": "onnx", "imgsz": 640, "simplify": True, "device": "cpu"},
        )

    def test_build_export_kwargs_omits_empty_device(self) -> None:
        self.assertEqual(
            build_export_kwargs(imgsz=320, device=None, simplify=False),
            {"format": "onnx", "imgsz": 320, "simplify": False},
        )


if __name__ == "__main__":
    unittest.main()
