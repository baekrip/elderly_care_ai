from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CameraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    camera_id: str
    room_id: str | None = None
    display_name: str | None = None
    stream_url: str | None = None
    created_at: datetime


class RiskEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    camera_id: str
    timestamp_ms: int
    level: str
    label: str
    clip_status: str
    created_at: datetime


class VideoClipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clip_id: str
    event_id: str | None = None
    camera_id: str
    file_path: str
    timestamp_ms: int
    started_at_ms: int
    ended_at_ms: int
    duration_ms: int
    label: str | None = None
    created_at: datetime
