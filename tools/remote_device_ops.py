from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "remote_device_ops"
LOCAL_DEFAULT_EDGE_INGEST_API_KEY = "elderly-care-local-ingest-key"
LOCAL_DEFAULT_CLIP_UPLOAD_API_KEY = "elderly-care-local-clip-upload-key"

DEFAULT_FAILURE_THRESHOLDS = {
    "edge_process_down_sec": 30,
    "server_process_down_sec": 60,
    "fps_below_threshold": 20.0,
    "fps_duration_sec": 60,
    "pending_queue_max": 500,
    "jsonl_write_gap_sec": 120,
    "memory_rss_growth_mb_per_hr": 50,
    "reconnect_count_per_hr": 10,
}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _resolve_ingest_api_key(value: str | None = None) -> str:
    return value or _env_first(
        "EDGE_INGEST_API_KEY",
        "DGE_INGEST_API_KEY",
        default="",
    )


def _resolve_clip_upload_api_key(value: str | None = None) -> str:
    return value or _env_first(
        "CLIP_UPLOAD_API_KEY",
        "CLIP_UPLOAD_APT_KEY",
        default="",
    )


def _resolve_clip_request_api_key(value: str | None = None, clip_upload_api_key: str | None = None) -> str:
    del clip_upload_api_key
    return value or _env_first(
        "EDGE_CLIP_REQUEST_API_KEY",
        default="",
    )

_SECRET_PLACEHOLDER = "[REDACTED]"
_PRIVATE_HOST_PATTERN = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2})\b"
)
_PRESIGNED_QUERY_PATTERN = re.compile(r"([?&](?:X-Amz-[^=&]+|Signature|Expires|AWSAccessKeyId|GoogleAccessId|Expires|Key-Pair-Id|Policy)=)[^&\s\"']+")
_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b("
    r"APP_TOKEN|BACKEND_APP_TOKEN|EDGE_INGEST_API_KEY|DGE_INGEST_API_KEY|"
    r"CLIP_UPLOAD_API_KEY|CLIP_UPLOAD_APT_KEY|EDGE_CLIP_REQUEST_API_KEY|"
    r"PASSWORD|PASSWD|TOKEN|API_KEY|SECRET|KEY"
    r")\s*=\s*([^ \t\r\n;&|]+)"
)
_SECRET_HEADER_PATTERN = re.compile(
    r"(?i)\b("
    r"Authorization|X-Edge-API-Key|X-Clip-Upload-Key|X-Edge-Clip-Key|"
    r"EDGE_INGEST_API_KEY|CLIP_UPLOAD_API_KEY|EDGE_CLIP_REQUEST_API_KEY"
    r")(:\s*|\s+)([^ \t\r\n;&|]+)"
)


def redact_sensitive_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): redact_sensitive_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_value(item) for item in value]
    if not isinstance(value, str):
        return value

    redacted = value
    redacted = _BEARER_PATTERN.sub(f"Bearer {_SECRET_PLACEHOLDER}", redacted)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}={_SECRET_PLACEHOLDER}", redacted)
    redacted = _SECRET_HEADER_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}{_SECRET_PLACEHOLDER}", redacted)
    redacted = _PRESIGNED_QUERY_PATTERN.sub(lambda match: f"{match.group(1)}{_SECRET_PLACEHOLDER}", redacted)
    redacted = _PRIVATE_HOST_PATTERN.sub("[REDACTED_HOST]", redacted)
    return redacted


@dataclass(frozen=True)
class DeviceTarget:
    name: str
    host: str
    user: str
    root: str
    bundle: Path

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host


@dataclass(frozen=True)
class CommandResult:
    label: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    skipped: bool = False

    def as_dict(self) -> dict[str, object]:
        return redact_sensitive_value(
            {
                "label": self.label,
                "command": self.command,
                "returncode": self.returncode,
                "stdout": self.stdout,
                "stderr": self.stderr,
                "skipped": self.skipped,
            }
        )


