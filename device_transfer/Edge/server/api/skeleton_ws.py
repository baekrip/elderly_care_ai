from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.db import database as db_module
from server.services.backend_forwarder import build_backend_event, extract_first_event_id
from server.services.overlay_broadcaster import OverlayBroadcaster
from server.services.pi5_activity_persistence import persist_pi5_skeleton_batch
from server.services.risk_smoothing import coerce_risk_score, normalize_risk_confidence, to_risk_score
from shared.protocol import SkeletonFrame, SkeletonFrameBatch


router = APIRouter(tags=["skeleton"])


@router.websocket("/ws/skeleton/{camera_id}")
async def skeleton_socket(websocket: WebSocket, camera_id: str) -> None:
    await websocket.accept()
    pipeline = websocket.app.state.pi5_pipeline
    try:
        while True:
            message = await websocket.receive_text()
            payload = json.loads(message)
            if "frames" in payload:
                batch = SkeletonFrameBatch(**payload)
            else:
                frame = SkeletonFrame(**payload)
                batch = SkeletonFrameBatch(frames=[frame])
            result = pipeline.handle_batch(batch)
            persisted = {"activity_frames": 0, "timeline_segments": 0}
            if db_module.AsyncSessionLocal is not None:
                async with db_module.AsyncSessionLocal() as session:
                    persisted = await persist_pi5_skeleton_batch(session, batch, result)
            raw_events = result.get("events", []) if isinstance(result, dict) else []
            events = [_normalize_external_event(event) for event in raw_events]
            backend_forward = _forward_events_to_backend(websocket, events)
            _attach_backend_forward_to_events(events, backend_forward)
            _archive_backend_forward(websocket, events, backend_forward)
            broadcaster = getattr(websocket.app.state, "overlay_broadcaster", None)
            if broadcaster is not None:
                events_by_frame_id = {
                    str(event.get("frame_id")): event
                    for event in events
                    if isinstance(event, dict) and event.get("frame_id") is not None
                }
                for frame in batch.frames:
                    broadcaster.publish(
                        OverlayBroadcaster.frame_from_skeleton(
                            frame,
                            event=events_by_frame_id.get(str(frame.frame_id)),
                        )
                    )
            await websocket.send_json({
                "status": "ok",
                "camera_id": camera_id,
                "count": len(batch.frames),
                "processed": result.get("processed", len(batch.frames)) if isinstance(result, dict) else len(batch.frames),
                "event_count": len(events),
                "events": events[-5:],
                "persisted": persisted,
                "backend_forward": backend_forward,
            })
    except WebSocketDisconnect:
        return


