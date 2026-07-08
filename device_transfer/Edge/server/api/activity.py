from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.database import get_session
from server.db.models import ActivityFrameRecord, CameraRecord, TimelineSegmentRecord
from server.services.backend_forwarder import build_timeline_pattern_backend_event, config_from_project_config
from shared.protocol import ActivityFrameBatch, CameraRegistration, TimelineBatch


router = APIRouter(prefix="/api", tags=["activity"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/cameras/register")
async def register_camera(payload: CameraRegistration, session: SessionDep) -> dict[str, str]:
    camera = await session.get(CameraRecord, payload.camera_id)
    if camera is None:
        camera = CameraRecord(
            camera_id=payload.camera_id,
            room_id=payload.room_id,
            display_name=payload.display_name,
            stream_url=payload.stream_url,
            metadata_json=payload.metadata,
        )
        session.add(camera)
    else:
        camera.room_id = payload.room_id
        camera.display_name = payload.display_name
        camera.stream_url = payload.stream_url
        camera.metadata_json = payload.metadata
    await session.commit()
    return {"status": "ok"}


@router.post("/activity/frames")
async def ingest_activity_frames(payload: ActivityFrameBatch, session: SessionDep) -> dict[str, int]:
    for frame in payload.frames:
        session.add(
            ActivityFrameRecord(
                frame_id=frame.frame_id,
                camera_id=frame.camera_id,
                timestamp_ms=frame.timestamp_ms,
                room_id=frame.room_id,
                payload=frame.model_dump(mode="json"),
            )
        )
    await session.commit()
    return {"count": len(payload.frames)}


@router.post("/activity/timeline")
async def ingest_timeline(payload: TimelineBatch, session: SessionDep, request: Request) -> dict[str, int]:
    for segment in payload.segments:
        session.add(
            TimelineSegmentRecord(
                camera_id=segment.camera_id,
                track_id=segment.track_id,
                action_label=segment.action_label,
                action_confidence=segment.action_confidence,
                started_at_ms=segment.started_at_ms,
                ended_at_ms=segment.ended_at_ms,
                duration_ms=segment.duration_ms,
                room_id=segment.room_id,
                summary_features=segment.summary_features,
            )
        )
    await session.commit()
    pattern_count = _analyze_timeline_patterns(payload, request)
    return {"count": len(payload.segments), "pattern_results": pattern_count}


def _analyze_timeline_patterns(payload: TimelineBatch, request: Request) -> int:
    analyzer = getattr(request.app.state, "pattern_analyzer", None)
    archive = getattr(request.app.state, "result_archive", None)
    config = getattr(request.app.state, "config", {})
    if analyzer is None or archive is None or not payload.segments:
        return 0
    patient_id = str(config.get("patient", {}).get("patient_id", "P001"))
    rows = [segment.model_dump(mode="json") for segment in payload.segments]
    window_start_ms = min(int(row["started_at_ms"]) for row in rows)
    window_end_ms = max(int(row["ended_at_ms"]) for row in rows)
    anomalies = analyzer.analyze_window(patient_id, rows, window_start_ms, window_end_ms)
    pattern_payload = {
        "patient_id": patient_id,
        "window_start_ms": window_start_ms,
        "window_end_ms": window_end_ms,
        "segments": rows,
        "anomalies": anomalies,
    }
    backend_result = _submit_timeline_pattern_result(pattern_payload, request)
    if backend_result:
        pattern_payload["backend_forward"] = backend_result
    archive.write_pattern_result(pattern_payload)
    return len(anomalies)


def _submit_timeline_pattern_result(pattern_payload: dict[str, object], request: Request) -> dict[str, object] | None:
    config = getattr(request.app.state, "config", {})
    backend_cfg = config_from_project_config(config)
    if not backend_cfg.enabled:
        return None

    segments = list(pattern_payload.get("segments", []))
    anomalies = list(pattern_payload.get("anomalies", []))
    first_segment = segments[0] if segments and isinstance(segments[0], dict) else {}
    backend_config = config.get("backend", {}) or {}
    device_key = str(backend_config.get("device_key") or first_segment.get("camera_id") or "edge_hub")
    patient_id = str(pattern_payload.get("patient_id") or config.get("patient", {}).get("patient_id", "P001"))
    event = build_timeline_pattern_backend_event(
        device_key=device_key,
        patient_id=patient_id,
        window_start_ms=int(pattern_payload["window_start_ms"]),
        window_end_ms=int(pattern_payload["window_end_ms"]),
        segments=[dict(row) for row in segments if isinstance(row, dict)],
        anomalies=[dict(row) for row in anomalies if isinstance(row, dict)],
    )
    batcher = getattr(request.app.state, "backend_normal_batcher", None)
    if batcher is None:
        return {"event_queued": False, "reason": "backend_normal_batcher_missing"}
    result = batcher.submit(event, force=bool(anomalies))
    return {
        "event_queued": result.response is None,
        "batch_forwarded": result.forwarded,
        "event_type": event["event_type"],
        "forced": bool(anomalies),
    }


@router.get("/activity/timeline/{camera_id}")
async def get_timeline(camera_id: str, session: SessionDep, limit: int = 200) -> list[dict]:
    query = (
        select(TimelineSegmentRecord)
        .where(TimelineSegmentRecord.camera_id == camera_id)
        .order_by(desc(TimelineSegmentRecord.started_at_ms))
        .limit(limit)
    )
    result = await session.execute(query)
    return [
        {
            "track_id": row.track_id,
            "action_label": row.action_label,
            "action_confidence": row.action_confidence,
            "started_at_ms": row.started_at_ms,
            "ended_at_ms": row.ended_at_ms,
            "duration_ms": row.duration_ms,
            "room_id": row.room_id,
            "summary_features": row.summary_features,
        }
        for row in result.scalars()
    ]
