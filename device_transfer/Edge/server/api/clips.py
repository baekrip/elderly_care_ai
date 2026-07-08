from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.database import get_session
from server.db.models import RiskEventRecord, VideoClipRecord
from server.services.backend_clip_uploader import BackendClipUploader, ClipBackendUploadRequest
from server.services.backend_forwarder import config_from_project_config
from shared.api_key_auth import read_api_key_auth_config


router = APIRouter(prefix="/api", tags=["clips"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def validate_clip_upload_api_key(request: Request) -> None:
    config = request.app.state.config
    auth_config = read_api_key_auth_config(
        config,
        "media_server",
        enabled_field="clip_upload_auth_enabled",
        api_key_field="clip_upload_api_key",
        api_key_env_field="clip_upload_api_key_env",
        api_key_header_field="clip_upload_api_key_header",
    )
    if not auth_config.enabled:
        return
    if not auth_config.api_key:
        raise HTTPException(status_code=503, detail="clip_upload_api_key_not_configured")
    if request.headers.get(auth_config.api_key_header) != auth_config.api_key:
        raise HTTPException(status_code=401, detail="invalid_clip_upload_api_key")


def resolve_clip_upload_path(video_root: Path, clip_id: str, camera_id: str, filename: str | None) -> Path:
    safe_camera_id = _safe_path_component(camera_id, "camera_id")
    raw_name = filename or "clip.mp4"
    safe_filename = _safe_path_component(raw_name, "filename")
    root = video_root.resolve()
    file_path = (root / safe_camera_id / f"{clip_id}_{safe_filename}").resolve()
    if root != file_path and root not in file_path.parents:
        raise HTTPException(status_code=400, detail="invalid_clip_upload_path")
    return file_path


def _safe_path_component(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."}:
        raise HTTPException(status_code=400, detail=f"invalid_{field_name}")
    path = Path(text)
    if path.name != text or any(separator in text for separator in ("/", "\\")):
        raise HTTPException(status_code=400, detail=f"invalid_{field_name}")
    return text


@router.post("/clips/upload")
async def upload_clip(
    request: Request,
    session: SessionDep,
    file: UploadFile = File(...),
    event_id: str = Form(""),
    camera_id: str = Form(...),
    timestamp_ms: int = Form(...),
    started_at_ms: int = Form(...),
    ended_at_ms: int = Form(...),
    duration_ms: int = Form(...),
    label: str = Form(""),
    privacy_blur: str = Form(""),
    related_alert_id: int | None = Form(None),
) -> dict[str, object]:
    validate_clip_upload_api_key(request)
    existing_response = await _existing_clip_response_for_event_id(session, event_id)
    if existing_response is not None:
        return existing_response
    archive = request.app.state.result_archive
    video_root = Path(request.app.state.config["storage"]["video_dir"])
    file_path = resolve_clip_upload_path(video_root, uuid.uuid4().hex, camera_id, file.filename)
    clip_id = file_path.name.split("_", 1)[0]
    storage_dir = file_path.parent
    storage_dir.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(await file.read())
    file_path, stored_status = _apply_server_side_blur_if_needed(request, file_path, privacy_blur)

    backend_forward = _forward_clip_to_backend_if_enabled(
        request,
        file_path=file_path,
        event_id=event_id,
        camera_id=camera_id,
        timestamp_ms=timestamp_ms,
        duration_ms=duration_ms,
        label=label,
        related_alert_id=related_alert_id or _related_alert_id_for_event_id(request.app.state, event_id),
    )

    session.add(
        VideoClipRecord(
            clip_id=clip_id,
            event_id=event_id or None,
            camera_id=camera_id,
            file_path=str(file_path),
            timestamp_ms=timestamp_ms,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            duration_ms=duration_ms,
            label=label or None,
        )
    )
    if event_id:
        await session.execute(
            update(RiskEventRecord).where(RiskEventRecord.event_id == event_id).values(clip_status="uploaded")
        )
    await session.commit()
    archive.write_clip_result(
        {
            "clip_id": clip_id,
            "event_id": event_id,
            "camera_id": camera_id,
            "file_path": str(file_path),
            "timestamp_ms": timestamp_ms,
            "started_at_ms": started_at_ms,
            "ended_at_ms": ended_at_ms,
            "duration_ms": duration_ms,
            "label": label,
            "status": stored_status,
            "privacy_blur": "face_blur" if stored_status == "uploaded_blurred_on_server" else privacy_blur,
            "backend_forward": backend_forward,
        }
    )
    return {"clip_id": clip_id, "file_path": str(file_path), "backend_forward": backend_forward}


async def _existing_clip_response_for_event_id(session: AsyncSession, event_id: str) -> dict[str, object] | None:
    if not event_id:
        return None
    result = await session.execute(
        select(VideoClipRecord)
        .where(VideoClipRecord.event_id == event_id)
        .order_by(VideoClipRecord.id.asc())
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        return None
    return {
        "clip_id": existing.clip_id,
        "file_path": existing.file_path,
        "backend_forward": {
            "enabled": True,
            "ok": True,
            "duplicate": True,
            "local_event_id": event_id,
        },
    }


def _apply_server_side_blur_if_needed(request: Request, file_path: Path, privacy_blur: str) -> tuple[Path, str]:
    media_cfg = request.app.state.config.get("media_server", {}) or {}
    if not bool(media_cfg.get("face_blur_on_upload", False)):
        return file_path, "uploaded"
    if privacy_blur == "face_blur":
        return file_path, "uploaded"
    blurred_path = file_path.with_name(f"{file_path.stem}_blurred{file_path.suffix}")
    try:
        from edge.clip_blur import blur_video_file

        blur_video_file(file_path, blurred_path)
    except Exception as exc:
        file_path.unlink(missing_ok=True)
        blurred_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"clip_blur_failed: {exc}") from exc
    file_path.unlink(missing_ok=True)
    return blurred_path, "uploaded_blurred_on_server"


def _forward_clip_to_backend_if_enabled(
    request: Request,
    *,
    file_path: Path,
    event_id: str,
    camera_id: str,
    timestamp_ms: int,
    duration_ms: int,
    label: str,
    related_alert_id: int | None = None,
) -> dict[str, object]:
    media_cfg = request.app.state.config.get("media_server", {}) or {}
    if not bool(media_cfg.get("backend_clip_forward_enabled", True)):
        return {"enabled": False}
    backend_cfg = config_from_project_config(request.app.state.config)
    if not backend_cfg.enabled:
        return {"enabled": False}
    patient_id = str(request.app.state.config.get("patient", {}).get("patient_id", "P001"))
    backend = request.app.state.config.get("backend", {}) or {}
    device_key = str(backend.get("device_key") or camera_id)
    result = BackendClipUploader(backend_cfg).upload_clip(
        ClipBackendUploadRequest(
            file_path=file_path,
            patient_id=patient_id,
            device_key=device_key,
            event_type=label or "fall_detected",
            occurred_at=_occurred_at_from_ms(timestamp_ms),
            duration_sec=max(float(duration_ms) / 1000.0, 1.0),
            related_alert_id=related_alert_id,
        )
    )
    return {
        "enabled": True,
        "ok": result.ok,
        "step": result.step,
        "clip_id": result.clip_id,
        "s3_key": result.s3_key,
        "upload_url_status": result.upload_url_status,
        "s3_put_status": result.s3_put_status,
        "confirm_status": result.confirm_status,
        "retry_pending": result.retry_pending,
        "local_event_id": event_id,
        "related_alert_id": related_alert_id,
    }


def _related_alert_id_for_event_id(app_state: object, event_id: str) -> int | None:
    if not event_id:
        return None
    mapping = getattr(app_state, "backend_alert_ids_by_event_id", None)
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(event_id)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _occurred_at_from_ms(timestamp_ms: int) -> str:
    from shared.time_utils import utc_iso_from_ms

    return utc_iso_from_ms(int(timestamp_ms))


@router.get("/clips")
async def list_clips(session: SessionDep, camera_id: str | None = None, limit: int = 50) -> list[dict]:
    query = select(VideoClipRecord).order_by(desc(VideoClipRecord.created_at)).limit(limit)
    if camera_id:
        query = query.where(VideoClipRecord.camera_id == camera_id)
    result = await session.execute(query)
    return [
        {
            "clip_id": row.clip_id,
            "event_id": row.event_id,
            "camera_id": row.camera_id,
            "file_path": row.file_path,
            "timestamp_ms": row.timestamp_ms,
            "started_at_ms": row.started_at_ms,
            "ended_at_ms": row.ended_at_ms,
            "duration_ms": row.duration_ms,
            "label": row.label,
        }
        for row in result.scalars()
    ]


@router.get("/clips/{clip_id}")
async def get_clip(clip_id: str, session: SessionDep) -> dict:
    result = await session.execute(select(VideoClipRecord).where(VideoClipRecord.clip_id == clip_id))
    clip = result.scalar_one_or_none()
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return {
        "clip_id": clip.clip_id,
        "event_id": clip.event_id,
        "camera_id": clip.camera_id,
        "file_path": clip.file_path,
        "timestamp_ms": clip.timestamp_ms,
        "started_at_ms": clip.started_at_ms,
        "ended_at_ms": clip.ended_at_ms,
        "duration_ms": clip.duration_ms,
        "label": clip.label,
    }
