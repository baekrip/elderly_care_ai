"""프론트 조회 endpoint. 전부 JWT + 역할 기반 행 필터링."""
from flask import Blueprint, jsonify, request
from typing import Any, Dict
from sqlalchemy import text
from flask_jwt_extended import get_jwt_identity, jwt_required
from core.db import engine
from core.auth import check_patient_access, get_current_role, guardian_patient_codes
from core.serializers import alert_row_to_dict, event_row_to_dict, incident_row_to_dict, risk_score_row_to_dict
from core.utils import clamp_int

query_bp = Blueprint("query", __name__)


@query_bp.get("/api/v1/events")
@jwt_required()
def list_events():
    role    = get_current_role()
    user_id = int(get_jwt_identity())

    limit        = clamp_int(request.args.get("limit", 100), 1, 500, 100)
    device_key   = request.args.get("device_key")
    patient_code = request.args.get("patient_id")
    event_type   = request.args.get("event_type")

    where  = []
    params: Dict[str, Any] = {"limit": limit}

    with engine.begin() as conn:
        if role == "guardian":
            allowed = guardian_patient_codes(conn, user_id)
            if not allowed:
                return jsonify({"items": []})
            if patient_code:
                if patient_code not in allowed:
                    return jsonify({"error": "access_denied"}), 403
                where.append("p.patient_code = :patient_code")
                params["patient_code"] = patient_code
            else:
                where.append(f"p.patient_code = ANY(:allowed)")
                params["allowed"] = allowed
        else:
            if patient_code:
                where.append("p.patient_code = :patient_code")
                params["patient_code"] = patient_code

        if device_key:
            where.append("d.device_key = :device_key")
            params["device_key"] = device_key
        if event_type:
            where.append("e.event_type = :event_type")
            params["event_type"] = event_type

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        rows = conn.execute(text(f"""
            SELECT e.id, d.device_key, p.patient_code, e.event_type,
                   e.confidence, e.severity, e.event_status,
                   e.occurred_at, e.clip_url, e.payload_json
            FROM events e
            JOIN devices d ON e.device_id = d.id
            LEFT JOIN patients p ON e.patient_id = p.id
            {where_sql}
            ORDER BY e.occurred_at DESC
            LIMIT :limit
        """), params).mappings().all()

    return jsonify({"items": [event_row_to_dict(r) for r in rows]})


@query_bp.get("/api/v1/incidents")
@jwt_required()
def list_incidents():
    """사건(Silver) 목록 조회 — 프론트 알림/사건 카드용.
    raw events 대신 집계된 incidents를 본다. 역할 필터는 list_events와 동일.
    """
    role    = get_current_role()
    user_id = int(get_jwt_identity())

    limit         = clamp_int(request.args.get("limit", 100), 1, 500, 100)
    patient_code  = request.args.get("patient_id")
    incident_type = request.args.get("incident_type")
    status        = request.args.get("status")

    where  = []
    params: Dict[str, Any] = {"limit": limit}

    with engine.begin() as conn:
        if role == "guardian":
            allowed = guardian_patient_codes(conn, user_id)
            if not allowed:
                return jsonify({"items": []})
            if patient_code:
                if patient_code not in allowed:
                    return jsonify({"error": "access_denied"}), 403
                where.append("p.patient_code = :patient_code")
                params["patient_code"] = patient_code
            else:
                where.append("p.patient_code = ANY(:allowed)")
                params["allowed"] = allowed
        else:
            if patient_code:
                where.append("p.patient_code = :patient_code")
                params["patient_code"] = patient_code

        if incident_type:
            where.append("i.incident_type = :incident_type")
            params["incident_type"] = incident_type
        if status:
            where.append("i.status = :status")
            params["status"] = status

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        rows = conn.execute(text(f"""
            SELECT i.id, p.patient_code, i.incident_type,
                   i.started_at, i.ended_at, i.raw_event_count,
                   i.max_confidence, i.max_severity, i.avg_confidence,
                   i.status, i.created_at
            FROM incidents i
            JOIN patients p ON i.patient_id = p.id
            {where_sql}
            ORDER BY i.started_at DESC
            LIMIT :limit
        """), params).mappings().all()

    return jsonify({"items": [incident_row_to_dict(r) for r in rows]})


