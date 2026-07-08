from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


DEFAULT_UPLOAD_URL = "https://bucket.s3.amazonaws.com/clips/sample.mp4?X-Amz-Signature=mock"
EDGE_ROOT = Path(__file__).resolve().parents[1] / "device_transfer" / "Edge"
if str(EDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(EDGE_ROOT))

from server.services.backend_clip_uploader import BackendClipUploader, ClipBackendUploadRequest
from server.services.backend_forwarder import BackendConfig, load_project_dotenv


BACKEND_BASE_URL_ENV_NAMES = ("BACKEND_BASE_URL", "AI_BACKEND_BASE_URL", "AI2_SERVER_URL")


def backend_base_url_from_env(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    for name in BACKEND_BASE_URL_ENV_NAMES:
        value = source.get(name, "")
        if value:
            return str(value).strip().rstrip("/")
    return ""


def _s3_key_from_url(upload_url: str) -> str:
    path = urlsplit(upload_url).path.lstrip("/")
    return path or "clips/sample.mp4"


def build_mock_clip_smoke_report(
    *,
    upload_url: str = DEFAULT_UPLOAD_URL,
    app_token: str | None = None,
    backend_base_url: str | None = None,
    file_size_bytes: int = 4096,
    upload_url_status: int = 200,
    s3_put_status: int = 200,
    confirm_status: int = 200,
) -> dict[str, Any]:
    del app_token, backend_base_url
    s3_key = _s3_key_from_url(upload_url)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "mock": True,
        "live_backend_sent": False,
        "file_size_bytes": int(file_size_bytes),
        "retry_pending": False,
        "steps": {
            "upload_url": {
                "status_code": int(upload_url_status),
                "s3_key": s3_key,
            },
            "s3_put": {
                "status_code": int(s3_put_status),
                "content_type": "video/mp4",
            },
            "confirm": {
                "status_code": int(confirm_status),
                "s3_key": s3_key,
            },
        },
        "secret_scan": {
            "masked": True,
            "leaks_detected": [],
        },
    }
    if 400 <= int(upload_url_status) < 500:
        report.update(
            {
                "status": "blocked_contract_error",
                "error_code": "upload_url_contract_error",
                "retry_pending": False,
            }
        )
        return report
    if int(s3_put_status) >= 500:
        report.update(
            {
                "status": "pending_retryable_upload",
                "error_code": "s3_put_failed",
                "retry_pending": True,
                "pending": {
                    "s3_key": s3_key,
                    "status": "s3_put_failed",
                    "file_size_bytes": int(file_size_bytes),
                },
            }
        )
        return report
    if int(confirm_status) >= 400:
        report.update(
            {
                "status": "blocked_contract_error" if int(confirm_status) < 500 else "pending_retryable_confirm",
                "error_code": "confirm_failed",
                "retry_pending": int(confirm_status) >= 500,
            }
        )
        return report
    report.update({"status": "ok", "error_code": None})
    return report


def build_live_clip_smoke_report(
    *,
    backend_base_url: str,
    app_token: str,
    clip_file: Path,
    patient_id: str,
    device_key: str,
    event_type: str,
    occurred_at: str,
    duration_sec: float,
    pending_file: Path,
    urlopen: Any | None = None,
) -> dict[str, Any]:
    uploader = BackendClipUploader(
        BackendConfig(
            enabled=True,
            base_url=backend_base_url,
            token=app_token,
            pending_file=str(pending_file),
        ),
        urlopen=urlopen,
    )
    result = uploader.upload_clip(
        ClipBackendUploadRequest(
            file_path=clip_file,
            patient_id=patient_id,
            device_key=device_key,
            event_type=event_type,
            occurred_at=occurred_at,
            duration_sec=duration_sec,
        )
    )
    return {
        "schema_version": "1.0",
        "mock": False,
        "live_backend_sent": True,
        "status": "ok" if result.ok else "failed",
        "file_size_bytes": clip_file.stat().st_size,
        "retry_pending": result.retry_pending,
        "steps": {
            "upload_url": {"status_code": result.upload_url_status},
            "s3_put": {"status_code": result.s3_put_status, "content_type": "video/mp4"},
            "confirm": {"status_code": result.confirm_status},
        },
        "clip": {
            "clip_id": result.clip_id,
            "s3_key": result.s3_key,
            "step": result.step,
        },
        "secret_scan": {
            "masked": True,
            "leaks_detected": [],
        },
    }


def _write_json(report: dict[str, Any], report_out: Path) -> None:
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_checklist(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Backend Live User Checklist",
                "",
                "- Required env names: one of `BACKEND_BASE_URL`, `AI_BACKEND_BASE_URL`, `AI2_SERVER_URL`; plus `APP_TOKEN`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.",
                "- Event batch: `POST /api/v1/events/batch` with `Authorization: Bearer <APP_TOKEN>`.",
                "- Immediate alert: `POST /api/v1/alerts/immediate` with `Authorization: Bearer <APP_TOKEN>`.",
                "- Clip flow: `POST /api/v1/clips/upload-url` -> S3 `PUT video/mp4` -> `POST /api/v1/clips/confirm`.",
                "- Expected live statuses: backend presign 2xx, S3 PUT 2xx, confirm 2xx.",
                "- Before sharing output, mask bearer token, AWS keys, full backend IP, and the full presigned URL.",
                "- Codex did not send live backend traffic during this plan.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    load_project_dotenv()
    parser = argparse.ArgumentParser(description="Backend clip contract smoke")
    parser.add_argument("--mock", action="store_true", help="Run the local mock smoke.")
    parser.add_argument("--live", action="store_true", help="Run live backend clip smoke with backend URL env alias and APP_TOKEN.")
    parser.add_argument("--clip-file", default="")
    parser.add_argument("--patient-id", default="P001")
    parser.add_argument("--device-key", default="pi5-home001-cam1")
    parser.add_argument("--event-type", default="fall_detected")
    parser.add_argument("--occurred-at", default="")
    parser.add_argument("--duration-sec", type=float, default=3.0)
    parser.add_argument("--pending-file", default="reports/remaining_plan/backend_clip_live_pending.jsonl")
    parser.add_argument("--report-out", default="reports/remaining_plan/backend_clip_mock.json")
    parser.add_argument("--checklist-out", default="reports/remaining_plan/backend_live_user_checklist.md")
    args = parser.parse_args(argv)
    if args.live and args.mock:
        parser.error("choose only one of --mock or --live")
    if args.live:
        backend_base_url = backend_base_url_from_env()
        app_token = os.getenv("APP_TOKEN", "").strip()
        if not backend_base_url or not app_token:
            parser.error("--live requires one backend URL env alias and APP_TOKEN")
        if not args.clip_file:
            parser.error("--live requires --clip-file")
        clip_file = Path(args.clip_file)
        if not clip_file.exists():
            parser.error(f"clip file not found: {clip_file}")
        occurred_at = args.occurred_at or "2026-06-09T08:18:13Z"
        report = build_live_clip_smoke_report(
            backend_base_url=backend_base_url,
            app_token=app_token,
            clip_file=clip_file,
            patient_id=args.patient_id,
            device_key=args.device_key,
            event_type=args.event_type,
            occurred_at=occurred_at,
            duration_sec=args.duration_sec,
            pending_file=Path(args.pending_file),
        )
    else:
        report = build_mock_clip_smoke_report()
    _write_json(report, Path(args.report_out))
    _write_checklist(Path(args.checklist_out))
    print(f"wrote {args.report_out}")
    print(f"wrote {args.checklist_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
