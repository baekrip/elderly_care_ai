from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from server.services.stgcn_classifier import STGCNClassifier


class STGCNBackendTests(unittest.TestCase):
    def test_failed_checkpoint_load_clears_partial_model(self) -> None:
        original_load_model = STGCNClassifier._load_model

        def fail_after_partial_assignment(classifier: STGCNClassifier, _model_path: str) -> None:
            classifier.model = object()
            raise RuntimeError("invalid checkpoint")

        try:
            STGCNClassifier._load_model = fail_after_partial_assignment
            classifier = STGCNClassifier(model_path=__file__, device="cpu")
        finally:
            STGCNClassifier._load_model = original_load_model

        self.assertIsNone(classifier.model)

    def test_fp16_model_and_input_use_half_precision(self) -> None:
        import server.services.stgcn_classifier as stgcn_module

        original_torch = stgcn_module.torch

        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                return True

        class _Tensor:
            def __init__(self) -> None:
                self.half_called = False

            def to(self, _device: str) -> "_Tensor":
                return self

            def half(self) -> "_Tensor":
                self.half_called = True
                return self

        class _Torch:
            cuda = _Cuda()

            @staticmethod
            def from_numpy(_value: object) -> _Tensor:
                return _Tensor()

        class _Model:
            def __init__(self) -> None:
                self.half_called = False

            def half(self) -> "_Model":
                self.half_called = True
                return self

        try:
            stgcn_module.torch = _Torch()
            classifier = STGCNClassifier(model_path=None, device="cuda", backend="pytorch", precision="fp16")
            model = _Model()
            classifier.model = model

            classifier._apply_model_precision()
            tensor = classifier._input_tensor_for_inference(np.zeros((1,), dtype=np.float32))
        finally:
            stgcn_module.torch = original_torch

        self.assertTrue(model.half_called)
        self.assertTrue(tensor.half_called)

    def test_tensorrt_backend_falls_back_when_engine_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_engine = Path(tmpdir) / "missing.engine"
            classifier = STGCNClassifier(model_path=str(missing_engine), device="cpu", backend="tensorrt")

        self.assertEqual(classifier.backend, "pytorch")
        self.assertIn("fallback", classifier.backend_detail)

    def test_cuda_device_falls_back_to_cpu_when_unavailable(self) -> None:
        import server.services.stgcn_classifier as stgcn_module

        original_torch = stgcn_module.torch

        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                return False

        class _Torch:
            cuda = _Cuda()

        try:
            stgcn_module.torch = _Torch()
            classifier = STGCNClassifier(model_path=None, device="cuda", backend="pytorch")
        finally:
            stgcn_module.torch = original_torch

        self.assertEqual(classifier.device, "cpu")

    def test_auto_precision_uses_fp16_on_cuda(self) -> None:
        import server.services.stgcn_classifier as stgcn_module

        original_torch = stgcn_module.torch

        class _Cuda:
            @staticmethod
            def is_available() -> bool:
                return True

        class _Torch:
            cuda = _Cuda()

        try:
            stgcn_module.torch = _Torch()
            classifier = STGCNClassifier(model_path=None, device="cuda", backend="pytorch", precision="auto")
        finally:
            stgcn_module.torch = original_torch

        self.assertEqual(classifier.precision, "fp16")

    def test_fp16_precision_falls_back_to_fp32_on_cpu(self) -> None:
        classifier = STGCNClassifier(model_path=None, device="cpu", backend="pytorch", precision="fp16")

        self.assertEqual(classifier.precision, "fp32")
        self.assertIn("fp16 unavailable", classifier.backend_detail)

    def test_tensorrt_runtime_status_records_required_fallback_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing_engine = Path(tmpdir) / "missing.engine"
            classifier = STGCNClassifier(
                model_path=str(missing_engine),
                device="cpu",
                backend="tensorrt",
                precision="fp16",
            )

        status = classifier.runtime_status()
        self.assertEqual(status["requested_backend"], "tensorrt")
        self.assertEqual(status["selected_backend"], "pytorch")
        self.assertEqual(status["precision"], "fp32")
        self.assertEqual(status["engine_path"], str(missing_engine))
        self.assertIs(status["load_attempted"], True)
        self.assertIn("engine_path not found", str(status["fallback_reason"]))
        self.assertIn("fallback", classifier.backend_detail)
        self.assertIn("latency_ms", status)

    def test_existing_tensorrt_file_uses_tensorrt_loader_when_available(self) -> None:
        original_loader = STGCNClassifier._load_tensorrt_engine

        def fake_loader(classifier: STGCNClassifier, engine_path: str) -> None:
            classifier.model = object()
            classifier.backend_detail = f"tensorrt: engine loaded {engine_path}"

        try:
            STGCNClassifier._load_tensorrt_engine = fake_loader
            with tempfile.TemporaryDirectory() as tmpdir:
                engine_path = Path(tmpdir) / "model.engine"
                engine_path.write_bytes(b"fake engine")
                classifier = STGCNClassifier(
                    model_path=str(engine_path),
                    device="cpu",
                    backend="tensorrt",
                    precision="fp16",
                )
        finally:
            STGCNClassifier._load_tensorrt_engine = original_loader

        status = classifier.runtime_status()
        self.assertEqual(status["requested_backend"], "tensorrt")
        self.assertEqual(status["selected_backend"], "tensorrt")
        self.assertEqual(status["precision"], "fp16")
        self.assertEqual(status["fallback_reason"], "")
        self.assertIn("tensorrt: engine loaded", classifier.backend_detail)

    def test_invalid_tensorrt_file_falls_back_without_success_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine_path = Path(tmpdir) / "model.engine"
            engine_path.write_bytes(b"not a torch checkpoint")
            classifier = STGCNClassifier(
                model_path=str(engine_path),
                device="cpu",
                backend="tensorrt",
            )

        status = classifier.runtime_status()
        self.assertEqual(status["requested_backend"], "tensorrt")
        self.assertEqual(status["selected_backend"], "pytorch")
        self.assertIs(status["load_attempted"], True)
        self.assertIn("TensorRT engine load failed", str(status["fallback_reason"]))


if __name__ == "__main__":
    unittest.main()
