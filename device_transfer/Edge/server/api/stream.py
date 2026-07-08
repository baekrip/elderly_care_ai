from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.database import get_session
from server.db.models import CameraRecord


router = APIRouter(prefix="/api", tags=["stream"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/streams/{camera_id}")
async def get_stream_info(camera_id: str, request: Request, session: SessionDep) -> dict:
    camera = await session.get(CameraRecord, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    template = request.app.state.config["streams"].get("public_hls_template", "")
    public_hls_url = template.format(camera_id=camera_id) if template else None
    return {
        "camera_id": camera.camera_id,
        "display_name": camera.display_name,
        "room_id": camera.room_id,
        "stream_url": camera.stream_url,
        "public_hls_url": public_hls_url,
    }
