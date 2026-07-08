from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_OVERLAY_FIELDS = (
    "schema_version",
    "camera_id",
    "frame_id",
    "capture_ts",
    "analysis_ts",
    "source_width",
    "source_height",
    "tracks",
)


def validate_overlay_frame(frame: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_OVERLAY_FIELDS:
        if field not in frame:
            errors.append(f"missing:{field}")
    if "tracks" in frame and not isinstance(frame["tracks"], list):
        errors.append("tracks_not_list")
    for field in ["capture_ts", "analysis_ts"]:
        if field in frame:
            if _timestamp_ms(frame[field]) is None:
                errors.append(f"invalid_timestamp:{field}")
    return errors


def _timestamp_ms(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass

    iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1000.0


def _latency_ms(frame: dict[str, Any]) -> float | None:
    capture_ts = _timestamp_ms(frame.get("capture_ts"))
    analysis_ts = _timestamp_ms(frame.get("analysis_ts"))
    if capture_ts is None or analysis_ts is None:
        return None
    return analysis_ts - capture_ts


def summarize_overlay_frames(frames: list[dict[str, Any]], *, duration_sec: float) -> dict[str, Any]:
    validation_errors = [error for frame in frames for error in validate_overlay_frame(frame)]
    latencies = [value for frame in frames if (value := _latency_ms(frame)) is not None]
    return {
        "schema_version": "overlay-probe-v1",
        "duration_sec": float(duration_sec),
        "frame_count": len(frames),
        "fps": round(len(frames) / duration_sec, 4) if duration_sec > 0 else 0.0,
        "valid_frame_count": sum(1 for frame in frames if not validate_overlay_frame(frame)),
        "schema_error_count": len(validation_errors),
        "schema_errors": validation_errors[:20],
        "avg_latency_ms": round(float(statistics.fmean(latencies)), 4) if latencies else 0.0,
        "max_latency_ms": round(max(latencies), 4) if latencies else 0.0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Orin overlay WebSocket and write a validation report.")
    parser.add_argument("--url", required=True, help="Overlay WebSocket URL, for example ws://ORIN:8000/ws/overlay/raspi_cam01")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--report-out", default="reports/overlay_ws_probe.json")
    return parser


async def _collect_frames(url: str, seconds: float) -> list[dict[str, Any]]:
    import websockets

    frames: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(float(seconds), 0.1)
    async with websockets.connect(url, open_timeout=5) as websocket:
        while time.monotonic() < deadline:
            timeout = max(deadline - time.monotonic(), 0.01)
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            payload = json.loads(message)
            if isinstance(payload, dict):
                frames.append(payload)
            elif isinstance(payload, list):
                frames.extend(item for item in payload if isinstance(item, dict))
    return frames


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    frames = asyncio.run(_collect_frames(args.url, args.seconds))
    report = summarize_overlay_frames(frames, duration_sec=args.seconds)
    report["url"] = args.url
    report_path = Path(args.report_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["frame_count"] > 0 and report["schema_error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
