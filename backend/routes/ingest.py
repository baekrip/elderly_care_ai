"""AI/엣지 수신 endpoint + 2차 AI용 일일 요약. 전부 정적 APP_TOKEN 인증."""
from flask import Blueprint, jsonify, request
from typing import Any, Dict, Optional
from datetime import datetime, timezone, timedelta
from psycopg.types.json import Jsonb
from sqlalchemy import text
from core.db import engine, get_or_create_device, get_or_create_patient, resolve_device_context
from core.auth import require_token
from core.utils import clamp_int, maybe_float, parse_iso8601

ingest_bp = Blueprint("ingest", __name__)


@ingest_bp.get("/api/v1/daily-summary")
def get_daily_summary():
    require_token()

    days         = clamp_int(request.args.get("days", 7), 1, 30, 7)
    patient_code = request.args.get("patient_id")
    from_dt      = datetime.now(timezone.utc) - timedelta(days=days)

    where  = ["e.occurred_at >= :from_dt"]
    params: Dict[str, Any] = {"from_dt": from_dt}

    if patient_code:
        where.append("p.patient_code = :patient_code")
        params["patient_code"] = patient_code

    where_sql = "WHERE " + " AND ".join(where)

    with engine.begin() as conn:
        rows = conn.execute(text(f"""
            SELECT
                p.patient_code,
                DATE(e.occurred_at AT TIME ZONE 'UTC') AS date,
                e.event_type,
                COUNT(*)                               AS event_count,
                AVG(e.confidence)                      AS avg_confidence,
                MAX(e.severity)                        AS max_severity
            FROM events e
            JOIN patients p ON e.patient_id = p.id
            {where_sql}
            GROUP BY p.patient_code, DATE(e.occurred_at AT TIME ZONE 'UTC'), e.event_type
            ORDER BY p.patient_code, date, e.event_type
        """), params).mappings().all()

    return jsonify({
        "from_date": from_dt.date().isoformat(),
        "to_date":   datetime.now(timezone.utc).date().isoformat(),
        "items": [
            {
                "patient_id":      r["patient_code"],
                "date":            r["date"].isoformat(),
                "event_type":      r["event_type"],
                "event_count":     int(r["event_count"]),
                "avg_confidence":  float(r["avg_confidence"]) if r["avg_confidence"] else None,
                "max_severity":    int(r["max_severity"]),
            }
            for r in rows
        ]
    })


@ingest_bp.post("/api/v1/events/batch")
def ingest_events_batch():
    require_token()
    body   = request.get_json(force=True, silent=False) or {}
    events = body.get("events", [])

    if not events or not isinstance(events, list):
        return jsonify({"error": "events_array_required"}), 400

    results = []
    with engine.begin() as conn:
        for event in events:
            device_key = (event.get("device_key") or "").strip()
            event_type = (event.get("event_type") or "").strip()
            if not device_key or not event_type:
                continue

            patient_code = (event.get("patient_id") or "").strip() or None
            confidence   = maybe_float(event.get("confidence"))
            severity     = clamp_int(event.get("severity"), 0, 100, 0)
            occurred_at  = parse_iso8601(event.get("ts"))
            payload      = event.get("payload")
            clip_url     = (event.get("clip_url") or "").strip() or None
            frame_id_raw = event.get("frame_id")
            frame_id: Optional[int] = None
            if frame_id_raw is not None:
                try:
                    frame_id = int(frame_id_raw)
                except (ValueError, TypeError):
                    frame_id = None  # 잘못된 frame_id면 NULL (이벤트 자체는 받음)

            device_id  = get_or_create_device(conn, device_key)
            patient_fk = get_or_create_patient(conn, patient_code)

            # v2: device로부터 household_id + camera_id 비정규화
            ctx = resolve_device_context(conn, device_id)

            event_id = conn.execute(text("""
                INSERT INTO events (
                    device_id, patient_id, event_type, confidence,
                    severity, event_status, occurred_at, clip_url, payload_json,
                    household_id, camera_id, frame_id
                )
                VALUES (
                    :device_id, :patient_id, :event_type, :confidence,
                    :severity, 'detected', :occurred_at, :clip_url, :payload_json,
                    :household_id, :camera_id, :frame_id
                )
                RETURNING id
            """), {
                "device_id": device_id, "patient_id": patient_fk,
                "event_type": event_type, "confidence": confidence,
                "severity": severity, "occurred_at": occurred_at,
                "clip_url": clip_url,
                "payload_json": Jsonb(payload) if payload is not None else None,
                "household_id": ctx["household_id"],
                "camera_id":    ctx["camera_id"],
                "frame_id":     frame_id,
            }).scalar_one()

            results.append({"event_id": int(event_id), "stored": True})

    return jsonify({"results": results, "count": len(results)}), 201


