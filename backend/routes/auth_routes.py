"""로그인 — 사용자 JWT 발급."""
from flask import Blueprint, jsonify, request
import bcrypt
from sqlalchemy import text
from flask_jwt_extended import create_access_token
from core.db import engine

auth_bp = Blueprint("auth_routes", __name__)


@auth_bp.post("/api/v1/auth/login")
def login():
    body = request.get_json(force=True, silent=False) or {}

    email    = (body.get("email") or "").strip().lower()
    password = (body.get("password") or "").strip()

    if not email or not password:
        return jsonify({"error": "email_and_password_required"}), 400

    with engine.begin() as conn:
        user = conn.execute(
            text("SELECT id, email, password_hash, role FROM users WHERE email = :email"),
            {"email": email}
        ).mappings().first()

    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "invalid_credentials"}), 401

    token = create_access_token(
        identity=str(user["id"]),
        additional_claims={"role": user["role"], "email": user["email"]}
    )

    return jsonify({
        "access_token": token,
        "user_id": int(user["id"]),
        "email": user["email"],
        "role": user["role"]
    })
