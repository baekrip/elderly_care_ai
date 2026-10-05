"""위험 클립(S3). 업로드 계열은 APP_TOKEN, 목록 조회는 JWT."""
from flask import Blueprint, jsonify, request
from typing import Any, Dict, Optional
import uuid
from botocore.exceptions import ClientError
from sqlalchemy import text
from flask_jwt_extended import get_jwt_identity, jwt_required
from core.config import (
    APP_TOKEN, S3_BUCKET, S3_PRESIGN_EXPIRES, MAX_CLIP_SIZE_BYTES, IDENTIFIER_PATTERN,
    CLIP_RETENTION_DAYS,
)
from core.db import engine, get_or_create_device, get_or_create_patient, resolve_device_context
from core.auth import get_current_role, guardian_patient_codes, require_token
from core.serializers import clip_row_to_dict
from core.s3 import s3_client
from core.utils import clamp_int, maybe_float, parse_iso8601

clips_bp = Blueprint("clips", __name__)


@clips_bp.post("/api/v1/clips/upload-url")
def request_clip_upload_url():
    """1차 AI가 위험 클립 업로드 직전에 호출. Presigned PUT URL 발급 + clips 메타 pre-INSERT.

    related_alert_id가 동일하면 멱등 처리 (재시도 시 pending row 재사용).
    """
    require_token()

    if s3_client is None or not S3_BUCKET:
        return jsonify({"error": "s3_not_configured"}), 503

    body = request.get_json(force=True, silent=False) or {}

    patient_code = (body.get("patient_id") or "").strip()
    device_key   = (body.get("device_key") or "").strip()
    event_type   = (body.get("event_type") or "").strip()

    if not (patient_code and device_key and event_type):
        return jsonify({"error": "patient_id_device_key_event_type_required"}), 400

    # 식별자 화이트리스트 검증 — S3 key prefix 안전성 + DB injection 방지
    if not IDENTIFIER_PATTERN.match(patient_code):
        return jsonify({"error": "invalid_patient_id"}), 400
    if not IDENTIFIER_PATTERN.match(device_key):
        return jsonify({"error": "invalid_device_key"}), 400

    # 타임스탬프 캐스팅 가드
    # AttributeError: 클라이언트가 정수(epoch 등) 보내면 .strip() 호출에서 발생
    try:
        occurred_at = parse_iso8601(body.get("occurred_at") or body.get("ts"))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"error": "invalid_occurred_at"}), 400

    # related_alert_id 캐스팅 가드
    related_alert_id_raw = body.get("related_alert_id")
    related_alert_id: Optional[int] = None
    if related_alert_id_raw is not None:
        try:
            related_alert_id = int(related_alert_id_raw)
        except (ValueError, TypeError):
            return jsonify({"error": "invalid_related_alert_id"}), 400

    duration_sec = clamp_int(body.get("duration_sec"), 1, 600, 5)

    with engine.begin() as conn:
        patient_fk = get_or_create_patient(conn, patient_code)
        device_fk  = get_or_create_device(conn, device_key)

        # 신뢰 경계 검증: related_alert_id가 정말 이 환자의 alert인지 확인
        # (APP_TOKEN 유출 시 다른 환자 alert에 클립 매다는 공격 차단)
        if related_alert_id is not None:
            alert_row = conn.execute(
                text("SELECT patient_id FROM alerts WHERE id = :id"),
                {"id": related_alert_id}
            ).first()
            if alert_row is None:
                return jsonify({"error": "related_alert_not_found"}), 400
            if alert_row[0] is not None and alert_row[0] != patient_fk:
                return jsonify({"error": "related_alert_patient_mismatch"}), 400

        # 멱등성: 같은 related_alert_id로 이미 pending row가 있으면 재사용
        existing_uuid: Optional[str] = None
        existing_s3_key: Optional[str] = None
        if related_alert_id is not None:
            existing = conn.execute(
                text("""
                    SELECT clip_uuid, s3_key
                      FROM clips
                     WHERE related_alert_id = :alert_id
                       AND status            = 'pending'
                     ORDER BY created_at DESC
                     LIMIT 1
                """),
                {"alert_id": related_alert_id}
            ).first()
            if existing is not None:
                existing_uuid = str(existing[0])
                existing_s3_key = existing[1]

        if existing_uuid is not None and existing_s3_key is not None:
            clip_uuid = existing_uuid
            s3_key = existing_s3_key
        else:
            # v2: device로부터 household_id + camera_id 비정규화
            ctx = resolve_device_context(conn, device_fk)

            clip_uuid = str(uuid.uuid4())
            s3_key = f"clips/{patient_code}/{occurred_at.strftime('%Y/%m/%d')}/{clip_uuid}.mp4"
            conn.execute(
                text("""
                    INSERT INTO clips (
                        clip_uuid, patient_id, device_id, related_alert_id,
                        event_type, occurred_at, requested_duration_sec,
                        s3_key, status,
                        household_id, camera_id
                    )
                    VALUES (
                        :clip_uuid, :patient_id, :device_id, :related_alert_id,
                        :event_type, :occurred_at, :requested_duration_sec,
                        :s3_key, 'pending',
                        :household_id, :camera_id
                    )
                """),
                {
                    "clip_uuid": clip_uuid,
                    "patient_id": patient_fk,
                    "device_id": device_fk,
                    "related_alert_id": related_alert_id,
                    "event_type": event_type,
                    "occurred_at": occurred_at,
                    "requested_duration_sec": duration_sec,
                    "s3_key": s3_key,
                    "household_id": ctx["household_id"],
                    "camera_id":    ctx["camera_id"],
                }
            )

    try:
        presigned_url = s3_client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": s3_key,
                "ContentType": "video/mp4",
            },
            ExpiresIn=S3_PRESIGN_EXPIRES,
        )
    except ClientError as e:
        # detail 필드는 IAM/S3 내부 메시지 leak — 로그에만 남기고 응답에는 노출 X
        app.logger.error("presign_failed for clip %s: %s", clip_uuid, e)
        return jsonify({"error": "presign_failed"}), 500

    return jsonify({
        "clip_id": clip_uuid,
        "s3_key": s3_key,
        "presigned_url": presigned_url,
        "expires_in": S3_PRESIGN_EXPIRES,
    }), 201


