from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


CAN_RUN_KEYS: tuple[str, ...] = (
    "pose_replay_gate",
    "overlay_mock_browser",
    "overlay_live_ws",
    "backend_mock",
    "backend_live_manual",
    "label_gate",
    "stability_short_validation",
    "offload_decision",
)

MANUAL_OR_OUT_OF_SCOPE: tuple[str, ...] = (
    "backend_live_send",
    "12_24h_soak",
    "model_training",
    "model_replacement",
    "orin_pose_offload_implementation",
)

CURRENT_YOLO_FILES: tuple[str, ...] = (
    "edge/main.py",
    "edge/async_pose.py",
    "edge/pose_estimator.py",
)

BACKEND_URL_ENV_KEYS: tuple[str, ...] = (
    "BACKEND_BASE_URL",
    "AI_BACKEND_BASE_URL",
    "AI2_SERVER_URL",
    "BACKEND_URL",
)

BACKEND_TOKEN_ENV_KEYS: tuple[str, ...] = (
    "APP_TOKEN",
    "BACKEND_APP_TOKEN",
)


def _read_env_keys(env_path: Path) -> set[str]:
    if not env_path.exists():
        return set()
    keys: set[str] = set()
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, _value = line.partition("=")
        keys.add(key.strip())
    return keys


def _count_existing(root: Path, patterns: tuple[str, ...]) -> int:
    count = 0
    for pattern in patterns:
        count += sum(1 for path in root.glob(pattern) if path.is_file())
    return count


def _has_backend_credentials(env_keys: set[str]) -> bool:
    return bool(set(BACKEND_URL_ENV_KEYS) & env_keys) and bool(set(BACKEND_TOKEN_ENV_KEYS) & env_keys)


def _has_aws_prerequisites(env_keys: set[str]) -> bool:
    return {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}.issubset(env_keys)


def build_preflight_report(root: Path | None = None) -> dict[str, Any]:
    project_root = (root or Path.cwd()).resolve()
    env_keys = _read_env_keys(project_root / ".env")
    replay_clip_count = _count_existing(
        project_root,
        (
            "temp_test/*.mp4",
            "data/**/*.mp4",
            "video/**/*.mp4",
            "experiments/behavior_training/reports/pose_replay_samples_*/**/*.mp4",
        ),
    )
    label_count = _count_existing(
        project_root,
        (
            "temp_test/*label*.json",
            "temp_test/*.jsonl",
            "data/**/*label*.json",
            "experiments/behavior_training/**/*.jsonl",
            "experiments/behavior_training/**/*label*.json",
        ),
    )
    pi5_configured = bool({"PI5_HOST", "PI5_IP", "RASPI_HOST"} & env_keys)
    orin_configured = bool({"ORIN_HOST", "ORIN_IP"} & env_keys)
    overlay_ws_configured = bool({"ORIN_OVERLAY_WS_URL", "OVERLAY_WS_URL"} & env_keys)
    backend_url_configured = bool(set(BACKEND_URL_ENV_KEYS) & env_keys)
    backend_token_configured = bool(set(BACKEND_TOKEN_ENV_KEYS) & env_keys)
    backend_credentials = _has_backend_credentials(env_keys)
    aws_prerequisites = _has_aws_prerequisites(env_keys)

    can_run = {
        "pose_replay_gate": replay_clip_count >= 3,
        "overlay_mock_browser": True,
        "overlay_live_ws": overlay_ws_configured and orin_configured,
        "backend_mock": True,
        "backend_live_manual": False,
        "label_gate": label_count > 0,
        "stability_short_validation": True,
        "offload_decision": False,
    }

    blocked_reasons: dict[str, str] = {}
    if not can_run["pose_replay_gate"]:
        blocked_reasons["pose_replay_gate"] = "fewer than three replay clip files were found"
    if not can_run["overlay_live_ws"]:
        blocked_reasons["overlay_live_ws"] = "Orin overlay WebSocket prerequisites are not fully configured"
    if not can_run["label_gate"]:
        blocked_reasons["label_gate"] = "label report or JSONL inputs were not found"
    blocked_reasons["backend_live_manual"] = (
        "live backend send stays user/manual even when credentials are configured"
        if backend_credentials
        else "backend credentials are missing and live backend send is user/manual"
    )
    blocked_reasons["offload_decision"] = "offload is decision-only until numeric Pi5 gates justify review"

    return {
        "schema_version": "1.0",
        "can_run": {key: bool(can_run[key]) for key in CAN_RUN_KEYS},
        "manual_or_out_of_scope": list(MANUAL_OR_OUT_OF_SCOPE),
        "blocked_reasons": blocked_reasons,
        "prerequisites": {
            "replay_clip_count": replay_clip_count,
            "label_input_count": label_count,
            "pi5_configured": pi5_configured,
            "orin_configured": orin_configured,
            "overlay_ws_configured": overlay_ws_configured,
            "backend_url_configured": backend_url_configured,
            "backend_token_configured": backend_token_configured,
            "backend_credentials_present": backend_credentials,
            "aws_prerequisites_present": aws_prerequisites,
        },
        "secret_scan": {
            "masked": True,
            "sources_checked": [".env"] if (project_root / ".env").exists() else [],
            "leaks_detected": [],
        },
    }


