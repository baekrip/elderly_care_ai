"""Admin: 가구(households) 관리. v2 정규화 모델의 first-class 엔티티."""
from flask import Blueprint, jsonify, request
from sqlalchemy import text
from flask_jwt_extended import jwt_required
from core.config import IDENTIFIER_PATTERN
from core.db import engine
from core.auth import get_current_role

admin_households_bp = Blueprint("admin_households", __name__)


@admin_households_bp.post("/api/v1/admin/households/register")
@jwt_required()
def register_household():
    """가구 등록 (UPSERT by home_code). admin 계정 연결은 선택."""
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    body = request.get_json(force=True, silent=False) or {}
    home_code     = (body.get("home_code") or "").strip()
    address       = (body.get("address") or "").strip() or None
    account_email = (body.get("account_email") or "").strip().lower() or None

    if not home_code:
        return jsonify({"error": "home_code_required"}), 400
    if not IDENTIFIER_PATTERN.match(home_code):
        return jsonify({"error": "invalid_home_code"}), 400

    with engine.begin() as conn:
        account_id = None
        if account_email:
            acc = conn.execute(
                text("SELECT id FROM users WHERE email = :e"),
                {"e": account_email}
            ).first()
            if acc is None:
                return jsonify({"error": "account_not_found"}), 404
            account_id = acc[0]

        row = conn.execute(text("""
            INSERT INTO households (home_code, address, account_id)
            VALUES (:home_code, :address, :account_id)
            ON CONFLICT (home_code) DO UPDATE SET
                address    = COALESCE(EXCLUDED.address,    households.address),
                account_id = COALESCE(EXCLUDED.account_id, households.account_id)
            RETURNING id, home_code, address, account_id, created_at
        """), {
            "home_code": home_code,
            "address": address,
            "account_id": account_id,
        }).mappings().one()

    return jsonify({
        "household_id":   int(row["id"]),
        "home_code":      row["home_code"],
        "address":        row["address"],
        "account_id":     int(row["account_id"]) if row["account_id"] is not None else None,
        "created_at_utc": row["created_at"].isoformat(),
    })


@admin_households_bp.get("/api/v1/admin/households")
@jwt_required()
def list_households():
    """모든 가구 + 매핑된 환자/디바이스 개수."""
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT h.id, h.home_code, h.address, h.account_id, h.created_at,
                   u.email AS account_email,
                   (SELECT COUNT(*) FROM patients p WHERE p.household_id = h.id) AS patient_count,
                   (SELECT COUNT(*) FROM devices d WHERE d.household_id = h.id) AS device_count
              FROM households h
              LEFT JOIN users u ON h.account_id = u.id
             ORDER BY h.home_code
        """)).mappings().all()

    return jsonify({
        "items": [
            {
                "household_id":   int(r["id"]),
                "home_code":      r["home_code"],
                "address":        r["address"],
                "account_id":     int(r["account_id"]) if r["account_id"] is not None else None,
                "account_email":  r["account_email"],
                "patient_count":  int(r["patient_count"]),
                "device_count":   int(r["device_count"]),
                "created_at_utc": r["created_at"].isoformat(),
            }
            for r in rows
        ]
    })
