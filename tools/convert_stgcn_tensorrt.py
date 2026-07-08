"""Convert ST-GCN PyTorch checkpoint → ONNX → TensorRT FP16 engine.

Usage (on Orin with TensorRT installed):
    python tools/convert_stgcn_tensorrt.py \
        --checkpoint server/models/stgcn_fall_binary.pth \
        --output-onnx server/models/stgcn_fall_binary.onnx \
        --output-trt  server/models/stgcn_fall_binary_fp16.engine \
        --precision fp16

Benchmark only (skip conversion):
    python tools/convert_stgcn_tensorrt.py \
        --benchmark server/models/stgcn_fall_binary_fp16.engine

Requirements: torch, onnx, onnxruntime (+ tensorrt, pycuda on Orin)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import SupportsBytes

import numpy as np

# ── project root ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "device_transfer" / "Edge"))

# ── constants ───────────────────────────────────────────────────
BATCH = 1
CHANNELS = 3
FRAMES = 24
JOINTS = 17
PERSONS = 1
INPUT_SHAPE = (BATCH, CHANNELS, FRAMES, JOINTS, PERSONS)


def _serialized_engine_to_bytes(engine_bytes: bytes | bytearray | memoryview | SupportsBytes) -> bytes:
    if isinstance(engine_bytes, bytes):
        return engine_bytes
    return bytes(engine_bytes)


# ── step 1: PyTorch → ONNX ─────────────────────────────────────
def export_onnx(checkpoint_path: str, onnx_path: str) -> dict:
    import torch
    from shared.stgcn_model import MiniSTGCN

    ckpt = torch.load(checkpoint_path, map_location="cpu")
    label_map = ckpt.get("label_map", [])
    num_classes = len(label_map) if label_map else 9
    model = MiniSTGCN(num_classes=num_classes)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    dummy = torch.randn(*INPUT_SHAPE)
    torch.onnx.export(
        model,
        dummy,
        onnx_path,
        input_names=["input"],
        output_names=["output"],
        opset_version=17,
        dynamic_axes=None,
        dynamo=False,
    )
    print(f"[OK] ONNX exported → {onnx_path}")
    return {"onnx_path": onnx_path, "num_classes": num_classes, "label_map": label_map}


# ── step 2: ONNX → TensorRT ────────────────────────────────────
def build_tensorrt_engine(onnx_path: str, trt_path: str, precision: str = "fp16") -> str:
    try:
        import tensorrt as trt
    except ImportError:
        print("[SKIP] tensorrt not installed. Use trtexec CLI or install tensorrt.")
        _print_trtexec_fallback(onnx_path, trt_path, precision)
        return ""

    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, logger)

    with open(onnx_path, "rb") as f:
        if not parser.parse(f.read()):
            for i in range(parser.num_errors):
                print(f"  ONNX parse error: {parser.get_error(i)}")
            raise RuntimeError("ONNX parse failed")

    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 1 << 28)  # 256 MB

    if precision == "fp16" and builder.platform_has_fast_fp16:
        config.set_flag(trt.BuilderFlag.FP16)
        print("[INFO] FP16 enabled")
    elif precision == "fp16":
        print("[WARN] FP16 requested but platform does not support fast FP16. Building FP32.")

    engine_bytes = builder.build_serialized_network(network, config)
    if engine_bytes is None:
        raise RuntimeError("TensorRT engine build failed")

    serialized_engine = _serialized_engine_to_bytes(engine_bytes)
    Path(trt_path).parent.mkdir(parents=True, exist_ok=True)
    with open(trt_path, "wb") as f:
        f.write(serialized_engine)
    print(f"[OK] TensorRT engine → {trt_path} ({len(serialized_engine)} bytes)")
    return trt_path


def _print_trtexec_fallback(onnx_path: str, trt_path: str, precision: str) -> None:
    flag = "--fp16" if precision == "fp16" else ""
    print(f"\n  trtexec --onnx={onnx_path} --saveEngine={trt_path} {flag} --workspace=256\n")


# ── step 3: benchmark ──────────────────────────────────────────
def benchmark_pytorch(checkpoint_path: str, iterations: int = 200) -> dict:
    import torch
    from shared.stgcn_model import MiniSTGCN

    ckpt = torch.load(checkpoint_path, map_location="cpu")
    label_map = ckpt.get("label_map", [])
    num_classes = len(label_map) if label_map else 9
    model = MiniSTGCN(num_classes=num_classes)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    dummy = torch.randn(*INPUT_SHAPE, device=device)

    # warmup
    for _ in range(20):
        model(dummy)
    if device == "cuda":
        torch.cuda.synchronize()

    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        model(dummy)
        if device == "cuda":
            torch.cuda.synchronize()
        times.append((time.perf_counter() - start) * 1000)

    return {
        "backend": f"pytorch_{device}",
        "iterations": iterations,
        "mean_ms": round(np.mean(times), 3),
        "median_ms": round(np.median(times), 3),
        "p95_ms": round(np.percentile(times, 95), 3),
        "p99_ms": round(np.percentile(times, 99), 3),
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
    }


def benchmark_onnxruntime(onnx_path: str, iterations: int = 200) -> dict:
    import onnxruntime as ort

    providers = ort.get_available_providers()
    session = ort.InferenceSession(onnx_path, providers=providers)
    dummy = np.random.randn(*INPUT_SHAPE).astype(np.float32)
    input_name = session.get_inputs()[0].name

    # warmup
    for _ in range(20):
        session.run(None, {input_name: dummy})

    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        session.run(None, {input_name: dummy})
        times.append((time.perf_counter() - start) * 1000)

    return {
        "backend": f"onnxruntime_{providers[0]}",
        "iterations": iterations,
        "mean_ms": round(np.mean(times), 3),
        "median_ms": round(np.median(times), 3),
        "p95_ms": round(np.percentile(times, 95), 3),
        "p99_ms": round(np.percentile(times, 99), 3),
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
    }


def benchmark_tensorrt(trt_path: str, iterations: int = 200) -> dict:
    try:
        import tensorrt as trt
        import pycuda.driver as cuda
        import pycuda.autoinit  # noqa: F401
    except ImportError:
        print("[SKIP] tensorrt/pycuda not installed. Cannot benchmark TRT engine.")
        return {"backend": "tensorrt_unavailable"}

    logger = trt.Logger(trt.Logger.WARNING)
    runtime = trt.Runtime(logger)
    with open(trt_path, "rb") as f:
        engine = runtime.deserialize_cuda_engine(f.read())
    context = engine.create_execution_context()

    h_input = np.random.randn(*INPUT_SHAPE).astype(np.float32)

    # TensorRT 10+ vs legacy 8.x check
    is_legacy = hasattr(engine, "get_binding_index")

    if is_legacy:
        input_idx = engine.get_binding_index("input")
        output_idx = engine.get_binding_index("output")
        output_shape = engine.get_binding_shape(output_idx)
    else:
        # TensorRT 10+ APIs
        output_shape = engine.get_tensor_shape("output")

    h_output = np.empty(output_shape, dtype=np.float32)

    d_input = cuda.mem_alloc(h_input.nbytes)
    d_output = cuda.mem_alloc(h_output.nbytes)
    stream = cuda.Stream()

    # warmup
    for _ in range(20):
        cuda.memcpy_htod_async(d_input, h_input, stream)
        if is_legacy:
            context.execute_async_v2([int(d_input), int(d_output)], stream.handle)
        else:
            context.set_input_shape("input", INPUT_SHAPE)
            context.set_tensor_address("input", int(d_input))
            context.set_tensor_address("output", int(d_output))
            context.execute_async_v3(stream.handle)
        cuda.memcpy_dtoh_async(h_output, d_output, stream)
        stream.synchronize()

    times = []
    for _ in range(iterations):
        cuda.memcpy_htod_async(d_input, h_input, stream)
        start = time.perf_counter()
        if is_legacy:
            context.execute_async_v2([int(d_input), int(d_output)], stream.handle)
        else:
            context.execute_async_v3(stream.handle)
        stream.synchronize()
        times.append((time.perf_counter() - start) * 1000)
        cuda.memcpy_dtoh_async(h_output, d_output, stream)
        stream.synchronize()

    d_input.free()
    d_output.free()

    return {
        "backend": "tensorrt_fp16" if "fp16" in str(trt_path).lower() else "tensorrt_fp32",
        "iterations": iterations,
        "mean_ms": round(np.mean(times), 3),
        "median_ms": round(np.median(times), 3),
        "p95_ms": round(np.percentile(times, 95), 3),
        "p99_ms": round(np.percentile(times, 99), 3),
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
    }


# ── main ────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="ST-GCN → ONNX → TensorRT FP16 converter + benchmark")
    parser.add_argument("--checkpoint", type=str, help="PyTorch .pth checkpoint path")
    parser.add_argument("--output-onnx", type=str, default=None, help="Output ONNX path")
    parser.add_argument("--output-trt", type=str, default=None, help="Output TensorRT engine path")
    parser.add_argument("--precision", choices=["fp16", "fp32"], default="fp16")
    parser.add_argument("--benchmark", type=str, default=None, help="Benchmark a TRT engine (skip conversion)")
    parser.add_argument("--iterations", type=int, default=200, help="Benchmark iterations")
    parser.add_argument("--report", type=str, default=None, help="JSON report output path")
    args = parser.parse_args()

    results: dict = {"steps": []}

    if args.benchmark:
        print(f"=== Benchmark TRT engine: {args.benchmark} ===")
        trt_result = benchmark_tensorrt(args.benchmark, args.iterations)
        results["trt_benchmark"] = trt_result
        print(json.dumps(trt_result, indent=2))
    elif args.checkpoint:
        onnx_path = args.output_onnx or args.checkpoint.replace(".pth", ".onnx")
        trt_path = args.output_trt or args.checkpoint.replace(".pth", f"_{args.precision}.engine")

        # export ONNX
        onnx_info = export_onnx(args.checkpoint, onnx_path)
        results["steps"].append({"step": "onnx_export", **onnx_info})

        # build TRT
        built = build_tensorrt_engine(onnx_path, trt_path, args.precision)
        results["steps"].append({"step": "trt_build", "engine_path": built or "skipped"})

        # benchmark all backends
        print("\n=== Benchmarks ===")
        pt_result = benchmark_pytorch(args.checkpoint, args.iterations)
        results["pytorch"] = pt_result
        print(f"  PyTorch: {pt_result['mean_ms']:.2f} ms (median {pt_result['median_ms']:.2f} ms)")

        try:
            onnx_result = benchmark_onnxruntime(onnx_path, args.iterations)
            results["onnxruntime"] = onnx_result
            print(f"  ONNX Runtime: {onnx_result['mean_ms']:.2f} ms (median {onnx_result['median_ms']:.2f} ms)")
        except Exception as e:
            print(f"  ONNX Runtime: skipped ({e})")

        if built:
            trt_result = benchmark_tensorrt(trt_path, args.iterations)
            results["tensorrt"] = trt_result
            if "mean_ms" in trt_result and "median_ms" in trt_result:
                print(f"  TensorRT: {trt_result['mean_ms']:.2f} ms (median {trt_result['median_ms']:.2f} ms)")
            else:
                print(f"  TensorRT: skipped ({trt_result.get('backend', 'unavailable')})")
    else:
        parser.print_help()
        return

    report_path = args.report or "reports/stgcn_tensorrt_benchmark.json"
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Report → {report_path}")


if __name__ == "__main__":
    main()