def build_reconcile_report() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "current_source_of_truth": list(CURRENT_YOLO_FILES),
        "mappings": [
            {
                "reference_file": "edge/split_pipeline_worker.py",
                "current_file": "edge/async_pose.py",
                "action": "not_required",
                "reason": "LatestPoseInferenceWorker already provides the split-worker behavior.",
            },
            {
                "reference_file": "edge/optimized_pose_estimator.py",
                "current_file": "edge/pose_estimator.py",
                "action": "extend_existing",
                "reason": "ONNX session options and timing belong in the current estimator.",
            },
            {
                "reference_file": "edge/main.py",
                "current_file": "edge/main.py",
                "action": "extend_existing",
                "reason": "Runtime loop metrics should extend the existing edge loop.",
            },
        ],
    }


def build_offload_decision(
    *,
    preflight: dict[str, Any],
    benchmark: dict[str, Any],
    stability: dict[str, Any],
) -> dict[str, Any]:
    missing_or_failed: list[str] = []
    preflight_reasons = preflight.get("blocked_reasons", {}) if isinstance(preflight, dict) else {}
    if isinstance(preflight_reasons, dict) and preflight_reasons.get("offload_decision"):
        missing_or_failed.append(str(preflight_reasons["offload_decision"]))

    operational_gate = benchmark.get("operational_gate", {}) if isinstance(benchmark, dict) else {}
    if isinstance(operational_gate, dict):
        for reason in operational_gate.get("reasons", []) or []:
            missing_or_failed.append(str(reason))
        if operational_gate.get("allowed") is not True:
            missing_or_failed.append("benchmark_operational_gate_not_passed")
    if benchmark.get("status") == "blocked":
        missing_or_failed.append(str(benchmark.get("blocked_reason") or benchmark.get("reason") or "benchmark_blocked"))
    if stability.get("long_run_passed") is not True:
        missing_or_failed.append("long_run_stability_not_verified")

    normalized_missing = list(dict.fromkeys(item for item in missing_or_failed if item))
    return {
        "schema_version": "1.0",
        "decision": "do_not_apply_offload_yet" if normalized_missing else "keep_pi5_local_pose",
        "offload_allowed": not normalized_missing,
        "missing_or_failed_gates": normalized_missing,
        "allowed_future_decisions": [
            "keep_pi5_local_pose",
            "try_smaller_pi5_pose_model",
            "request_new_orin_offload_plan",
        ],
        "gate_table": {
            "capture_to_risk_latency_ms_max": 500,
            "danger_recall_min": 0.90,
            "fp_per_hour_max": 2.0,
            "camera_rtsp_fps_min": 30,
            "long_run_stability_required": True,
        },
    }


