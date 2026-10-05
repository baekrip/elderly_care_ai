"""Admin: 디바이스 관리. device_key 명명 규칙: pi5-<home>-cam<N>, jetson-<home>."""
from flask import Blueprint, jsonify, request
from typing import Any, Dict, Optional
from sqlalchemy import text
from flask_jwt_extended import jwt_required
from core.config import IDENTIFIER_PATTERN, ALLOWED_DEVICE_TYPES
from core.db import engine
from core.auth import get_current_role

admin_devices_bp = Blueprint("admin_devices", __name__)


@admin_devices_bp.post("/api/v1/admin/devices/register")
@jwt_required()
def register_device():
    """디바이스 등록 (UPSERT). device_type='camera'면 cameras row도 자동 UPSERT."""
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    body = request.get_json(force=True, silent=False) or {}

    device_key        = (body.get("device_key") or "").strip()
    device_name       = (body.get("device_name") or "").strip() or None
    device_type       = (body.get("device_type") or "").strip() or None
    location          = (body.get("location") or "").strip() or None
    home_code         = (body.get("home_code") or "").strip() or None
    parent_device_key = (body.get("parent_device_key") or "").strip() or None
    serial_number     = (body.get("serial_number") or "").strip() or None
    mac_address       = (body.get("mac_address") or "").strip() or None

    # camera-specific
    stream_path = (body.get("stream_path") or "").strip() or None
    resolution  = (body.get("resolution") or "").strip() or None
    fps_raw     = body.get("fps")

    # 검증
    if not device_key:
        return jsonify({"error": "device_key_required"}), 400
    if not IDENTIFIER_PATTERN.match(device_key):
        return jsonify({"error": "invalid_device_key"}), 400
    if device_type is not None and device_type not in ALLOWED_DEVICE_TYPES:
        return jsonify({"error": "invalid_device_type"}), 400
    if home_code is not None and not IDENTIFIER_PATTERN.match(home_code):
        return jsonify({"error": "invalid_home_code"}), 400
    if parent_device_key is not None and not IDENTIFIER_PATTERN.match(parent_device_key):
        return jsonify({"error": "invalid_parent_device_key"}), 400
    if parent_device_key is not None and parent_device_key == device_key:
        return jsonify({"error": "invalid_parent_self_loop"}), 400

    fps_int: Optional[int] = None
    if fps_raw is not None:
        try:
            fps_int = int(fps_raw)
        except (ValueError, TypeError):
            return jsonify({"error": "invalid_fps"}), 400

    with engine.begin() as conn:
        # home_code → household_id
        household_id = None
        if home_code:
            hh = conn.execute(
                text("SELECT id FROM households WHERE home_code = :hc"),
                {"hc": home_code}
            ).first()
            if hh is None:
                return jsonify({"error": "household_not_found"}), 404
            household_id = hh[0]

        # parent_device_key → parent_device_id
        parent_device_id = None
        if parent_device_key:
            pd = conn.execute(
                text("SELECT id FROM devices WHERE device_key = :dk"),
                {"dk": parent_device_key}
            ).first()
            if pd is None:
                return jsonify({"error": "parent_device_not_found"}), 404
            parent_device_id = pd[0]

        # devices UPSERT
        device_row = conn.execute(text("""
            INSERT INTO devices (
                device_key, device_name, device_type, location,
                household_id, parent_device_id, serial_number, mac_address
            )
            VALUES (
                :device_key, :device_name, :device_type, :location,
                :household_id, :parent_device_id, :serial_number, :mac_address
            )
            ON CONFLICT (device_key) DO UPDATE SET
                device_name      = COALESCE(EXCLUDED.device_name,      devices.device_name),
                device_type      = COALESCE(EXCLUDED.device_type,      devices.device_type),
                location         = COALESCE(EXCLUDED.location,         devices.location),
                household_id     = COALESCE(EXCLUDED.household_id,     devices.household_id),
                parent_device_id = COALESCE(EXCLUDED.parent_device_id, devices.parent_device_id),
                serial_number    = COALESCE(EXCLUDED.serial_number,    devices.serial_number),
                mac_address      = COALESCE(EXCLUDED.mac_address,      devices.mac_address)
            RETURNING id, device_key, device_name, device_type, location,
                      household_id, parent_device_id, serial_number, mac_address,
                      status, created_at
        """), {
            "device_key": device_key,
            "device_name": device_name,
            "device_type": device_type,
            "location": location,
            "household_id": household_id,
            "parent_device_id": parent_device_id,
            "serial_number": serial_number,
            "mac_address": mac_address,
        }).mappings().one()

        # cameras 동기화 (audit Logic H-4):
        #   device_type='camera'이면 UPSERT
        #   device_type='camera'가 아니면 (기존 'camera'였다가 변경된 경우 포함) DELETE
        #   → 카메라/Pi5 타입 플립 시 stale cameras row 차단
        camera_info = None
        if device_row["device_type"] == "camera":
            cam_row = conn.execute(text("""
                INSERT INTO cameras (device_id, stream_path, resolution, fps)
                VALUES (:device_id, :stream_path, :resolution, :fps)
                ON CONFLICT (device_id) DO UPDATE SET
                    stream_path = COALESCE(EXCLUDED.stream_path, cameras.stream_path),
                    resolution  = COALESCE(EXCLUDED.resolution,  cameras.resolution),
                    fps         = COALESCE(EXCLUDED.fps,         cameras.fps)
                RETURNING id, stream_path, resolution, fps
            """), {
                "device_id": device_row["id"],
                "stream_path": stream_path,
                "resolution": resolution,
                "fps": fps_int,
            }).mappings().one()
            camera_info = {
                "camera_id":   int(cam_row["id"]),
                "stream_path": cam_row["stream_path"],
                "resolution":  cam_row["resolution"],
                "fps":         int(cam_row["fps"]) if cam_row["fps"] is not None else None,
            }
        else:
            # device_type이 'camera'가 아니면 기존 cameras row 제거 (있었다면)
            # ON DELETE CASCADE는 device 삭제 시만 동작. 여기선 type 변경이라 명시적 DELETE 필요.
            conn.execute(
                text("DELETE FROM cameras WHERE device_id = :device_id"),
                {"device_id": device_row["id"]}
            )

    return jsonify({
        "device_id":         int(device_row["id"]),
        "device_key":        device_row["device_key"],
        "device_name":       device_row["device_name"],
        "device_type":       device_row["device_type"],
        "location":          device_row["location"],
        "household_id":      int(device_row["household_id"]) if device_row["household_id"] is not None else None,
        "parent_device_id":  int(device_row["parent_device_id"]) if device_row["parent_device_id"] is not None else None,
        "serial_number":     device_row["serial_number"],
        "mac_address":       device_row["mac_address"],
        "status":            device_row["status"],
        "created_at_utc":    device_row["created_at"].isoformat(),
        "camera":            camera_info,
    })