@clips_bp.post("/api/v1/clips/confirm")
def confirm_clip_upload():
    """1차 AI가 S3 업로드 완료 후 호출. S3 객체 존재/크기 확인 후 clips 메타를 'uploaded'로 확정."""
    require_token()

    if s3_client is None or not S3_BUCKET:
        return jsonify({"error": "s3_not_configured"}), 503

    body = request.get_json(force=True, silent=False) or {}

    clip_id             = (body.get("clip_id") or "").strip()
    actual_duration_sec = maybe_float(body.get("actual_duration_sec"))

    if not clip_id:
        return jsonify({"error": "clip_id_required"}), 400

    # file_size_bytes 캐스팅 가드 (잘못된 입력 시 500이 아닌 400)
    file_size_bytes_raw = body.get("file_size_bytes")
    file_size_bytes: Optional[int] = None
    if file_size_bytes_raw is not None:
        try:
            file_size_bytes = int(file_size_bytes_raw)
        except (ValueError, TypeError):
            return jsonify({"error": "invalid_file_size_bytes"}), 400
        if file_size_bytes < 0:
            return jsonify({"error": "invalid_file_size_bytes"}), 400

    # 1. pending 상태인 클립의 s3_key 조회 (없거나 이미 확정됐으면 404)
    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT s3_key
                  FROM clips
                 WHERE clip_uuid = :clip_uuid
                   AND status    = 'pending'
            """),
            {"clip_uuid": clip_id}
        ).first()

    if existing is None:
        return jsonify({"error": "clip_not_found_or_already_confirmed"}), 404

    s3_key = existing[0]

    # 2. S3에 실제 객체 존재 확인 + 크기 검증
    # 주의: 우리 IAM 정책에 s3:ListBucket이 없어서, 객체 없을 때 AWS가 404 대신
    # 403 Forbidden을 반환함 (object enumeration 방지 보안 기능). 두 케이스 모두
    # "object not found"로 처리.
    try:
        head_resp = s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if status in (403, 404) or code in ("404", "403", "NoSuchKey", "NotFound", "Forbidden", "AccessDenied"):
            return jsonify({"error": "s3_object_not_found"}), 409
        app.logger.error("s3_head_failed for clip %s: %s", clip_id, e)
        return jsonify({"error": "s3_head_failed"}), 502

    # S3에 보고된 실제 크기로 제한 검증 (presigned PUT URL 자체는 size 제약 불가,
    # 사후 검증으로 무제한 업로드 / S3 비용 폭주 차단)
    actual_size = int(head_resp.get("ContentLength") or 0)
    if actual_size > MAX_CLIP_SIZE_BYTES:
        app.logger.warning(
            "clip_too_large: clip %s, actual=%d, max=%d",
            clip_id, actual_size, MAX_CLIP_SIZE_BYTES
        )
        return jsonify({"error": "clip_too_large"}), 413

    # 3. uploaded로 마킹 (status='pending' 재확인으로 race condition 방어)
    # 클라이언트가 보고한 size보다 S3 실제 size를 신뢰 (변조 방지)
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                UPDATE clips
                   SET file_size_bytes     = :file_size_bytes,
                       actual_duration_sec = :actual_duration_sec,
                       uploaded_at         = now(),
                       status              = 'uploaded'
                 WHERE clip_uuid = :clip_uuid
                   AND status    = 'pending'
                RETURNING id
            """),
            {
                "clip_uuid": clip_id,
                "file_size_bytes": actual_size,
                "actual_duration_sec": actual_duration_sec,
            }
        ).fetchone()

    # 동시에 들어온 두 번째 confirm이 먼저 UPDATE 했을 경우 (race) → 404
    if row is None:
        return jsonify({"error": "clip_not_found_or_already_confirmed"}), 404

    return jsonify({"clip_id": clip_id, "stored": True}), 200