def _forward_events_to_backend(websocket: WebSocket, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return []
    config = getattr(websocket.app.state, "config", {}) or {}
    backend = config.get("backend", {}) if isinstance(config, dict) else {}
    deployment = config.get("deployment", {}) if isinstance(config, dict) else {}
    alerts_enabled = bool(deployment.get("alerts_enabled", True)) if isinstance(deployment, dict) else True
    if not bool(backend.get("enabled", False)):
        return []
    if not alerts_enabled:
        return [
            {
                "local_event_id": event.get("event_id"),
                "event_forwarded": False,
                "event_queued": False,
                "batch_forwarded": 0,
                "event_status_code": 0,
                "delivery_suppressed": True,
                "suppression_reason": "shadow_mode",
            }
            for event in _latest_events_by_local_event_id(events)
        ]
    batcher = getattr(websocket.app.state, "backend_event_batcher", None)
    if batcher is None:
        return []

    # 위험(danger) 이벤트 1초당 1개 제한용 타임스탬프 딕셔너리 초기화
    if not hasattr(websocket.app.state, "_last_danger_forward_ts"):
        websocket.app.state._last_danger_forward_ts = {}

    # 위험/비정상 이벤트 상태 변이 감지용 캐시 딕셔너리 초기화
    if not hasattr(websocket.app.state, "_last_sent_event_state"):
        websocket.app.state._last_sent_event_state = {}

    forwarded: list[dict[str, Any]] = []
    for event in _latest_events_by_local_event_id(events):
        risk_label = str(event.get("risk_label") or "").lower()
        state = str(event.get("state") or "").upper()
        event_type = str(event.get("event_type") or "")

        is_normal = (risk_label == "normal")
        is_danger = (risk_label == "danger") or (state in {"DANGEROUS", "CONFIRMED"})

        if not is_normal:
            # 위험/비정상 이벤트인 경우: 상태 변이가 일어났을 때만 전송
            state_key = f"{event.get('camera_id')}:{event.get('track_id')}"
            current_state = (event_type, state, risk_label)
            last_state = websocket.app.state._last_sent_event_state.get(state_key)

            if last_state == current_state:
                # 상태가 이전 프레임과 완전히 동일하면 전송하지 않고 스킵
                continue

            # 상태 변경을 저장
            websocket.app.state._last_sent_event_state[state_key] = current_state
        else:
            # 정상 활동으로 전이되는 찰나(또는 정상 하트비트) 시점에도 상태 캐시를 리셋
            state_key = f"{event.get('camera_id')}:{event.get('track_id')}"
            websocket.app.state._last_sent_event_state[state_key] = (event_type, state, risk_label)

        if is_danger:
            import time
            camera_id = str(event.get("camera_id") or "default")
            now = time.time()
            last_ts = websocket.app.state._last_danger_forward_ts.get(camera_id, 0.0)
            if now - last_ts < 1.0:
                # 1초 이내 추가 위험 이벤트 전송 스킵 (normal 활동은 건들지 않음)
                continue
            websocket.app.state._last_danger_forward_ts[camera_id] = now

        backend_event = _build_backend_event_from_skeleton_event(config, event)
        submit_result = batcher.submit(backend_event, force=_should_force_backend_forward(event))
        response = submit_result.response
        forwarded.append(
            {
                "local_event_id": event.get("event_id"),
                "event_forwarded": bool(response.ok) if response is not None else False,
                "event_queued": response is None,
                "batch_forwarded": submit_result.forwarded,
                "event_status_code": response.status_code if response is not None else None,
                "ref_event_id": extract_first_event_id(response.json_body) if response is not None else None,
                "pending_replay": submit_result.pending_replay.__dict__ if submit_result.pending_replay else None,
            }
        )
    return forwarded


def _latest_events_by_local_event_id(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    without_id: list[dict[str, Any]] = []
    for event in events:
        local_event_id = event.get("event_id")
        if local_event_id is None:
            without_id.append(event)
            continue
        latest[str(local_event_id)] = event
    return [*without_id, *latest.values()]


def _build_backend_event_from_skeleton_event(config: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    backend = config.get("backend", {}) or {}
    patient = config.get("patient", {}) or {}
    
    fall_only_mode = bool(config.get("events", {}).get("fall_only_mode", False))
    
    source_label = _source_label_for_backend(event)
    severity = _severity_for_event(event)
    state = event.get("state")
    risk_label = event.get("risk_label")
    risk_score = event.get("risk_score")
    model_outputs = event.get("model_outputs")
    
    if fall_only_mode:
        is_fall = (
            not bool(event.get("delivery_suppressed", False))
            and _temporal_decision_result(event) == "fall"
        )
        if not is_fall:
            source_label = "NORMAL"
            severity = 0
            state = "NORMAL"
            risk_label = "normal"
            risk_score = 1
            if isinstance(model_outputs, dict):
                model_outputs = dict(model_outputs)

    return build_backend_event(
        device_key=str(backend.get("device_key") or event.get("camera_id") or ""),
        patient_id=str(patient.get("patient_id", "P001")),
        source_label=source_label,
        confidence=_event_risk_confidence(event),
        severity=severity,
        timestamp_ms=int(event.get("timestamp_ms") or 0),
        frame_id=event.get("frame_id"),
        payload={
            "local_event_id": event.get("event_id"),
            "camera_id": event.get("camera_id"),
            "frame_id": event.get("frame_id"),
            "track_id": event.get("track_id"),
            "state": state,
            "risk_label": risk_label,
            "risk_score": risk_score,
            "risk_confidence": event.get("risk_confidence"),
            "raw_score": event.get("raw_score"),
            "vote_ratio": event.get("vote_ratio"),
            "model_outputs": model_outputs,
            "temporal_decision": event.get("temporal_decision"),
            "activity_diagnostics": event.get("activity_diagnostics"),
            "delivery_suppressed": bool(event.get("delivery_suppressed", False)),
        },
    )


def _event_risk_confidence(event: dict[str, Any]) -> float:
    value = event.get("risk_confidence")
    if value is None:
        value = event.get("raw_score", 0.0)
    return normalize_risk_confidence(value)


def _normalize_external_event(event: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(event)
    confidence = _event_risk_confidence(normalized)
    score = normalized.get("risk_score")
    normalized["risk_confidence"] = confidence
    normalized["risk_score"] = to_risk_score(confidence) if score is None else coerce_risk_score(score)
    return normalized


def _source_label_for_backend(event: dict[str, Any]) -> str:
    if _temporal_decision_result(event) == "fall":
        return "fall_detected"
    return str(event.get("event_type") or event.get("risk_label") or "abnormal_posture")


def _temporal_decision_result(event: dict[str, Any]) -> str:
    decision = event.get("temporal_decision")
    if isinstance(decision, dict):
        return str(decision.get("result") or "").lower()
    return ""


def _severity_for_event(event: dict[str, Any]) -> int:
    risk_label = str(event.get("risk_label") or "").lower()
    state = str(event.get("state") or "").upper()
    if risk_label == "danger" or state in {"DANGEROUS", "CONFIRMED"}:
        return 90
    if risk_label == "suspicious" or state == "SUSPICIOUS":
        return 60
    return 0


def _should_force_backend_forward(event: dict[str, Any]) -> bool:
    risk_label = str(event.get("risk_label") or "").lower()
    if risk_label in {"danger", "normal"}:
        return True
    return _severity_for_event(event) >= 90


def _attach_backend_forward_to_events(events: list[dict[str, Any]], backend_forward: list[dict[str, Any]]) -> None:
    by_local_event_id = {item.get("local_event_id"): item for item in backend_forward}
    for event in events:
        forward = by_local_event_id.get(event.get("event_id"))
        if forward is not None:
            event["backend_forward"] = forward


def _archive_backend_forward(
    websocket: WebSocket,
    events: list[dict[str, Any]],
    backend_forward: list[dict[str, Any]],
) -> None:
    if not backend_forward:
        return
    archive = getattr(websocket.app.state, "result_archive", None)
    if archive is None:
        return
    for event in events:
        if event.get("backend_forward") is not None:
            archive.write_stgcn_result(event)
