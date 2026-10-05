"""Admin: 보호자 계정 CRUD + 환자 매핑. 전부 role=admin 필요."""
from flask import Blueprint, jsonify, request
from typing import Any, Dict, Optional
import bcrypt
from sqlalchemy import text
from flask_jwt_extended import get_jwt_identity, jwt_required
from core.config import IDENTIFIER_PATTERN
from core.db import engine, resolve_device_context
from core.auth import get_current_role

admin_users_bp = Blueprint("admin_users", __name__)


@admin_users_bp.post("/api/v1/admin/patient-assign")
@jwt_required()
def assign_patient():
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    body = request.get_json(force=True, silent=False) or {}
    user_id    = body.get("user_id")
    patient_id = body.get("patient_id")

    if not user_id or not patient_id:
        return jsonify({"error": "user_id_and_patient_id_required"}), 400

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO patient_guardians (user_id, patient_id)
                VALUES (:user_id, :patient_id)
                ON CONFLICT DO NOTHING
            """),
            {"user_id": user_id, "patient_id": patient_id}
        )

    return jsonify({"assigned": True})


@admin_users_bp.post("/api/v1/admin/users/register")
@jwt_required()
def admin_register_user():
    """Admin이 프론트엔드에서 새 보호자 계정 생성.

    body:
        email          (str, required)
        password       (str, required)
        patient_ids    (list[str], optional) — 매핑할 어르신 코드 (예: ["P001","P003"])
                       없는 코드면 그 자리에서 어르신 row 자동 생성 (메타는 비어둠).

    role은 강제로 'guardian' (이 endpoint로 admin 생성 차단 — 권한 상승 방지).
    """
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    body = request.get_json(force=True, silent=False) or {}
    email         = (body.get("email") or "").strip().lower()
    password      = (body.get("password") or "").strip()
    patient_codes = body.get("patient_ids") or []

    if not email or not password:
        return jsonify({"error": "email_and_password_required"}), 400
    if not isinstance(patient_codes, list):
        return jsonify({"error": "patient_ids_must_be_array"}), 400

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email}
        ).first()
        if existing:
            return jsonify({"error": "email_already_exists"}), 409

        patient_fks = []
        auto_created_codes = []
        for code in patient_codes:
            code = (code or "").strip()
            if not code:
                continue
            if not IDENTIFIER_PATTERN.match(code):
                return jsonify({"error": "invalid_patient_id", "patient_id": code}), 400

            row = conn.execute(
                text("SELECT id FROM patients WHERE patient_code = :pc"),
                {"pc": code}
            ).first()
            if row is None:
                # auto-create: 코드만 박고 메타는 NULL (admin이 어르신 상세 페이지에서 채움)
                new_id = conn.execute(
                    text("""
                        INSERT INTO patients (patient_code, status)
                        VALUES (:pc, 'active')
                        RETURNING id
                    """),
                    {"pc": code}
                ).scalar_one()
                patient_fks.append(int(new_id))
                auto_created_codes.append(code)
            else:
                patient_fks.append(row[0])

        user_id = conn.execute(
            text("""
                INSERT INTO users (email, password_hash, role)
                VALUES (:email, :pw_hash, 'guardian')
                RETURNING id
            """),
            {"email": email, "pw_hash": pw_hash}
        ).scalar_one()

        for pf in patient_fks:
            conn.execute(
                text("""
                    INSERT INTO patient_guardians (user_id, patient_id)
                    VALUES (:uid, :pid)
                    ON CONFLICT DO NOTHING
                """),
                {"uid": int(user_id), "pid": pf}
            )

    return jsonify({
        "user_id":                int(user_id),
        "email":                  email,
        "role":                   "guardian",
        "assigned_patient_count": len(patient_fks),
        "auto_created_patients":  auto_created_codes,
    }), 201


@admin_users_bp.get("/api/v1/admin/users")
@jwt_required()
def admin_list_users():
    """Admin이 user 목록 조회. 각 user에 매핑된 환자 코드 배열 포함.

    Optional filter: ?role=guardian
    """
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    role_filter = (request.args.get("role") or "").strip() or None
    if role_filter is not None and role_filter not in ("admin", "guardian", "caregiver"):
        return jsonify({"error": "invalid_role"}), 400

    where_sql = ""
    params: Dict[str, Any] = {}
    if role_filter:
        where_sql = "WHERE u.role = :role"
        params["role"] = role_filter

    with engine.begin() as conn:
        rows = conn.execute(text(f"""
            SELECT u.id, u.email, u.role, u.created_at,
                   COALESCE(
                       (
                           SELECT array_agg(p.patient_code ORDER BY p.patient_code)
                             FROM patient_guardians pg
                             JOIN patients p ON p.id = pg.patient_id
                            WHERE pg.user_id = u.id
                       ),
                       ARRAY[]::varchar[]
                   ) AS patient_codes
              FROM users u
              {where_sql}
             ORDER BY u.id
        """), params).mappings().all()

    return jsonify({
        "items": [
            {
                "user_id":         int(r["id"]),
                "email":           r["email"],
                "role":            r["role"],
                "patient_ids":     list(r["patient_codes"]) if r["patient_codes"] else [],
                "created_at_utc":  r["created_at"].isoformat(),
            }
            for r in rows
        ]
    })


@admin_users_bp.delete("/api/v1/admin/users/<int:user_id>")
@jwt_required()
def admin_delete_user(user_id: int):
    """Admin이 user 계정 삭제. patient_guardians 매핑은 FK CASCADE로 자동 정리.

    추가 청소 (자동):
        삭제되는 user가 가지고 있던 어르신 중,
        다른 보호자 매핑도 없고 events/alerts/clips/risk_scores 활동도 없는
        '진짜 orphan'은 자동으로 같이 삭제.
        활동 데이터가 있는 어르신은 보존 (데이터 손실 방지).

    안전장치:
        - admin 본인 계정은 삭제 불가 (관리자 락아웃 방지)
        - admin 역할 user 삭제 불가 (이 endpoint는 guardian/caregiver만)
    """
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    me_id = int(get_jwt_identity())
    if user_id == me_id:
        return jsonify({"error": "cannot_delete_self"}), 400

    with engine.begin() as conn:
        target = conn.execute(
            text("SELECT id, email, role FROM users WHERE id = :id"),
            {"id": user_id}
        ).mappings().first()
        if target is None:
            return jsonify({"error": "user_not_found"}), 404
        if target["role"] == "admin":
            return jsonify({"error": "cannot_delete_admin"}), 403

        # 1) 이 user에게 매핑된 어르신 id snapshot
        associated_patient_ids = [
            int(r[0]) for r in conn.execute(
                text("SELECT patient_id FROM patient_guardians WHERE user_id = :uid"),
                {"uid": user_id}
            ).fetchall()
        ]

        # 2) user 삭제 (patient_guardians FK CASCADE)
        conn.execute(
            text("DELETE FROM users WHERE id = :id"),
            {"id": user_id}
        )

        # 3) snapshot 중 진짜 orphan만 정리
        deleted_orphan_codes = []
        if associated_patient_ids:
            orphan_rows = conn.execute(
                text("""
                    DELETE FROM patients p
                     WHERE p.id = ANY(:ids)
                       AND NOT EXISTS (SELECT 1 FROM patient_guardians WHERE patient_id = p.id)
                       AND NOT EXISTS (SELECT 1 FROM events        WHERE patient_id = p.id)
                       AND NOT EXISTS (SELECT 1 FROM alerts        WHERE patient_id = p.id)
                       AND NOT EXISTS (SELECT 1 FROM clips         WHERE patient_id = p.id)
                       AND NOT EXISTS (SELECT 1 FROM risk_scores   WHERE patient_id = p.id)
                    RETURNING patient_code
                """),
                {"ids": associated_patient_ids}
            ).fetchall()
            deleted_orphan_codes = [r[0] for r in orphan_rows]

    return jsonify({
        "deleted":                 True,
        "user_id":                 int(target["id"]),
        "email":                   target["email"],
        "role":                    target["role"],
        "deleted_orphan_patients": deleted_orphan_codes,
    })


@admin_users_bp.post("/api/v1/admin/patient-unassign")
@jwt_required()
def admin_patient_unassign():
    """Admin이 guardian ↔ 환자 매핑 해제. assign의 역연산."""
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    body = request.get_json(force=True, silent=False) or {}
    user_id      = body.get("user_id")
    patient_code = (body.get("patient_id") or "").strip()

    if not user_id or not patient_code:
        return jsonify({"error": "user_id_and_patient_id_required"}), 400

    try:
        user_id_int = int(user_id)
    except (ValueError, TypeError):
        return jsonify({"error": "invalid_user_id"}), 400

    with engine.begin() as conn:
        patient = conn.execute(
            text("SELECT id FROM patients WHERE patient_code = :pc"),
            {"pc": patient_code}
        ).first()
        if patient is None:
            return jsonify({"error": "patient_not_found"}), 404

        result = conn.execute(
            text("""
                DELETE FROM patient_guardians
                 WHERE user_id    = :uid
                   AND patient_id = :pid
            """),
            {"uid": user_id_int, "pid": patient[0]}
        )
        deleted_count = result.rowcount

    if deleted_count == 0:
        return jsonify({"error": "mapping_not_found"}), 404

    return jsonify({"unassigned": True})


# ==========================
# Admin: 가구 / 환자 / 디바이스 관리 (v2 정규화 모델, migration 006)
# ==========================
# 가구(household) first-class. 환자도 디바이스도 가구에 종속.
# 환자 ↔ 디바이스는 가구를 통해 간접 연결 (M:N 제거).
#
# device_key 명명 규칙:
#   pi5-home001        Raspberry Pi 5 본체 (device_type='raspberry_pi')
#   pi5-home001-cam0   Pi5의 첫 카메라    (device_type='camera', parent_device_key='pi5-home001')
#   pi5-home001-cam1   Pi5의 두 번째 카메라
#   jetson-home001     Jetson              (device_type='jetson')
#
# 모든 endpoint는 JWT + admin role 필요.



def resolve_device_context(conn, device_fk: int) -> Dict[str, Optional[int]]:
    """device.id로부터 ingest 시점 비정규화에 쓸 (household_id, camera_id) 조회."""
    row = conn.execute(text("""
        SELECT d.household_id,
               (SELECT c.id FROM cameras c WHERE c.device_id = d.id) AS camera_id
          FROM devices d
         WHERE d.id = :device_id
    """), {"device_id": device_fk}).first()
    if row is None:
        return {"household_id": None, "camera_id": None}
    return {"household_id": row[0], "camera_id": row[1]}