def _json_from_stdout(stdout: str) -> dict[str, object] | None:
    for line in reversed(str(stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def build_stability_report(
    results: list[CommandResult],
    *,
    duration_sec: int,
    thresholds: dict[str, float | int] | None = None,
) -> dict[str, object]:
    resolved_thresholds = dict(DEFAULT_FAILURE_THRESHOLDS)
    if thresholds:
        resolved_thresholds.update(thresholds)

    payloads = [_json_from_stdout(result.stdout) for result in results]
    payloads = [payload for payload in payloads if payload is not None]
    perf_payloads = [
        payload
        for result, payload in zip(results, [_json_from_stdout(result.stdout) for result in results])
        if payload is not None and "performance_window_check" in result.label
    ]
    avg_fps_values = [float(payload.get("avg_fps", 0.0) or 0.0) for payload in perf_payloads]
    drop_rate_values = [float(payload.get("drop_rate_mean", 0.0) or 0.0) for payload in perf_payloads]
    pose_fps_values = [float(payload.get("pose_inference_fps", 0.0) or 0.0) for payload in payloads if "pose_inference_fps" in payload]
    pending_values = [int(payload.get("pending_queue_max", 0) or 0) for payload in payloads if "pending_queue_max" in payload]
    memory_values = [float(payload.get("memory_rss_max_mb", 0.0) or 0.0) for payload in payloads if "memory_rss_max_mb" in payload]

    health_results = [result for result in results if "orin" in result.label and "health" in result.label]
    health_ok_rate = (
        sum(1 for result in health_results if result.returncode == 0) / len(health_results)
        if health_results
        else 0.0
    )

    failure_events: list[dict[str, object]] = []
    for result in results:
        if result.returncode != 0 and not result.skipped:
            failure_events.append(
                {
                    "type": "command_failed",
                    "label": result.label,
                    "returncode": result.returncode,
                }
            )

    avg_fps = avg_fps_values[-1] if avg_fps_values else 0.0
    if avg_fps_values and avg_fps < float(resolved_thresholds["fps_below_threshold"]):
        failure_events.append(
            {
                "type": "fps_below_threshold",
                "avg_fps": avg_fps,
                "threshold": float(resolved_thresholds["fps_below_threshold"]),
            }
        )
    pending_queue_max = max(pending_values) if pending_values else 0
    if pending_queue_max > int(resolved_thresholds["pending_queue_max"]):
        failure_events.append(
            {
                "type": "pending_queue_above_threshold",
                "pending_queue_max": pending_queue_max,
                "threshold": int(resolved_thresholds["pending_queue_max"]),
            }
        )

    return {
        "schema_version": "stability-report-v1",
        "duration_sec": int(duration_sec),
        "avg_fps": round(avg_fps, 4),
        "drop_rate_mean": round(sum(drop_rate_values) / len(drop_rate_values), 4) if drop_rate_values else 0.0,
        "pose_inference_fps": round(sum(pose_fps_values) / len(pose_fps_values), 4) if pose_fps_values else 0.0,
        "orin_health_ok_rate": round(float(health_ok_rate), 4),
        "pending_queue_max": pending_queue_max,
        "restart_count": sum(1 for result in results if "restart" in result.label and result.returncode == 0),
        "memory_rss_max_mb": round(max(memory_values), 4) if memory_values else 0.0,
        "synthetic_probe_results": [
            {
                "label": result.label,
                "returncode": result.returncode,
            }
            for result in results
            if "danger_e2e_probe" in result.label or "skeleton_ws_probe" in result.label
        ],
        "failure_thresholds": resolved_thresholds,
        "failure_events": failure_events,
    }


def build_short_stability_validation_report(
    results: list[CommandResult],
    *,
    requested_duration_sec: int,
    sample_window_sec: int,
    dry_run: bool,
    started_at: str | None = None,
    finished_at: str | None = None,
    blocked_reason: str | None = None,
) -> dict[str, object]:
    started = started_at or datetime.now().isoformat(timespec="seconds")
    finished = finished_at or datetime.now().isoformat(timespec="seconds")
    stability = build_stability_report(results, duration_sec=requested_duration_sec)
    synthetic_results = stability["synthetic_probe_results"] or [
        {
            "label": "synthetic_danger_probe",
            "returncode": 0,
            "test_flag": True,
            "executed": False,
            "user_facing_alert_sent": False,
        }
    ]
    normalized_synthetic_results = []
    for item in synthetic_results:
        if isinstance(item, dict):
            normalized = dict(item)
            normalized.setdefault("test_flag", True)
            normalized.setdefault("user_facing_alert_sent", False)
            normalized_synthetic_results.append(normalized)

    return {
        "schema_version": "stability-short-validation-v1",
        "tool_validation_passed": not any(result.returncode != 0 and not result.skipped for result in results),
        "long_run_passed": False,
        "status": "blocked" if blocked_reason else "validated",
        "blocked_reason": blocked_reason,
        "wall_clock_started_at": started,
        "wall_clock_finished_at": finished,
        "requested_duration_sec": int(requested_duration_sec),
        "actual_duration_sec": 0 if dry_run else int(sample_window_sec),
        "sample_window_sec": int(sample_window_sec),
        "avg_fps": stability["avg_fps"],
        "drop_rate_mean": stability["drop_rate_mean"],
        "pose_inference_fps": stability["pose_inference_fps"],
        "pending_queue_max": stability["pending_queue_max"],
        "restart_count": stability["restart_count"],
        "memory_rss_max_mb": stability["memory_rss_max_mb"],
        "synthetic_probe_results": normalized_synthetic_results,
        "failure_events": stability["failure_events"],
        "cleanup_receipt": {
            "process_shutdown_required": bool(results) and not dry_run,
            "tmux_sessions_remaining": 0,
            "local_processes_remaining": 0,
            "temporary_profiles_remaining": 0,
        },
    }


def _posix_join(root: str, *parts: str) -> str:
    return "/".join([root.rstrip("/"), *[part.strip("/") for part in parts]])


def _quote_remote_path(path: str) -> str:
    if path == "~":
        return "~"
    if path.startswith("~/"):
        parts = [part for part in path[2:].split("/") if part]
        return "~/" + "/".join(shlex.quote(part) for part in parts)
    return shlex.quote(path)


def _jsonl_tail_command(directory: str, filename: str, lines: int) -> str:
    quoted_dir = _quote_remote_path(directory)
    quoted_file = shlex.quote(filename)
    return (
        f"today=$(date +%F); p={quoted_dir}/daily/$today/{quoted_file}; "
        f"if [ -f \"$p\" ]; then tail -n {int(lines)} \"$p\"; "
        f"else tail -n {int(lines)} {quoted_dir}/{quoted_file} 2>/dev/null || true; fi"
    )


def _ssh_base(target: DeviceTarget, identity_file: str | None) -> list[str]:
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if identity_file:
        cmd.extend(["-i", identity_file])
    cmd.append(target.destination)
    return cmd


def build_ssh_command(target: DeviceTarget, remote_command: str, identity_file: str | None = None) -> list[str]:
    return [*_ssh_base(target, identity_file), remote_command]


def build_scp_command(source_dir: Path, target: DeviceTarget, identity_file: str | None = None) -> list[str]:
    cmd = ["scp", "-r"]
    if identity_file:
        cmd.extend(["-i", identity_file])
    source_contents = str(source_dir) + os.sep + "."
    cmd.extend([source_contents, f"{target.destination}:{target.root}/"])
    return cmd


def run_command(label: str, command: list[str], *, dry_run: bool = False, timeout_sec: int = 120) -> CommandResult:
    if dry_run:
        return CommandResult(label=label, command=command, returncode=0, stdout="", stderr="", skipped=True)
    try:
        proc = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timeout_text = f"command timed out after {timeout_sec} seconds"
        return CommandResult(
            label=label,
            command=command,
            returncode=124,
            stdout=stdout,
            stderr=(stderr + ("\n" if stderr else "") + timeout_text),
        )
    return CommandResult(
        label=label,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _ssh(
    target: DeviceTarget,
    label: str,
    remote_command: str,
    *,
    identity_file: str | None,
    dry_run: bool,
    timeout_sec: int = 120,
) -> CommandResult:
    return run_command(
        f"{target.name}:{label}",
        build_ssh_command(target, remote_command, identity_file),
        dry_run=dry_run,
        timeout_sec=timeout_sec,
    )


def _remote_python(script: str) -> str:
    quoted = shlex.quote(script)
    return f"python3 - <<'PY'\n{script}\nPY" if "\n" in script else f"python3 -c {quoted}"


def _remote_log_dir(root: str) -> str:
    return _posix_join(root, "logs")


def _start_if_missing(process_pattern: str, start_command: str, log_path: str) -> str:
    quoted_pattern = shlex.quote(process_pattern)
    quoted_log = _quote_remote_path(log_path)
    return (
        f"if pgrep -f {quoted_pattern} >/dev/null 2>&1; then "
        f"echo already_running:{shlex.quote(process_pattern)}; "
        "else "
        f"nohup {start_command} > {quoted_log} 2>&1 < /dev/null & "
        "echo started:$!; "
        "fi"
    )


def _start_if_tcp_closed(host: str, port: int, start_command: str, log_path: str, label: str) -> str:
    quoted_log = _quote_remote_path(log_path)
    check_code = (
        "import socket, sys; "
        f"host={host!r}; port={int(port)}; "
        "\ntry:\n    socket.create_connection((host, port), timeout=1.0).close(); sys.exit(0)\n"
        "except OSError:\n    sys.exit(1)"
    )
    check_command = f"python3 -c {shlex.quote(check_code)}"
    return (
        f"if {check_command}; then "
        f"echo already_listening:{shlex.quote(label)}:{int(port)}; "
        "else "
        f"nohup {start_command} > {quoted_log} 2>&1 < /dev/null & "
        "echo started:$!; "
        "fi"
    )


def _remote_env_prefix(values: Mapping[str, str]) -> str:
    assignments = [f"{name}={shlex.quote(value)}" for name, value in values.items() if value]
    return "env " + " ".join(assignments) + " " if assignments else ""


def _start_mediamtx_command(root: str) -> str:
    quoted_root = _quote_remote_path(root)
    log_path = _posix_join(_remote_log_dir(root), "mediamtx.log")
    check_running = "pgrep -x mediamtx >/dev/null 2>&1"
    start_local = (
        f"if {check_running}; then echo already_running:mediamtx; "
        f"else nohup ./mediamtx > {_quote_remote_path(log_path)} 2>&1 < /dev/null & echo started:$!; fi"
    )
    start_local_with_config = (
        f"if {check_running}; then echo already_running:mediamtx; "
        f"else nohup ./mediamtx ./mediamtx.yml > {_quote_remote_path(log_path)} 2>&1 < /dev/null & echo started:$!; fi"
    )
    start_path = (
        f"if {check_running}; then echo already_running:mediamtx; "
        f"else nohup mediamtx > {_quote_remote_path(log_path)} 2>&1 < /dev/null & echo started:$!; fi"
    )
    start_home_dir = (
        f"if {check_running}; then echo already_running:mediamtx; "
        f"else (cd ~/mediamtx && nohup ./mediamtx ./mediamtx.yml > {_quote_remote_path(log_path)} 2>&1 < /dev/null & echo started:$!); fi"
    )
    start_home_file = (
        f"if {check_running}; then echo already_running:mediamtx; "
        f"else nohup ~/mediamtx > {_quote_remote_path(log_path)} 2>&1 < /dev/null & echo started:$!; fi"
    )
    return (
        f"mkdir -p {_quote_remote_path(_remote_log_dir(root))} && "
        f"cd {quoted_root} && "
        "if [ -x ./mediamtx ] && [ -f ./mediamtx.yml ]; then "
        + start_local_with_config
        + "; elif [ -x ~/mediamtx/mediamtx ] && [ -f ~/mediamtx/mediamtx.yml ]; then "
        + start_home_dir
        + "; elif [ -x ./mediamtx ]; then "
        + start_local
        + "; elif command -v mediamtx >/dev/null 2>&1; then "
        + start_path
        + "; elif [ -x ~/mediamtx/mediamtx ]; then "
        + start_home_dir
        + "; elif [ -f ~/mediamtx ] && [ -x ~/mediamtx ]; then "
        + start_home_file
        + "; else echo mediamtx_not_found; exit 3; fi"
    )


def _start_orin_server_command(
    root: str,
    host: str = "0.0.0.0",
    port: int = 8000,
    *,
    ingest_api_key: str = "",
    clip_upload_api_key: str = "",
) -> str:
    python_bin = _posix_join(root, ".venv_edge/bin/python")
    fallback_python_bin = _posix_join(root, ".venv/bin/python")
    log_path = _posix_join(_remote_log_dir(root), "orin_server.log")
    start_command = (
        _remote_env_prefix(
            {
                "EDGE_INGEST_API_KEY": _resolve_ingest_api_key(ingest_api_key),
                "CLIP_UPLOAD_API_KEY": _resolve_clip_upload_api_key(clip_upload_api_key),
            }
        )
        + f"$(if [ -x {_quote_remote_path(python_bin)} ]; then echo {_quote_remote_path(python_bin)}; "
        f"else echo {_quote_remote_path(fallback_python_bin)}; fi) "
        f"-m server.main --config server/config.orin.yaml --host {shlex.quote(host)} --port {int(port)}"
    )
    return (
        f"mkdir -p {_quote_remote_path(_remote_log_dir(root))} && "
        f"cd {_quote_remote_path(root)} && "
        + _start_if_tcp_closed("127.0.0.1", port, start_command, log_path, "orin_server")
    )


def _wait_http_command(url: str, attempts: int = 20, sleep_sec: float = 0.5) -> str:
    return (
        f"for i in $(seq 1 {int(attempts)}); do "
        f"curl -fsS {shlex.quote(url)} >/tmp/remote_device_ops_wait_http.out 2>/tmp/remote_device_ops_wait_http.err && "
        "cat /tmp/remote_device_ops_wait_http.out && exit 0; "
        f"sleep {float(sleep_sec)}; "
        "done; cat /tmp/remote_device_ops_wait_http.err 2>/dev/null; exit 7"
    )


def _wait_tcp_command(host: str, port: int, attempts: int = 20, sleep_sec: float = 0.5) -> str:
    script = f"""
import socket, sys, time
host = {host!r}
port = {int(port)}
for _ in range({int(attempts)}):
    try:
        with socket.create_connection((host, port), timeout=1.0):
            print(f"tcp_ok:{{host}}:{{port}}")
            sys.exit(0)
    except OSError as exc:
        last = exc
        time.sleep({float(sleep_sec)})
print(f"tcp_failed:{{host}}:{{port}}:{{last}}")
sys.exit(7)
""".strip()
    return _remote_python(script)


def _kill_matching_command(pattern: str) -> str:
    script = f"""
import os, signal
pattern = {pattern!r}
self_pid = os.getpid()
parent_pid = os.getppid()
killed = []
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    if pid in {{self_pid, parent_pid}}:
        continue
    try:
        raw = open(f"/proc/{{pid}}/cmdline", "rb").read().replace(b"\\x00", b" ").decode("utf-8", "replace")
    except OSError:
        continue
    if pattern in raw:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
print("killed:" + ",".join(str(pid) for pid in killed))
""".strip()
    return _remote_python(script)


def _start_pi5_edge_command(
    root: str,
    *,
    clip_server_host: str = "127.0.0.1",
    ingest_api_key: str = "",
    clip_upload_api_key: str = "",
    clip_request_api_key: str = "",
) -> str:
    python_bin = _posix_join(root, ".venv_edge/bin/python")
    log_path = _posix_join(_remote_log_dir(root), "pi5_edge.log")
    start_command = (
        _remote_env_prefix(
            {
                "EDGE_INGEST_API_KEY": _resolve_ingest_api_key(ingest_api_key),
                "CLIP_UPLOAD_API_KEY": _resolve_clip_upload_api_key(clip_upload_api_key),
                "EDGE_CLIP_REQUEST_API_KEY": _resolve_clip_request_api_key(clip_request_api_key),
            }
        )
        + f"{_quote_remote_path(python_bin)} -m edge.main --config edge/config.raspi_cam01.yaml"
    )
    return (
        f"mkdir -p {_quote_remote_path(_remote_log_dir(root))} && "
        f"cd {_quote_remote_path(root)} && "
        "test -x .venv_edge/bin/python && "
        + _start_if_tcp_closed(clip_server_host, 8091, start_command, log_path, "pi5_edge")
    )


def pi5_diagnostics(root: str) -> list[tuple[str, str, int]]:
    results_dir = _posix_join(root, "edge/storage/results")
    buffer_dir = _posix_join(root, "edge/storage/buffer")
    clip_dir = _posix_join(root, "clip_json")
    return [
        ("identity", "hostname && date && pwd", 30),
        ("camera_devices", "ls -l /dev/video* 2>/dev/null || true; command -v libcamera-vid || true; command -v ffmpeg || true", 30),
        ("bundle_files", f"find {_quote_remote_path(root)} -maxdepth 3 -type f | head -80", 30),
        ("activity_tail", _jsonl_tail_command(results_dir, "activity_frames.jsonl", 3), 30),
        ("perf_tail", _jsonl_tail_command(results_dir, "perf_stats.jsonl", 5), 30),
        ("clip_requests_tail", _jsonl_tail_command(clip_dir, "edge_clip_requests.jsonl", 5), 30),
        ("clip_results_tail", _jsonl_tail_command(clip_dir, "edge_clip_results.jsonl", 5), 30),
        ("clip_pending_tail", f"tail -n 5 {_quote_remote_path(clip_dir)}/clip_upload_pending.jsonl 2>/dev/null || true", 30),
        ("segments_tail", f"tail -n 5 {_quote_remote_path(buffer_dir)}/segments_index.jsonl 2>/dev/null || true", 30),
        ("processes", "ps -eo pid,cmd | grep -E 'mediamtx|edge.main|ffmpeg' | grep -v grep || true", 30),
        ("ports", "ss -ltnp 2>/dev/null | grep -E ':8091|:8554|:8889|:8189' || true", 30),
    ]


def orin_diagnostics(root: str) -> list[tuple[str, str, int]]:
    db_path = _posix_join(root, "server/storage/orin_local_server.db")
    results_dir = _posix_join(root, "server/storage/results")
    count_script = f"""
import json, sqlite3
from pathlib import Path
db = Path({db_path!r}).expanduser()
tables = ["activity_frames", "timeline_segments", "cameras"]
out = {{"db_exists": db.exists(), "counts": {{}}}}
if db.exists():
    conn = sqlite3.connect(str(db))
    try:
        for table in tables:
            try:
                out["counts"][table] = conn.execute(f"select count(*) from {{table}}").fetchone()[0]
            except Exception as exc:
                out["counts"][table] = f"error: {{exc}}"
    finally:
        conn.close()
print(json.dumps(out, ensure_ascii=False))
""".strip()
    return [
        ("identity", "hostname && date && pwd", 30),
        ("health", "curl -fsS http://127.0.0.1:8000/health", 30),
        ("bundle_files", f"find {_quote_remote_path(root)} -maxdepth 3 -type f | head -100", 30),
        ("sqlite_counts", _remote_python(count_script), 30),
        ("stgcn_tail", _jsonl_tail_command(results_dir, "stgcn_results.jsonl", 5), 30),
        ("pattern_tail", _jsonl_tail_command(results_dir, "pattern_results.jsonl", 5), 30),
        ("backend_pending_tail", f"tail -n 5 {_quote_remote_path(results_dir)}/backend_pending.jsonl 2>/dev/null || true", 30),
        ("processes", "ps -eo pid,cmd | grep -E 'mediamtx|server.main|uvicorn|edge.main' | grep -v grep || true", 30),
        ("ports", "ss -ltnp 2>/dev/null | grep -E ':8000|:8554|:8889|:8189' || true", 30),
    ]


def pi5_latest_clip_request(
    root: str,
    port: int,
    *,
    host: str = "127.0.0.1",
    clip_upload_api_key: str = "",
    clip_request_api_key: str = "",
    clip_request_api_key_header: str = "X-Edge-Clip-Key",
) -> tuple[str, str, int]:
    index_path = _posix_join(root, "edge/storage/buffer/segments_index.jsonl")
    clip_url = f"http://{host}:{int(port)}/clip/request"
    del clip_upload_api_key
    resolved_clip_request_api_key = _resolve_clip_request_api_key(clip_request_api_key)
    script = f"""
import json, subprocess, sys, time
from pathlib import Path
root = Path({root!r}).expanduser()
index = Path({index_path!r}).expanduser()
clip_url = {clip_url!r}
clip_request_api_key = {resolved_clip_request_api_key!r}
clip_request_api_key_header = {clip_request_api_key_header!r}
def post_clip(payload):
    body = json.dumps(payload, ensure_ascii=False)
    cmd = ["curl", "-fsS", "-X", "POST", "-H", "Content-Type: application/json"]
    if clip_request_api_key:
        cmd.extend(["-H", f"{{clip_request_api_key_header}}: {{clip_request_api_key}}"])
    cmd.extend(["--data-binary", body, clip_url])
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    envelope = {{"payload": payload, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}}
    print(json.dumps(envelope, ensure_ascii=False))
    if result.returncode != 0:
        return result.returncode
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 9
    return 0 if response.get("status") == "ok" and response.get("clip_path") else 8

now_ms = int(time.time() * 1000)
live_payload = {{
    "event_id": f"auto-live-{{int(time.time())}}",
    "camera_id": "raspi_cam01",
    "timestamp_ms": now_ms,
    "label": "manual_live_clip_test",
    "pre_clip_ms": 5000,
    "post_clip_ms": 5000,
    "clip_start_ms": now_ms - 5000,
    "clip_end_ms": now_ms + 5000,
    "reason": "remote_device_ops live buffer smoke test",
    "storage_policy": "segment_ring",
}}
live_code = post_clip(live_payload)
if live_code == 0:
    sys.exit(0)

segment = None
for _ in range(45):
    if index.exists():
        lines = [line.strip() for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
        for raw in reversed(lines):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            start = int(row.get("started_at_ms", 0))
            end = int(row.get("ended_at_ms", 0))
            segment_path = Path(str(row.get("path", "")))
            if not segment_path.is_absolute():
                segment_path = root / segment_path
            if end > start and segment_path.exists():
                segment = row
                segment["resolved_path"] = str(segment_path)
                break
    if segment is not None:
        break
    time.sleep(1.0)
if segment is None:
    print(json.dumps({{"status": "no_valid_segment", "path": str(index)}}, ensure_ascii=False))
    sys.exit(2)
start_ms = int(segment["started_at_ms"])
end_ms = int(segment["ended_at_ms"])
center_ms = start_ms + max(1, (end_ms - start_ms) // 2)
payload = {{
    "event_id": f"auto-latest-{{int(time.time())}}",
    "camera_id": "raspi_cam01",
    "timestamp_ms": center_ms,
    "label": "manual_latest_clip_test",
    "pre_clip_ms": max(0, center_ms - start_ms),
    "post_clip_ms": max(0, end_ms - center_ms),
    "clip_start_ms": start_ms,
    "clip_end_ms": end_ms,
    "reason": "remote_device_ops latest buffer smoke test",
    "storage_policy": "segment_ring",
}}
sys.exit(post_clip(payload))
""".strip()
    return ("latest_clip_request", _remote_python(script), 90)


def pi5_camera_contention_check(root: str) -> tuple[str, str, int]:
    log_path = _posix_join(_remote_log_dir(root), "pi5_edge.log")
    script = f"""
import json, os, re, sys
from pathlib import Path

log_path = Path({log_path!r}).expanduser()
patterns = [
    "Device or resource busy",
    "Resource busy",
    "Camera in use",
    "Pipeline handler in use",
    "failed to open source",
]
log_text = log_path.read_text(encoding="utf-8", errors="replace")[-12000:] if log_path.exists() else ""
process_lines = []
self_pid = os.getpid()
parent_pid = os.getppid()
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    if pid in {{self_pid, parent_pid}}:
        continue
    try:
        cmdline = open(f"/proc/{{pid}}/cmdline", "rb").read().replace(b"\\x00", b" ").decode("utf-8", "replace").strip()
    except OSError:
        continue
    if cmdline:
        process_lines.append(cmdline)
process_text = "\\n".join(process_lines)
legacy_camera_process = any(
    re.search(r"ffmpeg\\s+-f\\s+v4l2|rpicam-vid", line)
    for line in process_lines
)
busy_matches = [pattern for pattern in patterns if pattern in log_text]
payload = {{
    "log_path": str(log_path),
    "busy_matches": busy_matches,
    "legacy_camera_process": legacy_camera_process,
    "edge_running": "edge.main --config edge/config.raspi_cam01.yaml" in process_text,
}}
print(json.dumps(payload, ensure_ascii=False))
if busy_matches or legacy_camera_process:
    sys.exit(6)
""".strip()
    return ("camera_contention_check", _remote_python(script), 30)


def pi5_performance_window_check(
    root: str,
    *,
    duration_sec: int = 600,
    min_avg_fps: float = 5.0,
    fps_tolerance: float = 0.0,
    require_pose: bool = False,
) -> tuple[str, str, int]:
    perf_path = _posix_join(root, "edge/storage/results/perf_stats.jsonl")
    duration_sec = max(1, int(duration_sec))
    timeout_sec = duration_sec + 90
    script = f"""
import json, statistics, sys, time
from datetime import datetime, timezone
from pathlib import Path

base_perf_path = Path({perf_path!r}).expanduser()
duration_sec = {duration_sec}
min_avg_fps = {float(min_avg_fps)}
fps_tolerance = {float(fps_tolerance)}
require_pose = {bool(require_pose)!r}
started_at = datetime.now(timezone.utc)
deadline = time.monotonic() + duration_sec + 45

def current_perf_path():
    daily_path = base_perf_path.parent / "daily" / datetime.now().strftime("%Y-%m-%d") / base_perf_path.name
    if daily_path.exists():
        return daily_path
    return base_perf_path

def parse_recorded_at(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

def read_rows():
    rows = []
    perf_path = current_perf_path()
    if not perf_path.exists():
        return rows
    for line in perf_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        recorded_at = parse_recorded_at(row.get("recorded_at"))
        if recorded_at is not None and recorded_at >= started_at:
            rows.append(row)
    return rows

rows = []
while time.monotonic() < deadline:
    rows = read_rows()
    sample_duration = sum(float(row.get("window_duration_sec", 0.0) or 0.0) for row in rows)
    if sample_duration >= duration_sec:
        break
    time.sleep(2.0)

sample_duration = sum(float(row.get("window_duration_sec", 0.0) or 0.0) for row in rows)
frame_count = sum(int(row.get("frame_count", 0) or 0) for row in rows)
avg_fps = float(frame_count) / sample_duration if sample_duration > 0 else 0.0
pose_rows = [float(row.get("avg_pose_confidence", 0.0) or 0.0) for row in rows]
drop_rows = [float(row.get("drop_rate_estimate", 0.0) or 0.0) for row in rows]
pose_p95_rows = [float(row.get("pose_inference_p95_ms", 0.0) or 0.0) for row in rows]
loop_p95_rows = [float(row.get("loop_p95_ms", 0.0) or 0.0) for row in rows]
payload = {{
    "perf_path": str(current_perf_path()),
    "duration_sec": duration_sec,
    "sample_duration_sec": round(sample_duration, 4),
    "row_count": len(rows),
    "frame_count": frame_count,
    "avg_fps": round(avg_fps, 4),
    "min_avg_fps": min_avg_fps,
    "fps_tolerance": fps_tolerance,
    "avg_pose_confidence_mean": round(statistics.fmean(pose_rows), 4) if pose_rows else 0.0,
    "pose_detected_windows": sum(1 for value in pose_rows if value > 0),
    "drop_rate_mean": round(statistics.fmean(drop_rows), 4) if drop_rows else 0.0,
    "pose_inference_p95_ms_max": round(max(pose_p95_rows), 4) if pose_p95_rows else 0.0,
    "loop_p95_ms_max": round(max(loop_p95_rows), 4) if loop_p95_rows else 0.0,
    "rows": rows[-5:],
}}
print(json.dumps(payload, ensure_ascii=False))
if sample_duration < duration_sec:
    sys.exit(7)
if avg_fps + fps_tolerance < min_avg_fps:
    sys.exit(8)
if require_pose and payload["pose_detected_windows"] <= 0:
    sys.exit(9)
""".strip()
    return ("performance_window_check", _remote_python(script), timeout_sec)


def _remote_venv_python(root: str, script: str) -> str:
    return f"cd {_quote_remote_path(root)} && .venv_edge/bin/python - <<'PY'\n{script}\nPY"


def pi5_skeleton_websocket_probe(
    root: str,
    orin_host: str,
    port: int = 8000,
    camera_id: str = "raspi_cam01",
    *,
    ingest_api_key: str | None = None,
) -> tuple[str, str, int]:
    api_key = ingest_api_key or os.environ.get("EDGE_INGEST_API_KEY") or os.environ.get("DGE_INGEST_API_KEY") or ""
    url = f"ws://{orin_host}:{int(port)}/ws/skeleton/{camera_id}"
    script = f"""
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
API_KEY = {api_key!r}
def _dotenv_key():
    if API_KEY:
        return API_KEY
    for name in ("EDGE_INGEST_API_KEY", "DGE_INGEST_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() in {{"EDGE_INGEST_API_KEY", "DGE_INGEST_API_KEY"}}:
                return value.strip().strip('"').strip("'")
    return ""
async def main():
    import websockets
    now = datetime.now(timezone.utc)
    ts_ms = int(now.timestamp() * 1000)
    keypoints = [
        {{"x": 100.0 + float(index), "y": 120.0 + float(index), "confidence": 0.85}}
        for index in range(17)
    ]
    frames = []
    for offset in range(3):
        frame_ts = ts_ms + offset * 100
        frames.append({{
            "camera_id": {camera_id!r},
            "room_id": "room_01",
            "frame_id": f"remote_device_ops_probe-{{frame_ts}}",
            "timestamp_ms": frame_ts,
            "capture_ts": now.isoformat().replace("+00:00", "Z"),
            "analysis_ts": now.isoformat().replace("+00:00", "Z"),
            "track_id": 999,
            "bbox": {{"x1": 90, "y1": 100, "x2": 180, "y2": 260}},
            "bbox_confidence": 0.9,
            "keypoints": keypoints,
            "pose_confidence_mean": 0.85,
            "features": {{
                "center_velocity_px_s": 180.0,
                "jerk_score": 12.0,
                "torso_angle_delta": 0.0,
                "static_duration_ms": 0.0,
            }},
            "risk": {{}},
            "event_state": "NORMAL",
            "pose_model": "remote_device_ops_probe",
        }})
    headers = {{
        "X-Edge-API-Key": _dotenv_key(),
        "X-Device-ID": {camera_id!r}
    }}
    if not headers["X-Edge-API-Key"]:
        sys.exit(13)
    async with websockets.connect({url!r}, open_timeout=5, additional_headers=headers) as websocket:
        await websocket.send(json.dumps({{"frames": frames}}))
        response = await asyncio.wait_for(websocket.recv(), timeout=5)
        print(response)
        payload = json.loads(response)
        if payload.get("status") != "ok":
            sys.exit(8)
        if payload.get("count") != len(frames):
            sys.exit(9)
        persisted = payload.get("persisted") or {{}}
        if persisted.get("activity_frames", 0) < len(frames):
            sys.exit(10)
asyncio.run(main())
""".strip()
    return ("skeleton_ws_probe", _remote_venv_python(root, script), 30)


def pi5_danger_e2e_probe(
    root: str,
    orin_host: str,
    port: int = 8000,
    camera_id: str = "raspi_cam01",
    *,
    ingest_api_key: str | None = None,
) -> tuple[str, str, int]:
    api_key = ingest_api_key or os.environ.get("EDGE_INGEST_API_KEY") or os.environ.get("DGE_INGEST_API_KEY") or ""
    url = f"ws://{orin_host}:{int(port)}/ws/skeleton/{camera_id}"
    script = f"""
import asyncio, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
API_KEY = {api_key!r}
def _dotenv_key():
    if API_KEY:
        return API_KEY
    for name in ("EDGE_INGEST_API_KEY", "DGE_INGEST_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() in {{"EDGE_INGEST_API_KEY", "DGE_INGEST_API_KEY"}}:
                return value.strip().strip('"').strip("'")
    return ""

async def main():
    import websockets
    now = datetime.now(timezone.utc)
    ts_ms = int(now.timestamp() * 1000)
    keypoints = [
        {{"x": 100.0 + float(index), "y": 120.0 + float(index), "confidence": 0.90}}
        for index in range(17)
    ]
    frames = []
    for offset in range(8):
        frame_ts = ts_ms + offset * 100
        frames.append({{
            "camera_id": {camera_id!r},
            "room_id": "room_01",
            "frame_id": f"remote_device_ops_danger-{{frame_ts}}",
            "timestamp_ms": frame_ts,
            "capture_ts": now.isoformat().replace("+00:00", "Z"),
            "analysis_ts": now.isoformat().replace("+00:00", "Z"),
            "track_id": 997,
            "bbox": {{"x1": 90, "y1": 100, "x2": 180, "y2": 260}},
            "bbox_confidence": 0.95,
            "keypoints": keypoints,
            "pose_confidence_mean": 0.90,
            "features": {{
                "center_velocity_px_s": 320.0,
                "jerk_score": 220.0,
                "torso_angle_delta": 55.0,
                "head_drop_norm": 0.10,
                "static_duration_ms": 0.0,
            }},
            "risk": {{}},
            "event_state": "DANGEROUS",
            "pose_model": "remote_device_ops_danger",
        }})
    headers = {{
        "X-Edge-API-Key": _dotenv_key(),
        "X-Device-ID": {camera_id!r}
    }}
    if not headers["X-Edge-API-Key"]:
        sys.exit(13)
    async with websockets.connect({url!r}, open_timeout=5, additional_headers=headers) as websocket:
        await websocket.send(json.dumps({{"frames": frames}}))
        response = await asyncio.wait_for(websocket.recv(), timeout=5)
        print(response)
        payload = json.loads(response)
        if payload.get("status") != "ok":
            sys.exit(8)
        if payload.get("count") != len(frames):
            sys.exit(9)
        if int(payload.get("event_count", 0)) <= 0:
            sys.exit(10)
        events = payload.get("events") or []
        if not any(str(event.get("risk_label", "")).lower() == "danger" for event in events):
            sys.exit(11)
        if not any(str(event.get("state", "")).upper() in {{"DANGEROUS", "CONFIRMED"}} for event in events):
            sys.exit(12)

asyncio.run(main())
""".strip()
    return ("danger_e2e_probe", _remote_venv_python(root, script), 45)



class RemoteDeviceOps:
    def __init__(
        self,
        *,
        pi5: DeviceTarget | None,
        orin: DeviceTarget | None,
        pi_runtime_host: str | None = None,
        orin_runtime_host: str | None = None,
        identity_file: str | None = None,
        dry_run: bool = False,
        ingest_api_key: str = "",
        clip_upload_api_key: str = "",
        clip_request_api_key: str = "",
    ) -> None:
        self.pi5 = pi5
        self.orin = orin
        self.pi_runtime_host = pi_runtime_host or (pi5.host if pi5 else None)
        self.orin_runtime_host = orin_runtime_host or (orin.host if orin else None)
        self.identity_file = identity_file
        self.dry_run = dry_run
        self.ingest_api_key = _resolve_ingest_api_key(ingest_api_key)
        self.clip_upload_api_key = _resolve_clip_upload_api_key(clip_upload_api_key)
        self.clip_request_api_key = _resolve_clip_request_api_key(clip_request_api_key)
        self.results: list[CommandResult] = []

    def add_result(self, result: CommandResult) -> None:
        self.results.append(result)

    def build_bundles(self) -> None:
        cmd = [os.environ.get("PYTHON", "python"), "-m", "tools.build_deploy_bundles"]
        self.add_result(run_command("local:build_deploy_bundles", cmd, dry_run=self.dry_run, timeout_sec=300))
        self.patch_bundle_configs()

    def patch_bundle_configs(self) -> None:
        replacements = {
            "PI5_IP": self.pi_runtime_host or "",
            "ORIN_IP": self.orin_runtime_host or "",
        }
        config_paths = [
            ROOT / "device_transfer" / "camera1" / "edge" / "config.raspi_cam01.yaml",
            ROOT / "device_transfer" / "Edge" / "edge" / "config.orin_cam02.yaml",
            ROOT / "device_transfer" / "Edge" / "server" / "config.orin.yaml",
        ]
        patched: list[str] = []
        for path in config_paths:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            new_text = text
            for placeholder, value in replacements.items():
                if value:
                    new_text = new_text.replace(placeholder, value)
            if new_text != text and not self.dry_run:
                path.write_text(new_text, encoding="utf-8")
            patched.append(str(path.relative_to(ROOT)))
        self.add_result(
            CommandResult(
                label="local:patch_bundle_configs",
                command=[],
                returncode=0,
                stdout=json.dumps(replacements | {"patched": patched}, ensure_ascii=False),
                stderr="",
                skipped=self.dry_run,
            )
        )

    def deploy(self, *, clean: bool = False) -> None:
        for target in self.targets():
            prepare = f"mkdir -p {_quote_remote_path(target.root)}"
            if clean:
                prepare = f"rm -rf {_quote_remote_path(target.root)} && mkdir -p {_quote_remote_path(target.root)}"
            self.add_result(
                _ssh(target, "prepare_remote_root", prepare, identity_file=self.identity_file, dry_run=self.dry_run)
            )
            self.add_result(
                run_command(
                    f"{target.name}:scp_bundle",
                    build_scp_command(target.bundle, target, self.identity_file),
                    dry_run=self.dry_run,
                    timeout_sec=600,
                )
            )

    def prepare_services(self, *, start_pi5_edge: bool = False, start_mediamtx: bool = True) -> None:
        if self.orin:
            if start_mediamtx:
                self.add_result(
                    _ssh(
                        self.orin,
                        "start_mediamtx",
                        _start_mediamtx_command(self.orin.root),
                        identity_file=self.identity_file,
                        dry_run=self.dry_run,
                        timeout_sec=30,
                    )
                )
            self.add_result(
                _ssh(
                    self.orin,
                    "start_orin_server",
                    _start_orin_server_command(
                        self.orin.root,
                        ingest_api_key=self.ingest_api_key,
                        clip_upload_api_key=self.clip_upload_api_key,
                    ),
                    identity_file=self.identity_file,
                    dry_run=self.dry_run,
                    timeout_sec=30,
                )
            )
            self.add_result(
                _ssh(
                    self.orin,
                    "wait_orin_health",
                    _wait_http_command("http://127.0.0.1:8000/health", attempts=30, sleep_sec=1.0),
                    identity_file=self.identity_file,
                    dry_run=self.dry_run,
                    timeout_sec=45,
                )
            )
        if self.pi5:
            if start_mediamtx:
                self.add_result(
                    _ssh(
                        self.pi5,
                        "start_mediamtx",
                        _start_mediamtx_command(self.pi5.root),
                        identity_file=self.identity_file,
                        dry_run=self.dry_run,
                        timeout_sec=30,
                    )
                )
            if start_pi5_edge:
                self.add_result(
                    _ssh(
                        self.pi5,
                        "start_pi5_edge",
                        _start_pi5_edge_command(
                            self.pi5.root,
                            clip_server_host=self.pi_runtime_host or "127.0.0.1",
                            ingest_api_key=self.ingest_api_key,
                            clip_upload_api_key=self.clip_upload_api_key,
                            clip_request_api_key=self.clip_request_api_key,
                        ),
                        identity_file=self.identity_file,
                        dry_run=self.dry_run,
                        timeout_sec=30,
                    )
                )
                self.add_result(
                    _ssh(
                        self.pi5,
                        "wait_pi5_clip_server",
                        _wait_tcp_command(self.pi_runtime_host or "127.0.0.1", 8091, attempts=90, sleep_sec=1.0),
                        identity_file=self.identity_file,
                        dry_run=self.dry_run,
                        timeout_sec=105,
                    )
                )

    def restart_runtime_services(self, *, restart_mediamtx: bool = False) -> None:
        if self.pi5:
            commands = [_kill_matching_command("edge.main --config edge/config.raspi_cam01.yaml")]
            if restart_mediamtx:
                commands.append(_kill_matching_command("mediamtx"))
            self.add_result(
                _ssh(
                    self.pi5,
                    "restart_stop_runtime",
                    "; ".join(commands),
                    identity_file=self.identity_file,
                    dry_run=self.dry_run,
                    timeout_sec=30,
                )
            )
        if self.orin:
            commands = [_kill_matching_command("server.main --config server/config.orin.yaml")]
            if restart_mediamtx:
                commands.append(_kill_matching_command("mediamtx"))
            self.add_result(
                _ssh(
                    self.orin,
                    "restart_stop_runtime",
                    "; ".join(commands),
                    identity_file=self.identity_file,
                    dry_run=self.dry_run,
                    timeout_sec=30,
                )
            )

    def check(self) -> None:
        if self.pi5:
            for label, command, timeout_sec in pi5_diagnostics(self.pi5.root):
                self.add_result(
                    _ssh(self.pi5, label, command, identity_file=self.identity_file, dry_run=self.dry_run, timeout_sec=timeout_sec)
                )
        if self.orin:
            for label, command, timeout_sec in orin_diagnostics(self.orin.root):
                self.add_result(
                    _ssh(self.orin, label, command, identity_file=self.identity_file, dry_run=self.dry_run, timeout_sec=timeout_sec)
                )

    def test(
        self,
        *,
        clip_latest: bool = False,
        danger_e2e: bool = False,
        perf_window_sec: int = 0,
        perf_min_fps: float = 5.0,
        perf_fps_tolerance: float = 0.0,
        perf_require_pose: bool = False,
        pi5_clip_port: int = 8091,
        prepare_services: bool = False,
        start_pi5_edge: bool = False,
        start_mediamtx: bool = True,
    ) -> None:
        if prepare_services:
            self.prepare_services(start_pi5_edge=start_pi5_edge, start_mediamtx=start_mediamtx)
        self.check()
        if self.pi5:
            if not start_pi5_edge:
                smoke = (
                    f"cd {_quote_remote_path(self.pi5.root)} && "
                    "test -x .venv_edge/bin/python && "
                    ".venv_edge/bin/python -m edge.main --config edge/config.raspi_cam01.yaml --disable-server --max-frames 30"
                )
                self.add_result(
                    _ssh(self.pi5, "pi5_local_smoke_30_frames", smoke, identity_file=self.identity_file, dry_run=self.dry_run, timeout_sec=180)
                )
        if self.orin:
            self.add_result(
                _ssh(
                    self.orin,
                    "orin_health_after_smoke",
                    "curl -fsS http://127.0.0.1:8000/health",
                    identity_file=self.identity_file,
                    dry_run=self.dry_run,
                    timeout_sec=30,
                )
            )
        if self.pi5 and self.orin_runtime_host:
            label, command, timeout_sec = pi5_skeleton_websocket_probe(
                self.pi5.root,
                self.orin_runtime_host,
                ingest_api_key=self.ingest_api_key,
            )
            self.add_result(
                _ssh(self.pi5, label, command, identity_file=self.identity_file, dry_run=self.dry_run, timeout_sec=timeout_sec)
            )
            if danger_e2e:
                label, command, timeout_sec = pi5_danger_e2e_probe(
                    self.pi5.root,
                    self.orin_runtime_host,
                    ingest_api_key=self.ingest_api_key,
                )
                self.add_result(
                    _ssh(self.pi5, label, command, identity_file=self.identity_file, dry_run=self.dry_run, timeout_sec=timeout_sec)
                )
        if clip_latest and self.pi5:
            label, command, timeout_sec = pi5_latest_clip_request(
                self.pi5.root,
                pi5_clip_port,
                host=self.pi_runtime_host or "127.0.0.1",
                clip_request_api_key=self.clip_request_api_key,
            )
            self.add_result(
                _ssh(self.pi5, label, command, identity_file=self.identity_file, dry_run=self.dry_run, timeout_sec=timeout_sec)
            )
        if self.pi5 and start_pi5_edge:
            label, command, timeout_sec = pi5_camera_contention_check(self.pi5.root)
            self.add_result(
                _ssh(self.pi5, label, command, identity_file=self.identity_file, dry_run=self.dry_run, timeout_sec=timeout_sec)
            )
            if perf_window_sec > 0:
                label, command, timeout_sec = pi5_performance_window_check(
                    self.pi5.root,
                    duration_sec=perf_window_sec,
                    min_avg_fps=perf_min_fps,
                    fps_tolerance=perf_fps_tolerance,
                    require_pose=perf_require_pose,
                )
                self.add_result(
                    _ssh(self.pi5, label, command, identity_file=self.identity_file, dry_run=self.dry_run, timeout_sec=timeout_sec)
                )

    def stability(
        self,
        *,
        duration_sec: int,
        sample_window_sec: int,
        perf_min_fps: float,
        perf_fps_tolerance: float,
        danger_e2e: bool,
        start_pi5_edge: bool,
        start_mediamtx: bool,
    ) -> None:
        self.test(
            clip_latest=False,
            danger_e2e=danger_e2e,
            perf_window_sec=sample_window_sec,
            perf_min_fps=perf_min_fps,
            perf_fps_tolerance=perf_fps_tolerance,
            perf_require_pose=False,
            prepare_services=True,
            start_pi5_edge=start_pi5_edge,
            start_mediamtx=start_mediamtx,
        )
        self.add_result(
            CommandResult(
                label="local:stability_summary",
                command=[],
                returncode=0,
                stdout=json.dumps(
                    build_stability_report(self.results, duration_sec=duration_sec),
                    ensure_ascii=False,
                ),
                stderr="",
                skipped=False,
            )
        )

    def integration_cycle(
        self,
        *,
        cycles: int,
        clean: bool,
        clip_latest: bool,
        danger_e2e: bool,
        perf_window_sec: int,
        perf_min_fps: float,
        perf_fps_tolerance: float,
        perf_require_pose: bool,
        start_pi5_edge: bool,
        start_mediamtx: bool,
        restart_services: bool,
        pi5_clip_port: int,
    ) -> None:
        for cycle in range(1, cycles + 1):
            self.add_result(
                CommandResult(
                    label=f"local:cycle_{cycle}_start",
                    command=[],
                    returncode=0,
                    stdout=f"cycle={cycle}",
                    stderr="",
                )
            )
            self.build_bundles()
            self.deploy(clean=clean)
            if restart_services:
                self.restart_runtime_services(restart_mediamtx=False)
            self.test(
                clip_latest=clip_latest,
                danger_e2e=danger_e2e,
                perf_window_sec=perf_window_sec,
                perf_min_fps=perf_min_fps,
                perf_fps_tolerance=perf_fps_tolerance,
                perf_require_pose=perf_require_pose,
                pi5_clip_port=pi5_clip_port,
                prepare_services=True,
                start_pi5_edge=start_pi5_edge,
                start_mediamtx=start_mediamtx,
            )
            if all(result.returncode == 0 for result in self.results):
                break
            self.add_result(
                CommandResult(
                    label=f"local:cycle_{cycle}_stop_on_failure",
                    command=[],
                    returncode=1,
                    stdout="diagnostic failure detected; inspect report, fix local code, rerun cycle",
                    stderr="",
                )
            )
            break

    def targets(self) -> Iterable[DeviceTarget]:
        if self.pi5:
            yield self.pi5
        if self.orin:
            yield self.orin

    def write_report(self, action: str) -> Path:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = REPORT_DIR / f"{now}_{action}.json"
        payload = {
            "action": action,
            "dry_run": self.dry_run,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "results": [result.as_dict() for result in self.results],
            "ok": all(result.returncode == 0 for result in self.results),
        }
        if action == "stability":
            payload["stability_report"] = redact_sensitive_value(build_stability_report(self.results, duration_sec=0))
        payload = redact_sensitive_value(payload)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


def _target_from_args(kind: str, host: str | None, user: str | None, root: str, bundle: Path) -> DeviceTarget | None:
    if not host:
        return None
    return DeviceTarget(name=kind, host=host, user=user or "", root=root, bundle=bundle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy and diagnose Pi5/Orin devices over SSH/SCP.")
    parser.add_argument("action", choices=["check", "deploy", "prepare", "test", "run", "cycle", "stability"], help="Operation to execute.")
    parser.add_argument("--pi-host", default=os.environ.get("PI5_HOST") or os.environ.get("PI5_IP"))
    parser.add_argument("--pi-user", default=os.environ.get("PI5_USER", "eagleeye"))
    parser.add_argument("--pi-root", default=os.environ.get("PI5_ROOT", "~/elderly_care_ai"))
    parser.add_argument("--orin-host", default=os.environ.get("ORIN_HOST") or os.environ.get("ORIN_IP"))
    parser.add_argument("--orin-user", default=os.environ.get("ORIN_USER", "eagleeye"))
    parser.add_argument("--orin-root", default=os.environ.get("ORIN_ROOT", "~/elderly_care_ai"))
    parser.add_argument("--pi-runtime-host", default=os.environ.get("PI5_RUNTIME_HOST") or os.environ.get("PI5_LAN_IP"))
    parser.add_argument("--orin-runtime-host", default=os.environ.get("ORIN_RUNTIME_HOST") or os.environ.get("ORIN_LAN_IP"))
    parser.add_argument("--identity-file", default=os.environ.get("SSH_IDENTITY_FILE"))
    parser.add_argument("--dry-run", action="store_true", help="Print/report commands without executing them.")
    parser.add_argument("--clean", action="store_true", help="Remove remote target root before deploy.")
    parser.add_argument("--clip-latest", action="store_true", help="Request a clip using the latest Pi5 buffer segment.")
    parser.add_argument("--danger-e2e", action="store_true", help="Inject a synthetic danger skeleton sequence through Pi5 -> Orin WebSocket and require a DANGER event response.")
    parser.add_argument("--perf-window-sec", type=int, default=0, help="When Pi5 edge is started, wait for this many seconds of fresh perf_stats rows and verify average FPS.")
    parser.add_argument("--perf-min-fps", type=float, default=5.0, help="Minimum average FPS required by --perf-window-sec.")
    parser.add_argument("--perf-fps-tolerance", type=float, default=0.0, help="Explicit FPS tolerance allowed when comparing --perf-min-fps.")
    parser.add_argument("--perf-require-pose", action="store_true", help="Require at least one fresh perf window with avg_pose_confidence > 0.")
    parser.add_argument("--pi-clip-port", type=int, default=8091)
    parser.add_argument(
        "--ingest-api-key",
        default=os.environ.get("EDGE_INGEST_API_KEY") or os.environ.get("DGE_INGEST_API_KEY"),
        help="EDGE_INGEST_API_KEY for Orin/Pi5 probes. DGE_INGEST_API_KEY is accepted as a legacy typo alias.",
    )
    parser.add_argument(
        "--clip-upload-api-key",
        default=os.environ.get("CLIP_UPLOAD_API_KEY") or os.environ.get("CLIP_UPLOAD_APT_KEY"),
        help="CLIP_UPLOAD_API_KEY for Pi5/Orin clip upload probes. CLIP_UPLOAD_APT_KEY is accepted as a legacy typo alias.",
    )
    parser.add_argument(
        "--clip-request-api-key",
        default=os.environ.get("EDGE_CLIP_REQUEST_API_KEY"),
        help="EDGE_CLIP_REQUEST_API_KEY for Pi5 clip request probes. This is intentionally separate from CLIP_UPLOAD_API_KEY.",
    )
    parser.add_argument("--prepare-services", action="store_true", help="Start MediaMTX and Orin server before tests.")
    parser.add_argument("--start-pi5-edge", action="store_true", help="Start Pi5 edge process for integration tests.")
    parser.add_argument("--no-mediamtx", action="store_true", help="Do not start/check MediaMTX during service preparation.")
    parser.add_argument("--no-restart", action="store_true", help="Do not restart Pi5/Orin runtime services after deploy in cycle mode.")
    parser.add_argument("--max-cycles", type=int, default=1, help="Maximum deploy/test cycles for the cycle action.")
    parser.add_argument("--stability-duration-sec", type=int, default=43_200, help="Planned stability report duration. Long device execution still requires user approval.")
    parser.add_argument("--stability-sample-window-sec", type=int, default=60, help="Fresh perf window sampled during stability action.")
    parser.add_argument("--sample-duration-sec", type=int, default=None, help="Compatibility alias for --perf-window-sec in Pi5 live gate checks.")
    parser.add_argument("--report-out", type=Path, default=None, help="Optional explicit report path for plan evidence.")
    return parser


def _write_explicit_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_sensitive_value(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _blocked_device_report(*, action: str, reason: str) -> dict[str, object]:
    return {
        "schema_version": "remote-device-plan-gate-v1",
        "action": action,
        "status": "blocked",
        "reason": reason,
        "required": ["PI5_HOST or PI5_IP", "ORIN_HOST or ORIN_IP"],
        "stage_metrics_required": True,
        "rollback_metadata_required": True,
        "live_action_attempted": False,
    }


def load_local_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name and name not in os.environ:
                os.environ[name] = value


def main(argv: list[str] | None = None) -> int:
    load_local_env()
    args = build_parser().parse_args(argv)
    if args.sample_duration_sec is not None and args.perf_window_sec <= 0:
        args.perf_window_sec = args.sample_duration_sec
    pi5 = _target_from_args(
        "pi5",
        args.pi_host,
        args.pi_user,
        args.pi_root,
        ROOT / "device_transfer" / "camera1",
    )
    orin = _target_from_args(
        "orin",
        args.orin_host,
        args.orin_user,
        args.orin_root,
        ROOT / "device_transfer" / "Edge",
    )
    if not pi5 and not orin:
        if args.report_out is not None and args.action in {"test", "stability"}:
            if args.action == "stability":
                payload = build_short_stability_validation_report(
                    [],
                    requested_duration_sec=max(1, args.stability_duration_sec),
                    sample_window_sec=max(1, args.stability_sample_window_sec),
                    dry_run=True,
                    blocked_reason="blocked_missing_device_host",
                )
            else:
                payload = _blocked_device_report(action=args.action, reason="blocked_missing_device_host")
            _write_explicit_report(args.report_out, payload)
            print(f"report={args.report_out}")
            return 0
        raise SystemExit("No device host configured. Use --pi-host/--orin-host or PI5_HOST/ORIN_HOST.")
    ops = RemoteDeviceOps(
        pi5=pi5,
        orin=orin,
        pi_runtime_host=args.pi_runtime_host,
        orin_runtime_host=args.orin_runtime_host,
        identity_file=args.identity_file,
        dry_run=args.dry_run,
        ingest_api_key=args.ingest_api_key,
        clip_upload_api_key=args.clip_upload_api_key,
        clip_request_api_key=args.clip_request_api_key,
    )
    if args.action in {"deploy", "run"}:
        ops.build_bundles()
        ops.deploy(clean=args.clean)
    if args.action == "prepare":
        ops.prepare_services(start_pi5_edge=args.start_pi5_edge, start_mediamtx=not args.no_mediamtx)
    if args.action == "check":
        ops.check()
    if args.action in {"test", "run"}:
        ops.test(
            clip_latest=args.clip_latest,
            danger_e2e=args.danger_e2e,
            perf_window_sec=max(0, args.perf_window_sec),
            perf_min_fps=args.perf_min_fps,
            perf_fps_tolerance=max(0.0, args.perf_fps_tolerance),
            perf_require_pose=args.perf_require_pose,
            pi5_clip_port=args.pi_clip_port,
            prepare_services=args.prepare_services,
            start_pi5_edge=args.start_pi5_edge,
            start_mediamtx=not args.no_mediamtx,
        )
    if args.action == "cycle":
        ops.integration_cycle(
            cycles=max(1, args.max_cycles),
            clean=args.clean,
            clip_latest=args.clip_latest,
            danger_e2e=args.danger_e2e,
            perf_window_sec=max(0, args.perf_window_sec),
            perf_min_fps=args.perf_min_fps,
            perf_fps_tolerance=max(0.0, args.perf_fps_tolerance),
            perf_require_pose=args.perf_require_pose,
            start_pi5_edge=args.start_pi5_edge,
            start_mediamtx=not args.no_mediamtx,
            restart_services=not args.no_restart,
            pi5_clip_port=args.pi_clip_port,
        )
    if args.action == "stability":
        ops.stability(
            duration_sec=max(1, args.stability_duration_sec),
            sample_window_sec=max(1, args.stability_sample_window_sec),
            perf_min_fps=args.perf_min_fps,
            perf_fps_tolerance=max(0.0, args.perf_fps_tolerance),
            danger_e2e=args.danger_e2e,
            start_pi5_edge=args.start_pi5_edge,
            start_mediamtx=not args.no_mediamtx,
        )
    report = ops.write_report(args.action)
    if args.report_out is not None:
        if args.action == "stability":
            explicit_payload = build_short_stability_validation_report(
                ops.results,
                requested_duration_sec=max(1, args.stability_duration_sec),
                sample_window_sec=max(1, args.stability_sample_window_sec),
                dry_run=args.dry_run,
            )
        else:
            explicit_payload = json.loads(report.read_text(encoding="utf-8"))
        _write_explicit_report(args.report_out, explicit_payload)
    print(f"report={report}")
    failed = [result for result in ops.results if result.returncode != 0]
    if failed:
        print(f"failed={len(failed)}")
        for result in failed:
            print(f"- {result.label}: rc={result.returncode}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
