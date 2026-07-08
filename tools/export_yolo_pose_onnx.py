from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def build_export_kwargs(*, imgsz: int, device: str | None, simplify: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "format": "onnx",
        "imgsz": int(imgsz),
        "simplify": bool(simplify),
    }
    if device:
        kwargs["device"] = device
    return kwargs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export YOLO pose .pt model to ONNX for Pi5 runtime")
    parser.add_argument("--model", default="edge/models/yolo26s-pose.pt", help="source .pt model path")
    parser.add_argument("--imgsz", type=int, default=640, help="export image size")
    parser.add_argument("--device", default=None, help="optional export device, e.g. cpu or 0")
    parser.add_argument("--no-simplify", action="store_true", help="disable ONNX graph simplification")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    model_path = Path(args.model)
    if not model_path.exists():
        raise FileNotFoundError(f"model not found: {model_path}")

    from ultralytics import YOLO

    model = YOLO(str(model_path))
    exported = model.export(
        **build_export_kwargs(
            imgsz=args.imgsz,
            device=args.device,
            simplify=not args.no_simplify,
        )
    )
    print(exported)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
