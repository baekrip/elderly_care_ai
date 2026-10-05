"""헬스체크."""
from flask import Blueprint, jsonify, request
from datetime import datetime, timezone

health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    return {
        "ok": True,
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "db": "postgres"
    }
