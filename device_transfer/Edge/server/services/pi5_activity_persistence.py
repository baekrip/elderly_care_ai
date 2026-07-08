from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.models import ActivityFrameRecord, CameraRecord, RiskEventRecord, TimelineSegmentRecord
from shared.protocol import ActivityFrame, PosePerson, SkeletonFrame, SkeletonFrameBatch


def _risk_label(event: dict[str, Any] | None, frame: SkeletonFrame) -> str:
    if event is not None:
        state = str(event.get("state") or "").upper()
        if state in {"DANGEROUS", "CONFIRMED"}:
            return "DANGER"
        if state == "SUSPICIOUS":
            return "ABNORMAL"
    if frame.event_state in {"DANGEROUS", "CONFIRMED"}:
        return "DANGER"
    if frame.event_state == "SUSPICIOUS":
        return "ABNORMAL"
    return "NORMAL"


def _action_label(event: dict[str, Any] | None) -> str:
    if event is None:
        return "normal_activity"
    return str(event.get("event_type") or "normal_activity")


def _action_confidence(event: dict[str, Any] | None) -> float:
    if event is None:
        return 1.0
    score = event.get("risk_confidence", event.get("raw_score", 0.0))
    try:
        return max(0.0, min(float(score), 1.0))
    except (TypeError, ValueError):
        return 0.0


def build_activity_frame(frame: SkeletonFrame, event: dict[str, Any] | None) -> ActivityFrame:
    action_label = _action_label(event)
    risk_label = _risk_label(event, frame)
    risk_score = _action_confidence(event)
    person = PosePerson(
        track_id=frame.track_id,
        bbox=frame.bbox,
        bbox_confidence=frame.bbox_confidence,
        keypoints=frame.keypoints,
        pose_confidence_mean=frame.pose_confidence_mean,
        features=frame.features,
        action_label=action_label,
        action_confidence=risk_score if action_label != "normal_activity" else 1.0,
        risk_label=risk_label,
        risk_confidence=risk_score,
        label_source="orin_pi5_pipeline",
        xgboost_prob=None,
        xgboost_tier_ms=None,
        pose_model=frame.pose_model or "pi5_pose",
        classifier_model="orin_pi5_pipeline",
    )
    return ActivityFrame(
        camera_id=frame.camera_id,
        room_id=frame.room_id,
        frame_id=frame.frame_id,
        timestamp_ms=frame.timestamp_ms,
        capture_ts=frame.capture_ts,
        analysis_ts=(event or {}).get("analysis_ts") or frame.analysis_ts,
        persons=[person],
        pipeline_version="pi5_skeleton_v1",
    )


async def persist_pi5_skeleton_batch(
    session: AsyncSession,
    batch: SkeletonFrameBatch,
    pipeline_result: dict[str, Any],
) -> dict[str, int]:
    events_by_frame_id = {
        str(event.get("frame_id")): event
        for event in pipeline_result.get("events", [])
        if isinstance(event, dict) and event.get("frame_id")
    }
    activity_count = 0
    timeline_count = 0
    risk_event_count = 0
    new_frames: list[SkeletonFrame] = []
    for frame in batch.frames:
        await _ensure_camera(session, frame)
        exists = await session.scalar(
            select(ActivityFrameRecord.id).where(ActivityFrameRecord.frame_id == frame.frame_id)
        )
        if exists is not None:
            continue
        new_frames.append(frame)
        activity = build_activity_frame(frame, events_by_frame_id.get(frame.frame_id))
        session.add(
            ActivityFrameRecord(
                frame_id=activity.frame_id,
                camera_id=activity.camera_id,
                timestamp_ms=activity.timestamp_ms,
                room_id=activity.room_id,
                payload=activity.model_dump(mode="json"),
            )
        )
        activity_count += 1

    for segment in _timeline_segments(SkeletonFrameBatch(frames=new_frames), events_by_frame_id):
        session.add(segment)
        timeline_count += 1

    for event in events_by_frame_id.values():
        if await _add_risk_event_record(session, event):
            risk_event_count += 1

    if activity_count or timeline_count or risk_event_count:
        await session.commit()
    return {
        "activity_frames": activity_count,
        "timeline_segments": timeline_count,
        "risk_events": risk_event_count,
    }


