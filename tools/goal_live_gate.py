from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.remaining_plan_preflight import build_preflight_report


def _status(ready: bool, *, reason: str, command_template: str) -> dict[str, Any]:
    return {
        "ready": bool(ready),
        "blocked_reason": "" if ready else reason,
        "command_template": command_template,
    }


def build_goal_live_gate_report(
    *,
    root: Path | None = None,
    clip_file: Path | None = None,
    overlay_url_provided: bool = False,
) -> dict[str, Any]:
    project_root = (root or Path.cwd()).resolve()
    preflight = build_preflight_report(project_root)
    prerequisites = preflight["prerequisites"]
    clip_ready = bool(clip_file and clip_file.exists())

    backend_json_ready = bool(prerequisites["backend_credentials_present"])
    backend_clip_ready = backend_json_ready and bool(prerequisites["aws_prerequisites_present"]) and clip_ready
    overlay_ready = bool(prerequisites["overlay_ws_configured"] and prerequisites["orin_configured"]) or overlay_url_provided

    gates = {
        "backend_json": _status(
            backend_json_ready,
            reason="backend URL alias and token are required",
            command_template="python tools/test_backend_batch.py --multi",
        ),
        "backend_clip": _status(
            backend_clip_ready,
            reason="backend credentials, AWS prerequisites, and --clip-file are required",
            command_template="python tools/test_backend_clip_smoke.py --live --clip-file <danger-clip.mp4>",
        ),
        "overlay_ws": _status(
            overlay_ready,
            reason="ORIN_OVERLAY_WS_URL/OVERLAY_WS_URL plus ORIN_HOST/ORIN_IP, or --overlay-url, is required",
            command_template="python tools/overlay_ws_probe.py --url <overlay-ws-url> --seconds 10",
        ),
    }
    blocked_gates = [name for name, gate in gates.items() if not gate["ready"]]

    return {
        "schema_version": "goal-live-gate-v1",
        "all_ready": not blocked_gates,
        "blocked_gates": blocked_gates,
        "gates": gates,
        "prerequisites": {
            "backend_url_configured": prerequisites["backend_url_configured"],
            "backend_token_configured": prerequisites["backend_token_configured"],
            "aws_prerequisites_present": prerequisites["aws_prerequisites_present"],
            "orin_configured": prerequisites["orin_configured"],
            "overlay_ws_configured": prerequisites["overlay_ws_configured"],
            "clip_file_provided": clip_file is not None,
            "clip_file_exists": clip_ready,
        },
        "execution_policy": {
            "live_backend_sent": False,
            "live_clip_sent": False,
            "live_overlay_connected": False,
            "values_masked": True,
        },
        "secret_scan": {
            "masked": True,
            "leaks_detected": [],
        },
    }


def _write_json(report: dict[str, Any], report_out: Path) -> None:
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Goal live gate preflight for JSON, clip, and overlay verification.")
    parser.add_argument("--clip-file", default="")
    parser.add_argument("--overlay-url", default="")
    parser.add_argument("--report-out", default="reports/goal_live_gate.json")
    args = parser.parse_args(argv)

    clip_file = Path(args.clip_file) if args.clip_file else None
    report = build_goal_live_gate_report(
        root=Path.cwd(),
        clip_file=clip_file,
        overlay_url_provided=bool(args.overlay_url),
    )
    _write_json(report, Path(args.report_out))
    print(f"wrote {args.report_out}")
    return 0 if report["all_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
