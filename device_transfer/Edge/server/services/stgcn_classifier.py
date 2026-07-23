"""Server ST-GCN Classifier — Phase 6.

Receives CandidateWindow, converts skeleton sequence to ST-GCN input tensor,
runs inference, and returns final action label + confidence.

Until a trained ST-GCN model is available, this module operates in stub mode
and returns the coarse label from the candidate as-is.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None
logger = logging.getLogger(__name__)

# COCO 17 adjacency (undirected edges for ST-GCN graph)
COCO_17_EDGES = [
    (0, 1), (0, 2), (1, 3), (2, 4),       # head
    (5, 6),                                 # shoulders
    (5, 7), (7, 9),                         # left arm
    (6, 8), (8, 10),                        # right arm
    (5, 11), (6, 12),                       # torso
    (11, 12),                               # hips
    (11, 13), (13, 15),                     # left leg
    (12, 14), (14, 16),                     # right leg
]

# ST-GCN input shape: (N, C, T, V, M)
# N=batch, C=3(x,y,conf), T=24frames, V=17joints, M=1person
CHANNELS = 3
FRAMES = 24
JOINTS = 17
PERSONS = 1
INPUT_SHAPE = (1, CHANNELS, FRAMES, JOINTS, PERSONS)
MULTITASK_INPUT_SHAPE = (1, 60, 17, 3)


def _require_tensorrt_enqueue(succeeded: bool, operation: str) -> None:
    if not succeeded:
        raise RuntimeError(
            f"TensorRT {operation} failed; inference output was discarded"
        )


class TensorRTSTGCNRunner:
    def __init__(self, engine_path: str) -> None:
        import pycuda.autoprimaryctx  # noqa: F401
        import pycuda.driver as cuda
        import tensorrt as trt

        self.cuda = cuda
        logger_trt = trt.Logger(trt.Logger.WARNING)
        runtime = trt.Runtime(logger_trt)
        engine_bytes = Path(engine_path).read_bytes()
        self.engine = runtime.deserialize_cuda_engine(engine_bytes)
        if self.engine is None:
            raise RuntimeError("TensorRT deserialize_cuda_engine returned None")
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise RuntimeError("TensorRT create_execution_context returned None")
        self.is_legacy = hasattr(self.engine, "get_binding_index")
        if self.is_legacy:
            input_idx = self.engine.get_binding_index("input")
            output_idx = self.engine.get_binding_index("output")
            self.output_shape = tuple(self.engine.get_binding_shape(output_idx))
            self.bindings = [0] * int(self.engine.num_bindings)
            self.input_binding_index = input_idx
            self.output_binding_index = output_idx
        else:
            self.context.set_input_shape("input", INPUT_SHAPE)
            self.output_shape = tuple(self.engine.get_tensor_shape("output"))
            if any(int(dim) < 0 for dim in self.output_shape):
                self.output_shape = tuple(self.context.get_tensor_shape("output"))
        self.stream = cuda.Stream()
        self.host_input = np.empty(INPUT_SHAPE, dtype=np.float32)
        self.host_output = np.empty(self.output_shape, dtype=np.float32)
        self.device_input = cuda.mem_alloc(self.host_input.nbytes)
        self.device_output = cuda.mem_alloc(self.host_output.nbytes)

    def infer(self, input_array: np.ndarray) -> np.ndarray:
        np.copyto(self.host_input, np.ascontiguousarray(input_array, dtype=np.float32))
        self.cuda.memcpy_htod_async(self.device_input, self.host_input, self.stream)
        if self.is_legacy:
            self.bindings[self.input_binding_index] = int(self.device_input)
            self.bindings[self.output_binding_index] = int(self.device_output)
            _require_tensorrt_enqueue(
                self.context.execute_async_v2(self.bindings, self.stream.handle),
                "execute_async_v2",
            )
        else:
            self.context.set_tensor_address("input", int(self.device_input))
            self.context.set_tensor_address("output", int(self.device_output))
            _require_tensorrt_enqueue(
                self.context.execute_async_v3(self.stream.handle),
                "execute_async_v3",
            )
        self.cuda.memcpy_dtoh_async(self.host_output, self.device_output, self.stream)
        self.stream.synchronize()
        return np.array(self.host_output, copy=True)


class MultiTaskTensorRTRunner:
    def __init__(self, engine_path: str) -> None:
        import pycuda.autoprimaryctx  # noqa: F401
        import pycuda.driver as cuda
        import tensorrt as trt

        self.cuda = cuda
        runtime = trt.Runtime(trt.Logger(trt.Logger.WARNING))
        self.engine = runtime.deserialize_cuda_engine(Path(engine_path).read_bytes())
        if self.engine is None:
            raise RuntimeError("TensorRT MultiTask engine deserialization failed")
        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise RuntimeError("TensorRT MultiTask execution context creation failed")

        self.is_legacy = hasattr(self.engine, "get_binding_index")
        if self.is_legacy:
            names = [
                self.engine.get_binding_name(index)
                for index in range(int(self.engine.num_bindings))
            ]
            if "input" not in names:
                raise RuntimeError(f"TensorRT MultiTask input missing: {names}")
            self.input_binding_index = self.engine.get_binding_index("input")
            self.output_binding_indices = {
                name: self.engine.get_binding_index(name)
                for name in ("activity_logits", "risk_logits")
            }
            input_shape = tuple(self.engine.get_binding_shape(self.input_binding_index))
            if any(int(dim) < 0 for dim in input_shape):
                self.context.set_binding_shape(
                    self.input_binding_index,
                    MULTITASK_INPUT_SHAPE,
                )
            output_shapes = {
                name: tuple(self.context.get_binding_shape(index))
                for name, index in self.output_binding_indices.items()
            }
            self.bindings = [0] * int(self.engine.num_bindings)
        else:
            names = [
                self.engine.get_tensor_name(index)
                for index in range(int(self.engine.num_io_tensors))
            ]
            if "input" not in names:
                raise RuntimeError(f"TensorRT MultiTask input missing: {names}")
            self.input_name = "input"
            self.output_names = ("activity_logits", "risk_logits")
            self.context.set_input_shape(self.input_name, MULTITASK_INPUT_SHAPE)
            output_shapes = {
                name: tuple(self.context.get_tensor_shape(name))
                for name in self.output_names
            }

        if set(output_shapes) != {"activity_logits", "risk_logits"}:
            raise RuntimeError(
                f"TensorRT MultiTask output names mismatch: {sorted(output_shapes)}"
            )
        if output_shapes["activity_logits"][-1] != 5:
            raise RuntimeError(
                f"unexpected activity output shape: {output_shapes['activity_logits']}"
            )
        if output_shapes["risk_logits"][-1] != 3:
            raise RuntimeError(
                f"unexpected risk output shape: {output_shapes['risk_logits']}"
            )

        self.input_shape = MULTITASK_INPUT_SHAPE
        self.output_shapes = output_shapes
        self.stream = cuda.Stream()
        self.host_input = np.empty(self.input_shape, dtype=np.float32)
        self.device_input = cuda.mem_alloc(self.host_input.nbytes)
        self.host_outputs = {
            name: np.empty(shape, dtype=np.float32)
            for name, shape in output_shapes.items()
        }
        self.device_outputs = {
            name: cuda.mem_alloc(values.nbytes)
            for name, values in self.host_outputs.items()
        }

    def infer(self, input_array: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        values = np.asarray(input_array, dtype=np.float32)
        if values.shape != self.input_shape:
            raise ValueError(
                f"unexpected MultiTask TensorRT input: {values.shape}"
            )
        np.copyto(self.host_input, np.ascontiguousarray(values))
        self.cuda.memcpy_htod_async(self.device_input, self.host_input, self.stream)
        if self.is_legacy:
            self.bindings[self.input_binding_index] = int(self.device_input)
            for name, index in self.output_binding_indices.items():
                self.bindings[index] = int(self.device_outputs[name])
            _require_tensorrt_enqueue(
                self.context.execute_async_v2(self.bindings, self.stream.handle),
                "execute_async_v2",
            )
        else:
            self.context.set_tensor_address("input", int(self.device_input))
            for name in self.output_names:
                self.context.set_tensor_address(name, int(self.device_outputs[name]))
            _require_tensorrt_enqueue(
                self.context.execute_async_v3(self.stream.handle),
                "execute_async_v3",
            )
        for name, host_output in self.host_outputs.items():
            self.cuda.memcpy_dtoh_async(
                host_output,
                self.device_outputs[name],
                self.stream,
            )
        self.stream.synchronize()
        outputs = tuple(
            np.array(self.host_outputs[name], copy=True)
            for name in ("activity_logits", "risk_logits")
        )
        return outputs[0], outputs[1]


class MultiTaskOnnxRunner:
    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            model_path,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 2:
            raise RuntimeError("MultiTask ST-GCN ONNX contract requires one input and two outputs")
        input_shape = list(inputs[0].shape)
        if input_shape[1:] != [60, 17, 3]:
            raise RuntimeError(f"unexpected MultiTask ST-GCN input shape: {input_shape}")
        output_shapes = [list(output.shape) for output in outputs]
        if (
            len(output_shapes) != 2
            or len(output_shapes[0]) != 2
            or len(output_shapes[1]) != 2
            or output_shapes[0][1] != 5
            or output_shapes[1][1] != 3
        ):
            raise RuntimeError(f"unexpected MultiTask ST-GCN output shapes: {output_shapes}")
        self.input_name = inputs[0].name
        self.output_names = [output.name for output in outputs]
        self.input_shape = input_shape
        self.output_shapes = output_shapes

    def infer(self, input_array: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        values = np.asarray(input_array, dtype=np.float32)
        if values.ndim != 4 or tuple(values.shape[1:]) != (60, 17, 3):
            raise ValueError(f"unexpected MultiTask ST-GCN input: {values.shape}")
        outputs = self.session.run(self.output_names, {self.input_name: values})
        if len(outputs) != 2:
            raise RuntimeError("MultiTask ST-GCN ONNX returned an unexpected output count")
        return (
            np.asarray(outputs[0], dtype=np.float32),
            np.asarray(outputs[1], dtype=np.float32),
        )


class STGCNClassifier:
    """ST-GCN inference wrapper.

    Operates in two modes:
      - stub mode (no model file): returns coarse label passthrough
      - inference mode (model loaded): runs actual ST-GCN forward pass
    """

    def __init__(
        self,
        model_path: str | None = None,
        device: str = "cpu",
        backend: str = "pytorch",
        precision: str = "auto",
    ) -> None:
        self.requested_backend = str(backend or "pytorch").lower()
        self.model = None
        self.device = str(device) if self.requested_backend == "tensorrt" else self._resolve_device(device)
        self.backend = self.requested_backend
        self.precision = precision
        self.engine_path = str(model_path or "")
        self.load_attempted = False
        self.fallback_reason = ""
        self.latency_ms = 0.0
        self.backend_detail = f"{self.backend}: initialized"
        self.model_type = "legacy"
        self.risk_label_map: list[str] = ["NORMAL", "ABNORMAL", "DANGER"]
        if precision == "auto":
            self.precision = "fp16" if self.device.startswith("cuda") else "fp32"
        elif precision == "fp16" and not self.device.startswith("cuda") and self.requested_backend != "tensorrt":
            self.precision = "fp32"
            self.fallback_reason = "fp16 unavailable on non-cuda device"
            self.backend_detail = f"{self.backend}: initialized, fp16 unavailable"
        else:
            self.backend_detail = f"{self.backend}: initialized"
        self.label_map: list[str] = [
            "STANDING", "SITTING", "LYING", "WALKING", "TRANSITION",
            "FALL", "GRADUAL_FALL", "LOSS_OF_BALANCE", "NORMAL_LYING",
        ]

        load_path = model_path
        if self.backend == "tensorrt":
            self.load_attempted = True
            if not model_path:
                self.fallback_reason = "tensorrt engine_path not configured"
                self.backend_detail = f"fallback: {self.fallback_reason}"
                self.backend = "pytorch"
                if self.precision == "fp16" and not self.device.startswith("cuda"):
                    self.precision = "fp32"
                load_path = None
            elif not Path(model_path).exists():
                self.fallback_reason = "tensorrt engine_path not found"
                self.backend_detail = f"fallback: {self.fallback_reason}"
                self.backend = "pytorch"
                if self.precision == "fp16" and not self.device.startswith("cuda"):
                    self.precision = "fp32"
                load_path = None
            else:
                started = time.perf_counter()
                try:
                    self._load_tensorrt_engine(str(model_path))
                    self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
                    self.backend = "tensorrt"
                    self.backend_detail = "tensorrt: engine loaded"
                    load_path = None
                except Exception as exc:
                    self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
                    self.model = None
                    self.fallback_reason = f"TensorRT engine load failed: {exc}"
                    self.backend_detail = f"fallback: {self.fallback_reason}"
                    self.backend = "pytorch"
                    if self.precision == "fp16" and not self.device.startswith("cuda"):
                        self.precision = "fp32"
                    load_path = None
        elif self.backend in {"onnxruntime"}:
            self.load_attempted = True
            if not model_path:
                self.fallback_reason = f"{self.backend} engine_path not configured"
                self.backend_detail = f"fallback: {self.fallback_reason}"
                self.backend = "pytorch"
                load_path = None
            elif not Path(model_path).exists():
                self.fallback_reason = f"{self.requested_backend} engine_path not found"
                self.backend_detail = f"fallback: {self.fallback_reason}"
                self.backend = "pytorch"
                load_path = None
            else:
                started = time.perf_counter()
                try:
                    self._load_multitask_onnx(str(model_path))
                    self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
                    self.backend = "onnxruntime"
                    self.backend_detail = "onnxruntime: MultiTask ST-GCN loaded"
                    load_path = None
                except Exception as exc:
                    self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
                    self.model = None
                    self.fallback_reason = f"ONNX MultiTask load failed: {exc}"
                    self.backend_detail = f"fallback: {self.fallback_reason}"
                    self.backend = "pytorch"
                    load_path = None

        if load_path:
            if self.backend == "pytorch" and (load_path.endswith(".onnx") or load_path.endswith(".engine")):
                self.model = None
                self.fallback_reason = "Cannot load ONNX/Engine file with PyTorch backend. Forcing stub mode."
                self.backend_detail = f"fallback: {self.fallback_reason}"
            else:
                self.load_attempted = True
                started = time.perf_counter()
                try:
                    self._load_model(load_path)
                    self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
                    self.backend_detail = "pytorch: checkpoint loaded"
                except Exception as exc:
                    self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
                    self.model = None
                    logger.warning("Failed to load ST-GCN model from %s: %s. Using stub mode.", load_path, exc)
                    if not self.backend_detail.startswith("fallback"):
                        self.fallback_reason = str(exc)
                        self.backend_detail = f"fallback: {exc}"

    @staticmethod
    def _resolve_device(device: str) -> str:
        if str(device).startswith("cuda") and (torch is None or not torch.cuda.is_available()):
            logger.warning("CUDA requested for ST-GCN but unavailable. Falling back to CPU.")
            return "cpu"
        return device

    def _load_model(self, model_path: str) -> None:
        if torch is None:
            raise RuntimeError("torch is not installed")
        from shared.stgcn_model import MiniSTGCN
        checkpoint = torch.load(model_path, map_location=self.device)
        label_map = checkpoint.get("label_map")
        if isinstance(label_map, list) and label_map:
            self.label_map = [str(item) for item in label_map]
        self.model = MiniSTGCN(num_classes=len(self.label_map))
        self.model.load_state_dict(checkpoint["state_dict"])
        self.model.to(self.device)
        self._apply_model_precision()
        self.model.eval()
        logger.info("ST-GCN model loaded from %s", model_path)

    def _load_tensorrt_engine(self, engine_path: str) -> None:
        try:
            self.model = MultiTaskTensorRTRunner(engine_path)
        except RuntimeError:
            self.model = TensorRTSTGCNRunner(engine_path)
            self.model_type = "legacy"
            return
        self.model_type = "multitask_tensorrt"
        self.label_map = [
            "STANDING",
            "SITTING",
            "WALKING",
            "LYING",
            "FALL_DOWN",
        ]
        self.risk_label_map = ["NORMAL", "ABNORMAL", "DANGER"]

    def _load_multitask_onnx(self, model_path: str) -> None:
        self.model = MultiTaskOnnxRunner(model_path)
        self.model_type = "multitask_onnx"
        self.label_map = [
            "STANDING",
            "SITTING",
            "WALKING",
            "LYING",
            "FALL_DOWN",
        ]
        self.risk_label_map = ["NORMAL", "ABNORMAL", "DANGER"]

    def _apply_model_precision(self) -> None:
        if self.model is not None and self.precision == "fp16":
            self.model.half()

    def runtime_status(self) -> dict[str, object]:
        return {
            "requested_backend": self.requested_backend,
            "selected_backend": self.backend,
            "precision": self.precision,
            "engine_path": self.engine_path,
            "load_attempted": self.load_attempted,
            "fallback_reason": self.fallback_reason,
            "latency_ms": self.latency_ms,
            "model_type": self.model_type,
            "label_map": list(self.label_map),
            "risk_label_map": list(self.risk_label_map),
        }

    def _input_tensor_for_inference(self, input_array: np.ndarray) -> Any:
        if torch is None:
            raise RuntimeError("torch is not installed")
        tensor = torch.from_numpy(input_array).to(self.device)
        return tensor.half() if self.precision == "fp16" else tensor

    def prepare_input(self, window: Any) -> np.ndarray:
        """Convert CandidateWindow.sequence to ST-GCN input tensor.

        Args:
            window: CandidateWindow object with .sequence list

        Returns:
            numpy array of shape (1, 3, 24, 17, 1)
        """
        if self.model_type in {"multitask_onnx", "multitask_tensorrt"}:
            return self._prepare_multitask_input(window)

        tensor = np.zeros((1, CHANNELS, FRAMES, JOINTS, PERSONS), dtype=np.float32)

        frames = window.sequence
        num_frames = len(frames)

        for t in range(FRAMES):
            # if fewer frames than 24, repeat last frame (padding)
            frame_idx = min(t, num_frames - 1) if num_frames > 0 else 0
            if frame_idx >= num_frames:
                continue

            frame = frames[frame_idx]
            kps = frame.keypoints_17  # list of [x, y, conf]

            for v in range(min(JOINTS, len(kps))):
                kp = kps[v]
                tensor[0, 0, t, v, 0] = kp[0]  # x
                tensor[0, 1, t, v, 0] = kp[1]  # y
                tensor[0, 2, t, v, 0] = kp[2] if len(kp) > 2 else 0.0  # conf

        # normalize: hip center (joint 11+12 midpoint) relative coords
        for t in range(FRAMES):
            hip_x = (tensor[0, 0, t, 11, 0] + tensor[0, 0, t, 12, 0]) / 2
            hip_y = (tensor[0, 1, t, 11, 0] + tensor[0, 1, t, 12, 0]) / 2
            if hip_x > 0 or hip_y > 0:
                tensor[0, 0, t, :, 0] -= hip_x
                tensor[0, 1, t, :, 0] -= hip_y

        return tensor

    @staticmethod
    def _prepare_multitask_input(window: Any) -> np.ndarray:
        tensor = np.zeros((1, 60, JOINTS, CHANNELS), dtype=np.float32)
        frames = list(window.sequence)
        if not frames:
            return tensor
        indices = np.rint(np.linspace(0, len(frames) - 1, 60)).astype(np.int64)
        for target_index, source_index in enumerate(indices):
            keypoints = frames[int(source_index)].keypoints_17
            for joint_index, point in enumerate(keypoints[:JOINTS]):
                if len(point) >= CHANNELS:
                    tensor[0, target_index, joint_index] = np.asarray(
                        point[:CHANNELS],
                        dtype=np.float32,
                    )
        return tensor

    @staticmethod
    def _softmax(logits: np.ndarray) -> np.ndarray:
        values = np.asarray(logits, dtype=np.float32)
        shifted = values - np.max(values, axis=1, keepdims=True)
        exponent = np.exp(shifted)
        return exponent / np.maximum(exponent.sum(axis=1, keepdims=True), 1e-12)

    def classify(self, window: Any) -> tuple[str, float, str]:
        """Classify a CandidateWindow.

        Returns:
            (final_label, confidence, detail_message)
        """
        if self.model is None:
            # stub mode: passthrough coarse label with reduced confidence
            return (
                window.coarse_action,
                window.coarse_confidence * 0.9,
                "stub_mode: no ST-GCN model loaded, returning coarse label"
            )

        input_tensor = self.prepare_input(window)
        if self.backend == "tensorrt" and self.model_type == "multitask_tensorrt":
            started = time.perf_counter()
            runner = self.model
            if runner is None or not hasattr(runner, "infer"):
                raise RuntimeError("MultiTask ST-GCN TensorRT runner is not initialized")
            activity_logits, risk_logits = runner.infer(input_tensor)
            activity_probabilities = self._softmax(activity_logits)
            risk_probabilities = self._softmax(risk_logits)
            activity_index = int(activity_probabilities[0].argmax())
            risk_index = int(risk_probabilities[0].argmax())
            activity_label = self.label_map[activity_index]
            risk_label = self.risk_label_map[risk_index]
            activity_confidence = float(activity_probabilities[0, activity_index])
            risk_confidence = float(risk_probabilities[0, risk_index])
            final_label = "FALL" if risk_label == "DANGER" else activity_label
            confidence = risk_confidence if risk_label == "DANGER" else activity_confidence
            self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
            detail = (
                "inference_mode: multitask tensorrt; "
                f"activity={activity_label}:{activity_confidence:.6f}; "
                f"risk={risk_label}:{risk_confidence:.6f}"
            )
            return final_label, confidence, detail

        if self.backend == "tensorrt" and isinstance(self.model, TensorRTSTGCNRunner):
            started = time.perf_counter()
            output_array = self.model.infer(input_tensor)
            logits = np.asarray(output_array, dtype=np.float32).reshape(-1)
            logits = logits - np.max(logits)
            probabilities = np.exp(logits)
            probabilities = probabilities / max(float(np.sum(probabilities)), 1e-12)
            pred_idx = int(np.argmax(probabilities))
            confidence = float(probabilities[pred_idx])
            self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
            return self.label_map[pred_idx], confidence, "inference_mode: tensorrt engine loaded"

        if self.backend == "onnxruntime" and self.model_type == "multitask_onnx":
            started = time.perf_counter()
            runner = self.model
            if runner is None or not hasattr(runner, "infer"):
                raise RuntimeError("MultiTask ST-GCN ONNX runner is not initialized")
            activity_logits, risk_logits = runner.infer(input_tensor)
            activity_probabilities = self._softmax(activity_logits)
            risk_probabilities = self._softmax(risk_logits)
            activity_index = int(activity_probabilities[0].argmax())
            risk_index = int(risk_probabilities[0].argmax())
            activity_label = self.label_map[activity_index]
            risk_label = self.risk_label_map[risk_index]
            activity_confidence = float(activity_probabilities[0, activity_index])
            risk_confidence = float(risk_probabilities[0, risk_index])
            final_label = "FALL" if risk_label == "DANGER" else activity_label
            confidence = risk_confidence if risk_label == "DANGER" else activity_confidence
            self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
            detail = (
                "inference_mode: multitask onnx; "
                f"activity={activity_label}:{activity_confidence:.6f}; "
                f"risk={risk_label}:{risk_confidence:.6f}"
            )
            return final_label, confidence, detail

        if torch is None:
            return (
                window.coarse_action,
                window.coarse_confidence * 0.9,
                "stub_mode: torch not installed",
            )

        started = time.perf_counter()
        with torch.no_grad():
            output = self.model(self._input_tensor_for_inference(input_tensor))
            probabilities = torch.softmax(output, dim=1)[0]
            pred_idx = int(probabilities.argmax().item())
            confidence = float(probabilities[pred_idx].item())
        self.latency_ms = round((time.perf_counter() - started) * 1000.0, 4)
        label = self.label_map[pred_idx]
        detail = "inference_mode: stgcn checkpoint loaded"

        return label, confidence, detail
