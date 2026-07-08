from __future__ import annotations

import uuid
import time
from datetime import datetime, timezone
from typing import Annotated
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from server.db.database import get_session
from server.db.models import RiskEventRecord
from server.services.backend_forwarder import (
    BackendForwarder,
    append_pending,
    build_candidate_backend_event,
    build_immediate_alert,
    config_from_project_config,
    extract_first_alert_id,
    extract_first_event_id,
    is_retryable_response,
    should_forward_level,
)
from shared.protocol import CandidateWindowBatch, ClipRequestEvent
from shared.time_utils import utc_iso_from_datetime, utc_iso_from_ms, utc_iso_now


router = APIRouter(prefix="/api", tags=["candidates"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/candidates/submit")
async def submit_candidates(
    payload: dict[str, Any],
    request: Request,
    session: SessionDep,
) -> dict[str, object]:
    try:
        batch = _parse_candidate_batch_payload(payload)
    except (TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_candidate_payload",
                "message": str(exc),
            },
        ) from exc

    classifier = request.app.state.stgcn_classifier
    hub = request.app.state.edge_hub
    archive = request.app.state.result_archive
    default_pre_clip_ms = int(request.app.state.config.get("events", {}).get("default_pre_clip_ms", 90_000))
    default_post_clip_ms = int(request.app.state.config.get("events", {}).get("default_post_clip_ms", 90_000))
    clip_storage_policy = str(request.app.state.config.get("events", {}).get("clip_storage_policy", "segment_ring"))
    danger_labels = {
        str(label).upper()
        for label in request.app.state.config.get("events", {}).get(
            "danger_labels",
            ["DROP", "FALL", "GRADUAL_FALL", "LOSS_OF_BALANCE", "POST_FALL_IMMOBILITY"],
        )
    }

    results: list[dict[str, object]] = []
    archive.write_candidate_request(batch.model_dump(mode="json"))

    for window in batch.windows:
        stgcn_started = time.perf_counter()
        final_label, confidence, detail = classifier.classify(window)
        stgcn_inference_ms = round((time.perf_counter() - stgcn_started) * 1000.0, 4)
        normalized_final_label = str(final_label).upper()
        
        # [SENSITIVITY BOOST] 에지측 후보 정보가 누움/낙상 지표를 지니고 있다면 danger로 승격
        is_fall_supported = (
            window.risk_label in {"DROP", "FALL", "GRADUAL_FALL", "LYING"}
            or window.coarse_action in {"LYING", "FALL", "TRANSITION"}
            or any("FALL" in str(f).upper() or "DROP" in str(f).upper() for f in window.trigger_flags)
        )
        if is_fall_supported and normalized_final_label == "NORMAL":
            final_label = "FALL"
            normalized_final_label = "FALL"

        effective_level = (
            "normal"
            if normalized_final_label == "NORMAL"
            else (
                "danger"
                if window.candidate_category == "DANGER" or normalized_final_label in danger_labels
                else window.candidate_category.lower()
            )
        )

        # [TEMP] fall_only_mode 가 참일 때는 danger(낙상) 이외의 모든 후보를 강제로 normal(정상) 처리하여 유출 차단
        fall_only_mode = bool(request.app.state.config.get("events", {}).get("fall_only_mode", False))
        if fall_only_mode:
            is_fall = (effective_level == "danger") or (normalized_final_label in danger_labels)
            if not is_fall:
                effective_level = "normal"
                final_label = "NORMAL"
                normalized_final_label = "NORMAL"
                window.candidate_category = "NORMAL"
                window.risk_label = "NORMAL"

        start_ts_ms = int(window.window.get("start_ts_ms", window.sequence[0].ts_ms if window.sequence else 0))
        end_ts_ms = int(window.window.get("end_ts_ms", window.sequence[-1].ts_ms if window.sequence else start_ts_ms))
        capture_ts = window.capture_ts or utc_iso_from_ms(end_ts_ms)
        analysis_ts = utc_iso_now()
        event_id = f"cand_{window.camera_id}_{window.track_id}_{end_ts_ms}_{uuid.uuid4().hex[:8]}"

        session.add(
            RiskEventRecord(
                event_id=event_id,
                camera_id=window.camera_id,
                timestamp_ms=end_ts_ms,
                level=effective_level,
                label=final_label,
                source="candidate_stgcn",
                model_name="stgcn_stub" if classifier.model is None else "stgcn",
                metadata_json={
                    "candidate_type": window.candidate_type,
                    "candidate_category": window.candidate_category,
                    "coarse_action": window.coarse_action,
                    "coarse_confidence": window.coarse_confidence,
                    "risk_label": window.risk_label,
                    "risk_confidence": window.risk_confidence,
                    "trigger_flags": window.trigger_flags,
                    "composite_condition": window.composite_condition,
                    "window": window.window,
                    "preprocess": window.preprocess,
                    "video_ref": window.video_ref.model_dump(mode="json") if window.video_ref else None,
                    "candidate_clip_ref": window.candidate_clip_ref.model_dump(mode="json") if window.candidate_clip_ref else None,
                    "stgcn_detail": detail,
                    "capture_ts": capture_ts,
                    "analysis_ts": analysis_ts,
                    "edge_inference_timing": window.inference_timing,
                    "stgcn_inference_ms": stgcn_inference_ms,
                },
                clip_status="requested" if effective_level == "danger" else "not_required",
            )
        )

        clip_requested = False
        if effective_level == "danger":
            clip_request = ClipRequestEvent(
                event_id=event_id,
                camera_id=window.camera_id,
                timestamp_ms=end_ts_ms,
                capture_ts=capture_ts,
                analysis_ts=analysis_ts,
                label=final_label,
                pre_clip_ms=default_pre_clip_ms,
                post_clip_ms=default_post_clip_ms,
                clip_start_ms=end_ts_ms - default_pre_clip_ms,
                clip_end_ms=end_ts_ms + default_post_clip_ms,
                clip_duration_ms=default_pre_clip_ms + default_post_clip_ms,
                storage_policy=clip_storage_policy,
                reason=f"{window.candidate_type}:{final_label}",
            )
            archive.write_clip_request(clip_request.model_dump(mode="json"))
            clip_requested = await hub.send_to_camera(
                window.camera_id,
                clip_request.model_dump(mode="json"),
            )

        backend_result: dict[str, object] | None = None
        backend_cfg = config_from_project_config(request.app.state.config)
        if backend_cfg.enabled and should_forward_level(effective_level):
            backend_event = build_candidate_backend_event(
                device_key=window.device_id or window.camera_id,
                patient_id=str(request.app.state.config.get("patient", {}).get("patient_id", "P001")),
                candidate_type=window.candidate_type,
                candidate_category=window.candidate_category,
                final_label=final_label,
                effective_level=effective_level,
                confidence=confidence,
                timestamp_ms=end_ts_ms,
                capture_ts=capture_ts,
                analysis_ts=analysis_ts,
                trigger_flags=window.trigger_flags,
                stgcn_inference_ms=stgcn_inference_ms,
                frame_id=window.sequence[-1].frame_idx if window.sequence else None,
                extra_payload={
                    "event_id": event_id,
                    "camera_id": window.camera_id,
                    "track_id": window.track_id,
                    "window": window.window,
                },
            )
            forwarder = BackendForwarder(backend_cfg)
            event_response = None
            pending_replay = None
            ref_event_id = None
            event_queued = False
            batch_forwarded = 0
            if effective_level in {"danger", "normal"}:
                event_response = forwarder.post_events_batch([backend_event])
                if is_retryable_response(event_response):
                    append_pending(
                        backend_cfg.pending_file,
                        kind="events_batch",
                        url=backend_cfg.events_batch_url,
                        payload={"events": [backend_event]},
                        response=event_response,
                    )
                pending_replay = forwarder.retry_pending() if event_response.ok else None
                ref_event_id = extract_first_event_id(event_response.json_body)
                batch_forwarded = 1 if event_response.ok else 0
            else:
                batcher = getattr(request.app.state, "backend_event_batcher", None)
                if batcher is None:
                    event_response = forwarder.post_events_batch([backend_event])
                    if is_retryable_response(event_response):
                        append_pending(
                            backend_cfg.pending_file,
                            kind="events_batch",
                            url=backend_cfg.events_batch_url,
                            payload={"events": [backend_event]},
                            response=event_response,
                        )
                    pending_replay = forwarder.retry_pending() if event_response.ok else None
                    batch_forwarded = 1 if event_response.ok else 0
                else:
                    submit_result = batcher.submit(backend_event)
                    event_response = submit_result.response
                    pending_replay = submit_result.pending_replay
                    event_queued = submit_result.response is None
                    batch_forwarded = submit_result.forwarded
                    ref_event_id = extract_first_event_id(event_response.json_body) if event_response else None
            alert_response = None
            related_alert_id = None
            if effective_level == "danger" and event_response is not None and event_response.ok:
                alert = build_immediate_alert(
                    device_key=backend_event["device_key"],
                    patient_id=backend_event["patient_id"],
                    alert_type=backend_event["event_type"],
                    alert_level="critical",
                    message=f"{backend_event['event_type']} critical event",
                    ts=backend_event["ts"],
                    ref_event_id=ref_event_id,
                    payload={
                        "event_id": event_id,
                        "event_type": backend_event["event_type"],
                        "final_label": final_label,
                    },
                )
                alert_response = forwarder.post_immediate_alert(alert)
                if alert_response.ok:
                    related_alert_id = extract_first_alert_id(alert_response.json_body)
                    _remember_related_alert_id(request.app.state, event_id, related_alert_id)
                if is_retryable_response(alert_response):
                    append_pending(
                        backend_cfg.pending_file,
                        kind="alerts_immediate",
                        url=backend_cfg.alerts_immediate_url,
                        payload=alert,
                        response=alert_response,
                    )
            backend_result = {
                "event_forwarded": bool(event_response.ok) if event_response else False,
                "event_queued": event_queued,
                "batch_forwarded": batch_forwarded,
                "event_status_code": event_response.status_code if event_response else None,
                "ref_event_id": ref_event_id,
                "alert_forwarded": bool(alert_response.ok) if alert_response else False,
                "alert_status_code": alert_response.status_code if alert_response else None,
                "related_alert_id": related_alert_id,
                "pending_replay": pending_replay.__dict__ if pending_replay else None,
            }

        results.append(
            {
                "event_id": event_id,
                "camera_id": window.camera_id,
                "track_id": window.track_id,
                "candidate_type": window.candidate_type,
                "candidate_category": window.candidate_category,
                "effective_level": effective_level,
                "final_label": final_label,
                "confidence": confidence,
                "stgcn_inference_ms": stgcn_inference_ms,
                "capture_ts": capture_ts,
                "analysis_ts": analysis_ts,
                "clip_requested": clip_requested,
                "backend_forward": backend_result,
                "window_start_ts_ms": start_ts_ms,
                "window_end_ts_ms": end_ts_ms,
            }
        )
        archive.write_stgcn_result(results[-1] | {"detail": detail})

    await session.commit()
    return {"count": len(results), "results": results}


def _remember_related_alert_id(app_state: object, event_id: str, alert_id: object | None) -> None:
    if not event_id or alert_id is None:
        return
    try:
        resolved_alert_id = int(alert_id)
    except (TypeError, ValueError):
        return
    mapping = getattr(app_state, "backend_alert_ids_by_event_id", None)
    if not isinstance(mapping, dict):
        mapping = {}
        setattr(app_state, "backend_alert_ids_by_event_id", mapping)
    mapping[event_id] = resolved_alert_id


def _parse_candidate_batch_payload(payload: dict[str, Any]) -> CandidateWindowBatch:
    return CandidateWindowBatch(**_normalize_candidate_batch_payload(payload))


def _normalize_candidate_batch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("candidate payload must be a JSON object")
    if "frames" in payload and "windows" not in payload:
        raise ValueError("skeleton frame payload must use /ws/skeleton/{camera_id}")

    if "windows" in payload:
        raw_windows = payload["windows"]
    elif _looks_like_candidate_window(payload):
        raw_windows = [payload]
    else:
        raise ValueError("candidate payload must contain windows")

    if not isinstance(raw_windows, list):
        raise ValueError("windows must be a list")
    return {"windows": [_normalize_candidate_window(window) for window in raw_windows]}


def _looks_like_candidate_window(payload: dict[str, Any]) -> bool:
    return "sequence" in payload and ("candidate_type" in payload or "event_type" in payload)


def _normalize_candidate_window(raw_window: Any) -> dict[str, Any]:
    if not isinstance(raw_window, dict):
        raise ValueError("candidate window must be a JSON object")

    raw_sequence = raw_window.get("sequence")
    if not isinstance(raw_sequence, list) or not raw_sequence:
        raise ValueError("candidate window sequence must be a non-empty list")
    sequence = [_normalize_candidate_frame(frame, index) for index, frame in enumerate(raw_sequence)]

    camera_id = raw_window.get("camera_id") or _first_present(raw_sequence, "camera_id")
    if not camera_id:
        raise ValueError("candidate window camera_id is required")
    track_id = raw_window.get("track_id") or _first_present(raw_sequence, "track_id")
    if track_id is None:
        raise ValueError("candidate window track_id is required")

    trigger_flags = raw_window.get("trigger_flags") or []
    if not isinstance(trigger_flags, list):
        trigger_flags = [str(trigger_flags)]

    window_meta = dict(raw_window.get("window") or {})
    window_meta.setdefault("start_ts_ms", sequence[0]["ts_ms"])
    window_meta.setdefault("end_ts_ms", sequence[-1]["ts_ms"])
    window_meta.setdefault("frame_count", len(sequence))

    start_ts_ms = int(window_meta["start_ts_ms"])
    end_ts_ms = int(window_meta["end_ts_ms"])
    capture_ts = _coerce_utc_iso(raw_window.get("capture_ts"), fallback_ms=start_ts_ms)
    analysis_ts = _coerce_utc_iso(raw_window.get("analysis_ts"), fallback_ms=end_ts_ms)

    return {
        "schema_version": str(raw_window.get("schema_version", "v0.3")),
        "device_id": str(raw_window.get("device_id") or camera_id),
        "camera_id": str(camera_id),
        "track_id": int(track_id),
        "candidate_type": str(raw_window.get("candidate_type") or raw_window.get("event_type") or raw_window.get("label") or ""),
        "candidate_category": _normalize_candidate_category(raw_window),
        "composite_condition": str(
            raw_window.get("composite_condition")
            or raw_window.get("condition")
            or "+".join(str(flag) for flag in trigger_flags)
            or raw_window.get("candidate_type")
            or "legacy_candidate"
        ),
        "coarse_action": str(raw_window.get("coarse_action") or raw_window.get("action_label") or "UNKNOWN"),
        "coarse_confidence": _coerce_float(
            raw_window.get("coarse_confidence", raw_window.get("action_confidence", raw_window.get("confidence"))),
            "coarse_confidence",
        ),
        "capture_ts": capture_ts,
        "analysis_ts": analysis_ts,
        "inference_timing": _numeric_dict(raw_window.get("inference_timing") or {}),
        "risk_label": raw_window.get("risk_label"),
        "risk_confidence": _optional_float(raw_window.get("risk_confidence")),
        "window": window_meta,
        "trigger_flags": [str(flag) for flag in trigger_flags],
        "preprocess": dict(raw_window.get("preprocess") or {}),
        "sequence": sequence,
        "video_ref": raw_window.get("video_ref"),
        "candidate_clip_ref": raw_window.get("candidate_clip_ref"),
    }


def _normalize_candidate_frame(raw_frame: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw_frame, dict):
        raise ValueError("candidate sequence frame must be a JSON object")
    ts_ms = raw_frame.get("ts_ms", raw_frame.get("timestamp_ms"))
    if ts_ms is None:
        raise ValueError("candidate sequence frame ts_ms is required")

    pose_conf_mean = raw_frame.get("pose_conf_mean", raw_frame.get("pose_confidence_mean"))
    if pose_conf_mean is None:
        raise ValueError("candidate sequence frame pose_conf_mean is required")

    return {
        "frame_idx": int(raw_frame.get("frame_idx", index)),
        "ts_ms": int(ts_ms),
        "capture_ts": _coerce_utc_iso(raw_frame.get("capture_ts"), fallback_ms=int(ts_ms)),
        "analysis_ts": _coerce_utc_iso(raw_frame.get("analysis_ts"), fallback_ms=int(ts_ms)),
        "pose_conf_mean": _coerce_float(pose_conf_mean, "pose_conf_mean"),
        "bbox_xyxy": _normalize_bbox(raw_frame.get("bbox_xyxy", raw_frame.get("bbox"))),
        "keypoints_17": _normalize_keypoints(raw_frame.get("keypoints_17", raw_frame.get("keypoints"))),
        "features": _numeric_dict(raw_frame.get("features") or {}),
        "inference_timing": _numeric_dict(raw_frame.get("inference_timing") or {}),
    }


def _normalize_bbox(value: Any) -> list[int]:
    if isinstance(value, dict):
        keys = ("x1", "y1", "x2", "y2")
        if all(key in value for key in keys):
            return [int(value[key]) for key in keys]
        alt_keys = ("left", "top", "right", "bottom")
        if all(key in value for key in alt_keys):
            return [int(value[key]) for key in alt_keys]
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        return [int(value[index]) for index in range(4)]
    raise ValueError("candidate frame bbox must be [x1,y1,x2,y2] or object with x1/y1/x2/y2")


def _normalize_keypoints(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        raise ValueError("candidate frame keypoints must be a list")
    keypoints: list[list[float]] = []
    for item in value[:17]:
        if isinstance(item, dict):
            keypoints.append([
                float(item["x"]),
                float(item["y"]),
                float(item.get("confidence", item.get("conf", item.get("score", 0.0)))),
            ])
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            confidence = item[2] if len(item) > 2 else 0.0
            keypoints.append([float(item[0]), float(item[1]), float(confidence)])
        else:
            raise ValueError("candidate frame keypoint must be object or [x,y,confidence]")
    return keypoints


def _normalize_candidate_category(raw_window: dict[str, Any]) -> str:
    value = raw_window.get("candidate_category", raw_window.get("category", raw_window.get("level", raw_window.get("event_level"))))
    if value is None:
        raise ValueError("candidate_category is required")
    normalized = str(value).upper()
    if normalized in {"NORMAL", "DANGER", "ABNORMAL", "QUALITY"}:
        return normalized
    raise ValueError("candidate_category must be NORMAL, DANGER, ABNORMAL, or QUALITY")


def _coerce_utc_iso(value: Any, *, fallback_ms: int | None = None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return utc_iso_from_ms(int(value))
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return utc_iso_from_ms(int(raw))
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return utc_iso_from_ms(fallback_ms) if fallback_ms is not None else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return utc_iso_from_datetime(parsed)


def _numeric_dict(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        try:
            result[str(key)] = float(item)
        except (TypeError, ValueError):
            continue
    return result


def _coerce_float(value: Any, field_name: str) -> float:
    if value is None:
        raise ValueError(f"{field_name} is required")
    return float(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _first_present(rows: list[Any], key: str) -> Any | None:
    for row in rows:
        if isinstance(row, dict) and row.get(key) is not None:
            return row[key]
    return None
