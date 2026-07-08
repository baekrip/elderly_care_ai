from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from server.db.database import Base


JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")


class CameraRecord(Base):
    __tablename__ = "cameras"

    camera_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    room_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_VARIANT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ActivityFrameRecord(Base):
    __tablename__ = "activity_frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    frame_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.camera_id"), index=True)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    room_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON_VARIANT)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TimelineSegmentRecord(Base):
    __tablename__ = "timeline_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.camera_id"), index=True)
    track_id: Mapped[int] = mapped_column(Integer, index=True)
    action_label: Mapped[str] = mapped_column(String(64), index=True)
    action_confidence: Mapped[float]
    started_at_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    ended_at_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    duration_ms: Mapped[int] = mapped_column(BigInteger)
    room_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary_features: Mapped[dict | None] = mapped_column(JSON_VARIANT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RiskEventRecord(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.camera_id"), index=True)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    level: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(64), default="secondary_ai")
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON_VARIANT, nullable=True)
    clip_status: Mapped[str] = mapped_column(String(32), default="requested")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VideoClipRecord(Base):
    __tablename__ = "video_clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clip_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.camera_id"), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    started_at_ms: Mapped[int] = mapped_column(BigInteger)
    ended_at_ms: Mapped[int] = mapped_column(BigInteger)
    duration_ms: Mapped[int] = mapped_column(BigInteger)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
