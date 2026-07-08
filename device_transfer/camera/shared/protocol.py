from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PROTOCOL_VERSION = "2.1.0"


class ProtocolVersionResult(BaseModel):
    expected: str
    actual: str | None = None
    compatible: bool
    status: Literal["compatible", "missing_schema_version", "invalid_schema_version", "major_mismatch"]


def _major_version(value: str) -> int | None:
    try:
        return int(str(value).split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def validate_protocol_version(payload: dict[str, Any], *, expected: str = PROTOCOL_VERSION) -> ProtocolVersionResult:
    actual = payload.get("schema_version")
    if actual is None:
        return ProtocolVersionResult(
            expected=expected,
            actual=None,
            compatible=True,
            status="missing_schema_version",
        )
    actual_major = _major_version(str(actual))
    expected_major = _major_version(expected)
    if actual_major is None or expected_major is None:
        return ProtocolVersionResult(
            expected=expected,
            actual=str(actual),
            compatible=False,
            status="invalid_schema_version",
        )
    if actual_major != expected_major:
        return ProtocolVersionResult(
            expected=expected,
            actual=str(actual),
            compatible=False,
            status="major_mismatch",
        )
    return ProtocolVersionResult(
        expected=expected,
        actual=str(actual),
        compatible=True,
        status="compatible",
    )


class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class BoundingBox(BaseSchema):
    x1: int
    y1: int
    x2: int
    y2: int

    def as_list(self) -> list[int]:
        return [self.x1, self.y1, self.x2, self.y2]


class Keypoint(BaseSchema):
    x: float
    y: float
    confidence: float = Field(ge=0.0, le=1.0)


class PosePerson(BaseSchema):
    track_id: int
    bbox: BoundingBox
    bbox_confidence: float = Field(ge=0.0, le=1.0)
    keypoints: list[Keypoint]
    pose_confidence_mean: float = Field(ge=0.0, le=1.0)
    features: dict[str, Any]
    action_label: str
    action_confidence: float = Field(ge=0.0, le=1.0)
    risk_label: str | None = None
    risk_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    label_source: str | None = None
    xgboost_prob: float | None = Field(default=None, ge=0.0, le=1.0)
    xgboost_tier_ms: float | None = Field(default=None, ge=0.0)
    pose_model: str
    classifier_model: str


class ActivityFrame(BaseSchema):
    camera_id: str
    room_id: str | None = None
    frame_id: str
    timestamp_ms: int
    capture_ts: str | None = None
    analysis_ts: str | None = None
    persons: list[PosePerson]
    pipeline_version: str = "v1"


class ActivityFrameBatch(BaseSchema):
    schema_version: str = PROTOCOL_VERSION
    frames: list[ActivityFrame]


class SkeletonFrame(BaseSchema):
    camera_id: str
    room_id: str | None = None
    frame_id: str
    timestamp_ms: int
    capture_ts: str | None = None
    analysis_ts: str | None = None
    track_id: int
    bbox: BoundingBox
    bbox_confidence: float = Field(ge=0.0, le=1.0)
    keypoints: list[Keypoint]
    pose_confidence_mean: float = Field(ge=0.0, le=1.0)
    features: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    event_state: Literal["NORMAL", "SUSPICIOUS", "DANGEROUS", "CONFIRMED", "RESOLVED"] = "NORMAL"
    pose_model: str | None = None
    sequence_id: int | None = None


class SkeletonFrameBatch(BaseSchema):
    schema_version: str = PROTOCOL_VERSION
    frames: list[SkeletonFrame]


class TimelineSegment(BaseSchema):
    camera_id: str
    room_id: str | None = None
    track_id: int
    action_label: str
    action_confidence: float = Field(ge=0.0, le=1.0)
    started_at_ms: int
    ended_at_ms: int
    duration_ms: int
    summary_features: dict[str, Any] = Field(default_factory=dict)


class TimelineBatch(BaseSchema):
    schema_version: str = PROTOCOL_VERSION
    segments: list[TimelineSegment]


class OverlayTrack(BaseSchema):
    track_id: str
    bbox: list[int]
    keypoints: list[list[float]]
    action_label: str | None = None
    risk_label: str | None = None
    risk_score: int | None = Field(default=None, ge=1, le=5)
    event_state: str | None = None


class OverlayFrame(BaseSchema):
    schema_version: str = PROTOCOL_VERSION
    camera_id: str
    frame_id: str | int
    sequence_id: str | int | None = None
    capture_ts: str | int | None = None
    analysis_ts: str | int | None = None
    rtsp_pts_ms: int | None = None
    source_width: int
    source_height: int
    stream_width: int | None = None
    stream_height: int | None = None
    analysis_stage: Literal["bbox_only", "action", "stgcn"] = "stgcn"
    latency_ms: float | None = None
    stale: bool = False
    drop_count: int = 0
    fps: float | None = None
    tracks: list[OverlayTrack]


class CameraRegistration(BaseSchema):
    camera_id: str
    room_id: str | None = None
    display_name: str | None = None
    stream_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RiskAssessmentEvent(BaseSchema):
    event_id: str
    camera_id: str
    timestamp_ms: int
    capture_ts: str | None = None
    analysis_ts: str | None = None
    level: Literal["normal", "abnormal", "danger"]
    label: str
    source: str = "secondary_ai"
    model_name: str | None = None
    pre_clip_ms: int = 90_000
    post_clip_ms: int = 90_000
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClipRequestEvent(BaseSchema):
    event_id: str
    camera_id: str
    timestamp_ms: int
    capture_ts: str | None = None
    analysis_ts: str | None = None
    label: str
    pre_clip_ms: int = 90_000
    post_clip_ms: int = 90_000
    reason: str | None = None
    clip_start_ms: int | None = None
    clip_end_ms: int | None = None
    clip_duration_ms: int | None = None
    storage_policy: str | None = None


class CameraHealthPing(BaseSchema):
    camera_id: str
    timestamp_ms: int
    status: Literal["ok", "degraded", "error"]
    details: dict[str, Any] = Field(default_factory=dict)


# --- Phase 1: CandidateWindow schema (2026-04-12) ---

class CandidateWindowFrame(BaseSchema):
    """Single frame within a candidate window."""
    frame_idx: int
    ts_ms: int
    capture_ts: str | None = None
    analysis_ts: str | None = None
    pose_conf_mean: float = Field(ge=0.0, le=1.0)
    bbox_xyxy: list[int]
    keypoints_17: list[list[float]]
    features: dict[str, float]
    inference_timing: dict[str, float] = Field(default_factory=dict)


class VideoRef(BaseSchema):
    """Reference to video segments on edge storage."""
    segment_ids: list[str]
    start_offset_ms: int
    end_offset_ms: int


class CandidateClipRef(BaseSchema):
    """Reference to a danger clip (3-minute window)."""
    enabled: bool = False
    clip_id: str | None = None


class CandidateWindow(BaseSchema):
    """
    Edge -> Server candidate window payload.
    Ref strategy (결정 4):
      DANGER   -> clip_ref only
      ABNORMAL -> video_ref only
      QUALITY  -> no ref
    """
    schema_version: str = "v0.3"
    device_id: str
    camera_id: str
    track_id: int
    candidate_type: str
    candidate_category: Literal["NORMAL", "DANGER", "ABNORMAL", "QUALITY"]
    composite_condition: str
    coarse_action: str
    coarse_confidence: float = Field(ge=0.0, le=1.0)
    capture_ts: str | None = None
    analysis_ts: str | None = None
    inference_timing: dict[str, float] = Field(default_factory=dict)
    risk_label: str | None = None
    risk_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    window: dict[str, Any]  # start_ts_ms, end_ts_ms, fps, frame_count
    trigger_flags: list[str]
    preprocess: dict[str, Any] = Field(default_factory=dict)
    sequence: list[CandidateWindowFrame]
    video_ref: VideoRef | None = None       # ABNORMAL only
    candidate_clip_ref: CandidateClipRef | None = None  # DANGER only


class CandidateWindowBatch(BaseSchema):
    """Batch of candidate windows sent from edge to server."""
    windows: list[CandidateWindow]
