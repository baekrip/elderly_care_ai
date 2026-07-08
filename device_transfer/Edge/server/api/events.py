from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.database import get_session
from server.db.models import RiskEventRecord
from shared.protocol import ClipRequestEvent, RiskAssessmentEvent
from shared.time_utils import utc_iso_from_ms, utc_iso_now


router = APIRouter(tags=["events"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/api/risk-events")
async def create_risk_event(payload: RiskAssessmentEvent, request: Request, session: SessionDep) -> dict[str, str]:
    capture_ts = payload.capture_ts or utc_iso_from_ms(payload.timestamp_ms)
    analysis_ts = payload.analysis_ts or utc_iso_now()
    session.add(
        RiskEventRecord(
            event_id=payload.event_id,
            camera_id=payload.camera_id,
            timestamp_ms=payload.timestamp_ms,
            level=payload.level,
            label=payload.label,
            source=payload.source,
            model_name=payload.model_name,
            metadata_json=payload.metadata | {"capture_ts": capture_ts, "analysis_ts": analysis_ts},
            clip_status="requested",
        )
    )
    await session.commit()

    clip_request = ClipRequestEvent(
        event_id=payload.event_id,
        camera_id=payload.camera_id,
        timestamp_ms=payload.timestamp_ms,
        capture_ts=capture_ts,
        analysis_ts=analysis_ts,
        label=payload.label,
        pre_clip_ms=payload.pre_clip_ms,
        post_clip_ms=payload.post_clip_ms,
        clip_start_ms=payload.timestamp_ms - payload.pre_clip_ms,
        clip_end_ms=payload.timestamp_ms + payload.post_clip_ms,
        clip_duration_ms=payload.pre_clip_ms + payload.post_clip_ms,
        storage_policy="segment_ring",
        reason=f"{payload.level}:{payload.label}",
    )
    await request.app.state.edge_hub.send_to_camera(payload.camera_id, clip_request.model_dump(mode="json"))
    return {"status": "requested"}


@router.get("/api/risk-events")
async def list_risk_events(session: SessionDep, limit: int = 50) -> list[dict]:
    result = await session.execute(select(RiskEventRecord).order_by(desc(RiskEventRecord.created_at)).limit(limit))
    return [
        {
            "event_id": row.event_id,
            "camera_id": row.camera_id,
            "timestamp_ms": row.timestamp_ms,
            "level": row.level,
            "label": row.label,
            "source": row.source,
            "model_name": row.model_name,
            "metadata": row.metadata_json,
            "clip_status": row.clip_status,
        }
        for row in result.scalars()
    ]


@router.websocket("/ws/edges/{camera_id}")
async def edge_socket(websocket: WebSocket, camera_id: str) -> None:
    hub = websocket.app.state.edge_hub
    await hub.connect(camera_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(camera_id, websocket)