@ingest_bp.post("/api/v1/alerts/immediate")
def ingest_immediate_alert():
    require_token()
    body = request.get_json(force=True, silent=False) or {}

    device_key = (body.get("device_key") or "").strip()
    alert_type = (body.get("alert_type") or "").strip()
    level      = (body.get("level") or "").strip()
    message    = (body.get("message") or "").strip()

    if not (device_key and alert_type and level and message):
        return jsonify({"error": "device_key_alert_type_level_message_required"}), 400

    patient_code = (body.get("patient_id") or "").strip() or None
    occurred_at  = parse_iso8601(body.get("ts"))
    ref_event_id = body.get("ref_event_id")
    payload      = body.get("payload")

    with engine.begin() as conn:
        device_id  = get_or_create_device(conn, device_key)
        patient_fk = get_or_create_patient(conn, patient_code)
        ctx        = resolve_device_context(conn, device_id)

        alert_id = conn.execute(
            text("""
                INSERT INTO alerts (
                    source, event_id, device_id, patient_id,
                    alert_type, alert_level, message, occurred_at, payload_json,
                    household_id, camera_id
                )
                VALUES (
                    'EDGE', :event_id, :device_id, :patient_id,
                    :alert_type, :alert_level, :message, :occurred_at, :payload_json,
                    :household_id, :camera_id
                )
                RETURNING id
            """),
            {
                "event_id": int(ref_event_id) if ref_event_id is not None else None,
                "device_id": device_id, "patient_id": patient_fk,
                "alert_type": alert_type, "alert_level": level,
                "message": message, "occurred_at": occurred_at,
                "payload_json": Jsonb(payload) if payload is not None else None,
                "household_id": ctx["household_id"],
                "camera_id":    ctx["camera_id"],
            }
        ).scalar_one()

    return jsonify({"alert_id": int(alert_id), "stored": True}), 201


@ingest_bp.post("/api/v1/alerts/trend")
def ingest_trend_alert():
    require_token()
    body = request.get_json(force=True, silent=False) or {}

    device_key = (body.get("device_key") or "").strip()
    alert_type = (body.get("alert_type") or "").strip()
    level      = (body.get("level") or "").strip()
    message    = (body.get("message") or "").strip()

    if not (device_key and alert_type and level and message):
        return jsonify({"error": "device_key_alert_type_level_message_required"}), 400

    patient_code = (body.get("patient_id") or "").strip() or None
    occurred_at  = parse_iso8601(body.get("ts"))
    payload      = body.get("payload")

    with engine.begin() as conn:
        device_id  = get_or_create_device(conn, device_key)
        patient_fk = get_or_create_patient(conn, patient_code)
        ctx        = resolve_device_context(conn, device_id)

        alert_id = conn.execute(
            text("""
                INSERT INTO alerts (
                    source, event_id, device_id, patient_id,
                    alert_type, alert_level, message, occurred_at, payload_json,
                    household_id, camera_id
                )
                VALUES (
                    'ANALYZER', NULL, :device_id, :patient_id,
                    :alert_type, :alert_level, :message, :occurred_at, :payload_json,
                    :household_id, :camera_id
                )
                RETURNING id
            """),
            {
                "device_id": device_id, "patient_id": patient_fk,
                "alert_type": alert_type, "alert_level": level,
                "message": message, "occurred_at": occurred_at,
                "payload_json": Jsonb(payload) if payload is not None else None,
                "household_id": ctx["household_id"],
                "camera_id":    ctx["camera_id"],
            }
        ).scalar_one()

    return jsonify({"alert_id": int(alert_id), "stored": True}), 201


@ingest_bp.post("/api/v1/risk-scores")
def ingest_risk_score():
    require_token()
    body = request.get_json(force=True, silent=False) or {}

    patient_code = (body.get("patient_id") or "").strip()
    if not patient_code:
        return jsonify({"error": "patient_id_required"}), 400

    score         = clamp_int(body.get("score"), 0, 100, 0)
    reason        = (body.get("reason") or "").strip() or None
    risk_level    = (body.get("risk_level") or "").strip() or None
    analyzed_from = parse_iso8601(body.get("analyzed_from")) if body.get("analyzed_from") else None
    analyzed_to   = parse_iso8601(body.get("analyzed_to")) if body.get("analyzed_to") else None

    with engine.begin() as conn:
        patient_fk = get_or_create_patient(conn, patient_code)

        rs_id = conn.execute(
            text("""
                INSERT INTO risk_scores (patient_id, score, risk_level, reason, analyzed_from, analyzed_to)
                VALUES (:patient_id, :score, :risk_level, :reason, :analyzed_from, :analyzed_to)
                RETURNING id
            """),
            {
                "patient_id": patient_fk, "score": score,
                "risk_level": risk_level, "reason": reason,
                "analyzed_from": analyzed_from, "analyzed_to": analyzed_to,
            }
        ).scalar_one()

    return jsonify({"risk_score_id": int(rs_id), "stored": True}), 201
