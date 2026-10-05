"""Admin: 어르신(patients) 관리."""
from flask import Blueprint, jsonify, request
from typing import Optional
from sqlalchemy import text
from flask_jwt_extended import jwt_required
from core.config import IDENTIFIER_PATTERN
from core.db import engine
from core.auth import get_current_role

admin_patients_bp = Blueprint("admin_patients", __name__)


@admin_patients_bp.post("/api/v1/admin/patients/register")
@jwt_required()
def register_patient():
    """환자 등록 (UPSERT by patient_code). home_code 제공 시 가구 동시 매핑."""
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    body = request.get_json(force=True, silent=False) or {}
    patient_code = (body.get("patient_id") or "").strip()
    name         = (body.get("name") or "").strip() or None
    gender       = (body.get("gender") or "").strip() or None
    birth_date   = (body.get("birth_date") or "").strip() or None  # 'YYYY-MM-DD'
    home_code    = (body.get("home_code") or "").strip() or None

    if not patient_code:
        return jsonify({"error": "patient_id_required"}), 400
    if not IDENTIFIER_PATTERN.match(patient_code):
        return jsonify({"error": "invalid_patient_id"}), 400

    with engine.begin() as conn:
        household_id = None
        if home_code:
            if not IDENTIFIER_PATTERN.match(home_code):
                return jsonify({"error": "invalid_home_code"}), 400
            hh = conn.execute(
                text("SELECT id FROM households WHERE home_code = :hc"),
                {"hc": home_code}
            ).first()
            if hh is None:
                return jsonify({"error": "household_not_found"}), 404
            household_id = hh[0]

        row = conn.execute(text("""
            INSERT INTO patients (patient_code, name, gender, birth_date, household_id)
            VALUES (:patient_code, :name, :gender, :birth_date, :household_id)
            ON CONFLICT (patient_code) DO UPDATE SET
                name         = COALESCE(EXCLUDED.name,         patients.name),
                gender       = COALESCE(EXCLUDED.gender,       patients.gender),
                birth_date   = COALESCE(EXCLUDED.birth_date,   patients.birth_date),
                household_id = COALESCE(EXCLUDED.household_id, patients.household_id)
            RETURNING id, patient_code, name, gender, birth_date, status, household_id, created_at
        """), {
            "patient_code": patient_code,
            "name": name,
            "gender": gender,
            "birth_date": birth_date,
            "household_id": household_id,
        }).mappings().one()

    return jsonify({
        "patient_internal_id": int(row["id"]),
        "patient_id":          row["patient_code"],
        "name":                row["name"],
        "gender":              row["gender"],
        "birth_date":          row["birth_date"].isoformat() if row["birth_date"] else None,
        "status":              row["status"],
        "household_id":        int(row["household_id"]) if row["household_id"] is not None else None,
        "created_at_utc":      row["created_at"].isoformat(),
    })


@admin_patients_bp.post("/api/v1/admin/patients/<patient_code>/assign-household")
@jwt_required()
def assign_patient_household(patient_code: str):
    """환자 가구 매핑 변경 (또는 해제). body home_code=null이면 해제."""
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    body = request.get_json(force=True, silent=True) or {}
    home_code_raw = body.get("home_code")

    with engine.begin() as conn:
        patient = conn.execute(
            text("SELECT id FROM patients WHERE patient_code = :pc"),
            {"pc": patient_code}
        ).first()
        if patient is None:
            return jsonify({"error": "patient_not_found"}), 404

        household_id = None
        home_code: Optional[str] = None
        if home_code_raw:
            home_code = home_code_raw.strip()
            if not IDENTIFIER_PATTERN.match(home_code):
                return jsonify({"error": "invalid_home_code"}), 400
            hh = conn.execute(
                text("SELECT id FROM households WHERE home_code = :hc"),
                {"hc": home_code}
            ).first()
            if hh is None:
                return jsonify({"error": "household_not_found"}), 404
            household_id = hh[0]

        conn.execute(
            text("UPDATE patients SET household_id = :hid WHERE id = :pid"),
            {"hid": household_id, "pid": patient[0]}
        )

    return jsonify({
        "patient_id": patient_code,
        "home_code":  home_code,
        "assigned":   household_id is not None,
    })