@query_bp.get("/api/v1/alerts")
@jwt_required()
def list_alerts():
    role    = get_current_role()
    user_id = int(get_jwt_identity())

    limit        = clamp_int(request.args.get("limit", 100), 1, 500, 100)
    device_key   = request.args.get("device_key")
    patient_code = request.args.get("patient_id")
    source       = request.args.get("source")

    where  = []
    params: Dict[str, Any] = {"limit": limit}

    with engine.begin() as conn:
        if role == "guardian":
            allowed = guardian_patient_codes(conn, user_id)
            if not allowed:
                return jsonify({"items": []})
            if patient_code:
                if patient_code not in allowed:
                    return jsonify({"error": "access_denied"}), 403
                where.append("p.patient_code = :patient_code")
                params["patient_code"] = patient_code
            else:
                where.append("p.patient_code = ANY(:allowed)")
                params["allowed"] = allowed
        else:
            if patient_code:
                where.append("p.patient_code = :patient_code")
                params["patient_code"] = patient_code

        if device_key:
            where.append("d.device_key = :device_key")
            params["device_key"] = device_key
        if source:
            where.append("a.source = :source")
            params["source"] = source

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        rows = conn.execute(text(f"""
            SELECT a.id, a.source, d.device_key, p.patient_code,
                   a.alert_type, a.alert_level, a.message, a.event_id,
                   a.occurred_at, a.payload_json, a.is_read
            FROM alerts a
            JOIN devices d ON a.device_id = d.id
            LEFT JOIN patients p ON a.patient_id = p.id
            {where_sql}
            ORDER BY a.occurred_at DESC
            LIMIT :limit
        """), params).mappings().all()

    return jsonify({"items": [alert_row_to_dict(r) for r in rows]})


@query_bp.get("/api/v1/risk-scores")
@jwt_required()
def list_risk_scores():
    role    = get_current_role()
    user_id = int(get_jwt_identity())

    limit        = clamp_int(request.args.get("limit", 100), 1, 500, 100)
    patient_code = request.args.get("patient_id")
    risk_level   = request.args.get("risk_level")

    where  = []
    params: Dict[str, Any] = {"limit": limit}

    with engine.begin() as conn:
        if role == "guardian":
            allowed = guardian_patient_codes(conn, user_id)
            if not allowed:
                return jsonify({"items": []})
            if patient_code:
                if patient_code not in allowed:
                    return jsonify({"error": "access_denied"}), 403
                where.append("p.patient_code = :patient_code")
                params["patient_code"] = patient_code
            else:
                where.append("p.patient_code = ANY(:allowed)")
                params["allowed"] = allowed
        else:
            if patient_code:
                where.append("p.patient_code = :patient_code")
                params["patient_code"] = patient_code

        if risk_level:
            where.append("rs.risk_level = :risk_level")
            params["risk_level"] = risk_level

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        rows = conn.execute(text(f"""
            SELECT rs.id, p.patient_code, rs.score, rs.risk_level,
                   rs.reason, rs.analyzed_from, rs.analyzed_to, rs.created_at
            FROM risk_scores rs
            JOIN patients p ON rs.patient_id = p.id
            {where_sql}
            ORDER BY rs.created_at DESC
            LIMIT :limit
        """), params).mappings().all()

    return jsonify({"items": [risk_score_row_to_dict(r) for r in rows]})


@query_bp.get("/api/v1/patients")
@jwt_required()
def list_patients():
    """환자 목록.
    - guardian: patient_guardians 매핑된 환자만
    - admin: 전체
    각 환자에 가구 코드 + 최신 risk score(있으면) 포함.
    """
    role    = get_current_role()
    user_id = int(get_jwt_identity())

    where_sql = ""
    params: dict = {}
    if role == "guardian":
        where_sql = "WHERE p.id IN (SELECT patient_id FROM patient_guardians WHERE user_id = :user_id)"
        params["user_id"] = user_id

    with engine.begin() as conn:
        rows = conn.execute(
            text(f"""
                SELECT
                    p.patient_code,
                    p.name,
                    p.gender,
                    p.birth_date,
                    p.status,
                    h.home_code        AS household_code,
                    rs.id              AS risk_id,
                    rs.score           AS risk_score,
                    rs.risk_level      AS risk_level,
                    rs.created_at      AS risk_created_at
                FROM patients p
                LEFT JOIN households h ON h.id = p.household_id
                LEFT JOIN LATERAL (
                    SELECT id, score, risk_level, created_at
                    FROM risk_scores
                    WHERE patient_id = p.id
                    ORDER BY created_at DESC
                    LIMIT 1
                ) rs ON true
                {where_sql}
                ORDER BY p.patient_code
            """),
            params
        ).mappings().all()

    def to_dict(r):
        latest_risk = None
        if r["risk_id"] is not None:
            latest_risk = {
                "id":             int(r["risk_id"]),
                "score":          int(r["risk_score"]),
                "risk_level":     r["risk_level"],
                "created_at_utc": r["risk_created_at"].isoformat() if r["risk_created_at"] else None,
            }
        return {
            "patient_id":        r["patient_code"],
            "name":              r["name"],
            "gender":            r["gender"],
            "birth_date":        r["birth_date"].isoformat() if r["birth_date"] else None,
            "status":            r["status"],
            "household_code":    r["household_code"],
            "latest_risk_score": latest_risk,
        }

    return jsonify({"patients": [to_dict(r) for r in rows]})