@clips_bp.get("/api/v1/clips")
@jwt_required()
def list_clips():
    """프론트용 클립 목록 조회. status='uploaded'(확정)만 노출 + 각 클립의 재생용
    presigned GET URL 발급. guardian은 매핑된 환자만, admin은 전체."""
    role    = get_current_role()
    user_id = int(get_jwt_identity())

    limit        = clamp_int(request.args.get("limit", 50), 1, 200, 50)
    patient_code = request.args.get("patient_id")
    event_type   = request.args.get("event_type")

    where  = ["c.status = 'uploaded'"]   # pending(미확정) 클립은 재생 불가 → 제외
    params: Dict[str, Any] = {"limit": limit}
    if CLIP_RETENTION_DAYS > 0:
        # S3 lifecycle이 지웠을 수 있는 클립은 scheduler/clip_expirer가 expired로
        # 바꾸기 전이라도 숨긴다 — 죽은 presigned URL을 내보내지 않기 위한 방어.
        # 경계에서 살아있는 클립을 몇 시간 일찍 숨길 수는 있어도 죽은 걸 보여주진 않는다.
        where.append("COALESCE(c.uploaded_at, c.created_at) >= now() - (:retention_days * interval '1 day')")
        params["retention_days"] = CLIP_RETENTION_DAYS

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

        if event_type:
            where.append("c.event_type = :event_type")
            params["event_type"] = event_type

        where_sql = "WHERE " + " AND ".join(where)

        rows = conn.execute(text(f"""
            SELECT c.id, c.clip_uuid, p.patient_code, d.device_key,
                   c.event_type, c.s3_key, c.status, c.occurred_at, c.uploaded_at,
                   c.requested_duration_sec, c.actual_duration_sec,
                   c.file_size_bytes, c.related_alert_id
            FROM clips c
            LEFT JOIN patients p ON c.patient_id = p.id
            LEFT JOIN devices  d ON c.device_id  = d.id
            {where_sql}
            ORDER BY c.occurred_at DESC
            LIMIT :limit
        """), params).mappings().all()

    return jsonify({"items": [clip_row_to_dict(r) for r in rows]})