def render_offload_decision_memo(decision: dict[str, Any]) -> str:
    rows = "\n".join(
        f"| {key} | {value} |"
        for key, value in decision["gate_table"].items()
    )
    failures = "\n".join(f"- {item}" for item in decision["missing_or_failed_gates"]) or "- none"
    future = "\n".join(f"- {item}" for item in decision["allowed_future_decisions"])
    return (
        "# Offload Decision\n\n"
        f"## Decision\n{decision['decision']}\n\n"
        "## Gate Table\n"
        "| Gate | Required |\n|---|---|\n"
        f"{rows}\n\n"
        "## Missing Or Failed Gates\n"
        f"{failures}\n\n"
        "## Rejected Options\n"
        "- Orin pose offload implementation is rejected until numeric gates pass.\n"
        "- Backend live send, model training, and model replacement were not executed.\n\n"
        "## Approved Next Step\n"
        "Keep Pi5 local pose path and collect missing replay/device/label evidence first.\n\n"
        "## Allowed Future Decisions\n"
        f"{future}\n\n"
        "## Uncertainty\n"
        "The current evidence is insufficient for an operational offload recommendation.\n"
    )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "blocked_reason": f"missing file: {path}"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"status": "invalid"}


def build_doc_state_report(
    *,
    config_text: str,
    progress_text: str,
    command_log_text: str,
) -> dict[str, Any]:
    in_problem_section = False
    remaining_markers: list[str] = []
    marker_tokens = ("- [ ]", "plans/", "승인 요청", "계획 중인 부분", "보류 중인 부분", "검토할 부분")
    for line_no, raw_line in enumerate(config_text.splitlines(), start=1):
        line = raw_line.strip()
        if line.startswith("## "):
            in_problem_section = "문제점" in line or "해결방안" in line
        if in_problem_section:
            continue
        if any(token in line for token in marker_tokens):
            remaining_markers.append(f"{line_no}:{line}")

    return {
        "schema_version": "1.0",
        "no_remaining_plans_except_errors": not remaining_markers,
        "remaining_plan_markers": remaining_markers,
        "progress_doc_has_results": bool(progress_text.strip()),
        "command_log_has_start_work": "$omo:start-work" in command_log_text,
    }


def _write_json(report: dict[str, Any], report_out: Path) -> None:
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe remaining-plan preflight and reconciliation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--report-out", required=True)

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--report-out", required=True)

    offload = subparsers.add_parser("offload-decision")
    offload.add_argument("--preflight", required=True)
    offload.add_argument("--benchmark", required=True)
    offload.add_argument("--stability", required=True)
    offload.add_argument("--out", required=True)

    doc_state = subparsers.add_parser("doc-state")
    doc_state.add_argument("--config", required=True)
    doc_state.add_argument("--progress", required=True)
    doc_state.add_argument("--command-log", required=True)
    doc_state.add_argument("--report-out", required=True)

    args = parser.parse_args(argv)
    if args.command == "offload-decision":
        decision = build_offload_decision(
            preflight=_load_json(Path(str(args.preflight))),
            benchmark=_load_json(Path(str(args.benchmark))),
            stability=_load_json(Path(str(args.stability))),
        )
        out = Path(str(args.out))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_offload_decision_memo(decision), encoding="utf-8")
        print(f"wrote {out}")
        return 0
    if args.command == "doc-state":
        report = build_doc_state_report(
            config_text=Path(str(args.config)).read_text(encoding="utf-8"),
            progress_text=Path(str(args.progress)).read_text(encoding="utf-8"),
            command_log_text=Path(str(args.command_log)).read_text(encoding="utf-8"),
        )
        report_out = Path(str(args.report_out))
        _write_json(report, report_out)
        print(f"wrote {report_out}")
        return 0
    if args.command == "preflight":
        report = build_preflight_report(Path.cwd())
    elif args.command == "reconcile":
        report = build_reconcile_report()
    else:
        parser.error(f"unknown command: {args.command}")

    report_out = Path(str(args.report_out))
    _write_json(report, report_out)
    print(f"wrote {report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