@query_bp.get("/api/v1/dashboard")
@jwt_required()
def get_dashboard():
    role    = get_current_role()
    user_id = int(get_jwt_identity())

    patient_code = request.args.get("patient_id")
    if not patient_code:
        return jsonify({"error": "patient_id_required"}), 400

    with engine.begin() as conn:
        if role == "guardian":
            if not check_patient_access(conn, user_id, patient_code):
                return jsonify({"error": "access_denied"}), 403

        patient = conn.execute(
            text("SELECT id, patient_code, name, gender, birth_date, status FROM patients WHERE patient_code = :pc"),
            {"pc": patient_code}
        ).mappings().first()

        if not patient:
            return jsonify({"error": "patient_not_found"}), 404

        latest_risk = conn.execute(
            text("""
                SELECT rs.id, p.patient_code, rs.score, rs.risk_level,
                       rs.reason, rs.analyzed_from, rs.analyzed_to, rs.created_at
                FROM risk_scores rs
                JOIN patients p ON rs.patient_id = p.id
                WHERE p.patient_code = :pc
                ORDER BY rs.created_at DESC LIMIT 1
            """),
            {"pc": patient_code}
        ).mappings().first()

        event_rows = conn.execute(
            text("""
                SELECT e.id, d.device_key, p.patient_code, e.event_type,
                       e.confidence, e.severity, e.event_status,
                       e.occurred_at, e.clip_url, e.payload_json
                FROM events e
                JOIN devices d ON e.device_id = d.id
                JOIN patients p ON e.patient_id = p.id
                WHERE p.patient_code = :pc
                ORDER BY e.occurred_at DESC LIMIT 10
            """),
            {"pc": patient_code}
        ).mappings().all()

        alert_rows = conn.execute(
            text("""
                SELECT a.id, a.source, d.device_key, p.patient_code,
                       a.alert_type, a.alert_level, a.message, a.event_id,
                       a.occurred_at, a.payload_json, a.is_read
                FROM alerts a
                JOIN devices d ON a.device_id = d.id
                JOIN patients p ON a.patient_id = p.id
                WHERE p.patient_code = :pc
                ORDER BY a.occurred_at DESC LIMIT 10
            """),
            {"pc": patient_code}
        ).mappings().all()

        # Medallion Silver: incident 단위 사건 카드 (raw 22건 → 1건)
        incident_rows = conn.execute(
            text("""
                SELECT i.id, p.patient_code, i.incident_type, i.started_at, i.ended_at,
                       i.raw_event_count, i.max_confidence, i.max_severity,
                       i.avg_confidence, i.status, i.created_at
                FROM incidents i
                JOIN patients p ON i.patient_id = p.id
                WHERE i.patient_id = :pid
                ORDER BY i.started_at DESC LIMIT 10
            """),
            {"pid": int(patient["id"])}
        ).mappings().all()

    return jsonify({
        "patient": {
            "id": int(patient["id"]),
            "patient_id": patient["patient_code"],
            "name": patient["name"],
            "gender": patient["gender"],
            "birth_date": patient["birth_date"].isoformat() if patient["birth_date"] else None,
            "status": patient["status"],
        },
        "latest_risk_score": risk_score_row_to_dict(latest_risk) if latest_risk else None,
        "recent_events": [event_row_to_dict(r) for r in event_rows],
        "recent_alerts": [alert_row_to_dict(r) for r in alert_rows],
        "recent_incidents": [incident_row_to_dict(r) for r in incident_rows],
    })