@admin_devices_bp.get("/api/v1/admin/devices")
@jwt_required()
def list_devices():
    """모든 디바이스 목록 + 가구/부모/카메라 정보. Optional filter: ?home_code=X&patient_id=Y"""
    if get_current_role() != "admin":
        return jsonify({"error": "admin_only"}), 403

    home_code    = request.args.get("home_code")
    patient_code = request.args.get("patient_id")

    where  = []
    params: Dict[str, Any] = {}

    if home_code:
        where.append("h.home_code = :home_code")
        params["home_code"] = home_code
    if patient_code:
        # 환자가 속한 가구의 디바이스만
        where.append("""
            d.household_id = (
                SELECT household_id FROM patients WHERE patient_code = :patient_code
            )
        """)
        params["patient_code"] = patient_code

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with engine.begin() as conn:
        rows = conn.execute(text(f"""
            SELECT d.id, d.device_key, d.device_name, d.device_type, d.location,
                   d.household_id, d.parent_device_id, d.serial_number, d.mac_address,
                   d.status, d.created_at,
                   h.home_code AS household_code,
                   pd.device_key AS parent_device_key,
                   c.id AS camera_id, c.stream_path, c.resolution, c.fps
              FROM devices d
              LEFT JOIN households h ON d.household_id = h.id
              LEFT JOIN devices pd ON d.parent_device_id = pd.id
              LEFT JOIN cameras c ON c.device_id = d.id
              {where_sql}
             ORDER BY h.home_code NULLS LAST, d.device_key
        """), params).mappings().all()

    return jsonify({
        "items": [
            {
                "device_id":         int(r["id"]),
                "device_key":        r["device_key"],
                "device_name":       r["device_name"],
                "device_type":       r["device_type"],
                "location":          r["location"],
                "household_id":      int(r["household_id"]) if r["household_id"] is not None else None,
                "home_code":         r["household_code"],
                "parent_device_id":  int(r["parent_device_id"]) if r["parent_device_id"] is not None else None,
                "parent_device_key": r["parent_device_key"],
                "serial_number":     r["serial_number"],
                "mac_address":       r["mac_address"],
                "status":            r["status"],
                "created_at_utc":    r["created_at"].isoformat(),
                "camera": {
                    "camera_id":   int(r["camera_id"]),
                    "stream_path": r["stream_path"],
                    "resolution":  r["resolution"],
                    "fps":         int(r["fps"]) if r["fps"] is not None else None,
                } if r["camera_id"] is not None else None,
            }
            for r in rows
        ]
    })