async def _ensure_camera(session: AsyncSession, frame: SkeletonFrame) -> None:
    camera = await session.get(CameraRecord, frame.camera_id)
    if camera is None:
        session.add(
            CameraRecord(
                camera_id=frame.camera_id,
                room_id=frame.room_id,
                display_name=frame.camera_id,
                stream_url=None,
                metadata_json={"source": "pi5_skeleton_ws"},
            )
        )


def _timeline_segments(
    batch: SkeletonFrameBatch,
    events_by_frame_id: dict[str, dict[str, Any]],
) -> list[TimelineSegmentRecord]:
    grouped: dict[tuple[str, int], list[SkeletonFrame]] = defaultdict(list)
    for frame in batch.frames:
        grouped[(frame.camera_id, frame.track_id)].append(frame)

    rows: list[TimelineSegmentRecord] = []
    for (_, _), frames in grouped.items():
        frames.sort(key=lambda frame: frame.timestamp_ms)
        start = frames[0]
        current_label = _action_label(events_by_frame_id.get(start.frame_id))
        current_conf = _action_confidence(events_by_frame_id.get(start.frame_id))
        started_at_ms = start.timestamp_ms
        last = start
        for frame in frames[1:]:
            label = _action_label(events_by_frame_id.get(frame.frame_id))
            confidence = _action_confidence(events_by_frame_id.get(frame.frame_id))
            if label != current_label:
                rows.append(_segment_record(last, current_label, current_conf, started_at_ms, last.timestamp_ms))
                started_at_ms = frame.timestamp_ms
                current_label = label
                current_conf = confidence
            else:
                current_conf = max(current_conf, confidence)
            last = frame
        rows.append(_segment_record(last, current_label, current_conf, started_at_ms, last.timestamp_ms))
    return rows


async def _add_risk_event_record(session: AsyncSession, event: dict[str, Any]) -> bool:
    event_id = event.get("event_id")
    camera_id = event.get("camera_id")
    timestamp_ms = event.get("timestamp_ms")
    if event_id is None or camera_id is None or timestamp_ms is None:
        return False
    exists = await session.scalar(select(RiskEventRecord.id).where(RiskEventRecord.event_id == str(event_id)))
    if exists is not None:
        return False
    metadata = {
        "frame_id": event.get("frame_id"),
        "track_id": event.get("track_id"),
        "state": event.get("state"),
        "risk_score": event.get("risk_score"),
        "risk_confidence": event.get("risk_confidence"),
        "raw_score": event.get("raw_score"),
        "vote_ratio": event.get("vote_ratio"),
        "capture_ts": event.get("capture_ts"),
        "analysis_ts": event.get("analysis_ts"),
        "source": "pi5_skeleton_ws",
    }
    if event.get("model_outputs") is not None:
        metadata["model_outputs"] = event.get("model_outputs")
    session.add(
        RiskEventRecord(
            event_id=str(event_id),
            camera_id=str(camera_id),
            timestamp_ms=int(timestamp_ms),
            level=str(event.get("risk_label") or event.get("state") or "unknown"),
            label=str(event.get("event_type") or "unknown"),
            source="pi5_skeleton_ws",
            model_name="orin_pi5_pipeline",
            metadata_json=metadata,
            clip_status="not_requested",
        )
    )
    return True


def _segment_record(
    frame: SkeletonFrame,
    action_label: str,
    action_confidence: float,
    started_at_ms: int,
    ended_at_ms: int,
) -> TimelineSegmentRecord:
    duration_ms = max(0, int(ended_at_ms) - int(started_at_ms))
    return TimelineSegmentRecord(
        camera_id=frame.camera_id,
        track_id=frame.track_id,
        action_label=action_label,
        action_confidence=max(0.0, min(float(action_confidence), 1.0)),
        started_at_ms=int(started_at_ms),
        ended_at_ms=int(ended_at_ms),
        duration_ms=duration_ms,
        room_id=frame.room_id,
        summary_features={
            "source": "orin_pi5_pipeline",
            "frame_id": frame.frame_id,
            "pose_confidence_mean": frame.pose_confidence_mean,
            "features": frame.features,
        },
    )
