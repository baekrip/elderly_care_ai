import os
import re
import uuid
import bcrypt
import boto3
import requests as http_requests
from botocore.config import Config
from botocore.exceptions import ClientError
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity, get_jwt
)
from sqlalchemy import create_engine, text
from psycopg.types.json import Jsonb
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

# ---------------------------
# Config
# ---------------------------
APP_TOKEN      = os.getenv("APP_TOKEN")
DATABASE_URL   = os.getenv("DATABASE_URL")
JWT_SECRET     = os.getenv("JWT_SECRET")
AI2_SERVER_URL = os.getenv("AI2_SERVER_URL", "")

# AWS S3 (위험 클립 저장용 — 미설정 시 clips API는 503 반환)
AWS_REGION         = os.getenv("AWS_REGION", "ap-northeast-2")
S3_BUCKET          = os.getenv("S3_BUCKET", "")
S3_PRESIGN_EXPIRES = int(os.getenv("S3_PRESIGN_EXPIRES", "300"))
# 클립 재생용 presigned GET URL 만료 (업로드 PUT보다 길게 — 프론트가 목록 보다 클릭/재생까지 여유)
CLIP_VIEW_URL_EXPIRES = int(os.getenv("CLIP_VIEW_URL_EXPIRES", "3600"))
# 클립 보관 기간(일) — S3 버킷 lifecycle 만료 규칙과 맞춘다 (capstone-elderly-clips: 30일).
# lifecycle이 객체를 지워도 DB row가 status='uploaded'로 남으면 목록 API가 죽은 presigned
# URL을 내보내 프론트 재생이 403으로 실패한다 (2026-09-18 실발현, 8/9 클립 79건 전부).
# 0 이하면 만료 처리(스케줄러 + 목록 필터) 비활성.
CLIP_RETENTION_DAYS = int(os.getenv("CLIP_RETENTION_DAYS", "30"))

# 클립 업로드 최대 크기 (50MB = 5초 1080p H.264 충분, 악성 무제한 업로드 방지)
MAX_CLIP_SIZE_BYTES = 50 * 1024 * 1024

# 식별자 화이트리스트 (S3 key prefix 안전성 + DB injection 방지)
# 영문자/숫자/하이픈/언더스코어만 허용, 길이 1~64자
IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

if not APP_TOKEN or not DATABASE_URL or not JWT_SECRET:
    raise RuntimeError("필수 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

# S3 클라이언트는 버킷명이 설정된 경우에만 초기화. AWS 자격증명은 boto3가
# 환경변수(AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY) 또는 IAM 역할에서 자동 조회.
# ap-northeast-2 같은 비(非)us-east-1 리전은 regional endpoint + SigV4 필수
# (default boto3는 https://<bucket>.s3.amazonaws.com 로 생성해 SignatureDoesNotMatch 발생)
s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION,
    endpoint_url=f"https://s3.{AWS_REGION}.amazonaws.com",
    config=Config(signature_version="s3v4"),
) if S3_BUCKET else None

app = Flask(__name__)
CORS(app)

app.config["JWT_SECRET_KEY"] = JWT_SECRET
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=12)
jwt = JWTManager(app)


# ---------------------------
# 2차 AI 분석 트리거 스케줄러
# ---------------------------
def trigger_ai2_analysis():
    if not AI2_SERVER_URL:
        return
    try:
        http_requests.post(
            f"{AI2_SERVER_URL}/analyze",
            json={"trigger": "scheduled", "ts_utc": datetime.now(timezone.utc).isoformat()},
            headers={"Authorization": f"Bearer {APP_TOKEN}"},
            timeout=10
        )
    except Exception:
        pass

# ---------------------------
# Scheduler 워커 간 중복 실행 가드 (pg_try_advisory_lock)
# ---------------------------
# gunicorn -w N 환경에서 BackgroundScheduler가 워커마다 별도 인스턴스화되어
# 같은 job이 N번 동시 실행되는 문제 방지.
# advisory lock은 세션(connection) 단위 — 잡은 connection이 닫힐 때까지 보유.
# 다른 워커는 try_lock이 False 반환하면 즉시 skip.
LOCK_KEY_SWEEPER    = 7001
LOCK_KEY_AGGREGATOR = 7002
LOCK_KEY_CLIP_EXPIRER = 7003


# ---------------------------
# Silent Orphan Sweeper (soft mode)
# ---------------------------
# 매일 1회 NULL FK row 감지 + 로그. 자동 삭제/거부는 안 함 (1차 AI 통신 안전성 우선).
# STRICT 모드(미등록 device_key 거부)는 향후 환경변수 토글 예정.
def detect_orphan_data():
    """매일 09:00 KST(00:00 UTC) 실행. devices/events/patients의 NULL FK 감지."""
    try:
        with engine.connect() as conn:
            got = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"),
                {"k": LOCK_KEY_SWEEPER}
            ).scalar()
            if not got:
                # 다른 워커가 이미 실행 중. 중복 로그 방지.
                return

            try:
                stats = conn.execute(text("""
                    SELECT
                        (SELECT count(*) FROM devices  WHERE household_id IS NULL) AS orphan_devices,
                        (SELECT count(*) FROM events   WHERE household_id IS NULL) AS orphan_events,
                        (SELECT count(*) FROM patients WHERE household_id IS NULL) AS orphan_patients,
                        (SELECT count(*) FROM events   WHERE camera_id    IS NULL) AS events_no_camera
                """)).mappings().one()

                total = (
                    stats["orphan_devices"]
                    + stats["orphan_events"]
                    + stats["orphan_patients"]
                    + stats["events_no_camera"]
                )

                if total > 0:
                    # device_key 샘플 (디버깅용, 최대 5건)
                    samples = conn.execute(text("""
                        SELECT device_key, created_at
                          FROM devices
                         WHERE household_id IS NULL
                         ORDER BY created_at DESC
                         LIMIT 5
                    """)).mappings().all()
                    sample_keys = [s["device_key"] for s in samples]

                    app.logger.warning(
                        "[Sweeper] Orphan detected — "
                        "devices=%d, events(household NULL)=%d, "
                        "events(camera NULL)=%d, patients=%d, sample_device_keys=%s",
                        stats["orphan_devices"], stats["orphan_events"],
                        stats["events_no_camera"], stats["orphan_patients"],
                        sample_keys,
                    )
                else:
                    app.logger.info("[Sweeper] No orphans detected.")
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": LOCK_KEY_SWEEPER}
                )
    except Exception as e:
        app.logger.error("[Sweeper] failed: %s", e)


# ---------------------------
# Clip Expirer — S3 lifecycle로 지워진 클립 row 정리
# ---------------------------
# S3 lifecycle(CLIP_RETENTION_DAYS)이 객체를 지워도 DB는 모른다. 그 row가 status='uploaded'로
# 남으면 list_clips가 죽은 presigned URL을 내보내 프론트 재생이 403으로 실패한다.
# 매일 1회, 보관 기간을 넘긴 row를 status='expired'로 바꿔 목록(uploaded만 노출)에서 뺀다.
# row 자체는 남긴다 (이력·감사용, 되돌리기 가능).
#
# S3에 HEAD로 실존 여부를 묻지 않는 이유: IAM 사용자에 ListBucket이 없어 '객체 없음'도 403으로
# 와서 권한 오류와 구분이 안 된다. lifecycle은 만료일 다음 자정(UTC)에 도니 하루 여유
# (CLIP_RETENTION_DAYS + 1)를 두고 시간만으로 판단한다 — 살아있는 클립을 expired로 잘못
# 바꾸는 쪽이 더 나쁜 버그이므로 보수적으로 잡는다. 그 사이 하루의 공백은 list_clips의
# 나이 필터가 메운다.
def expire_lifecycle_deleted_clips():
    """매일 00:10 UTC 실행. 보관 기간을 넘긴 uploaded 클립을 expired로 전환."""
    if CLIP_RETENTION_DAYS <= 0:
        return
    try:
        with engine.begin() as conn:
            # 트랜잭션 스코프 advisory lock — commit/rollback 시 자동 해제 (unlock 누락 위험 0)
            got = conn.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": LOCK_KEY_CLIP_EXPIRER}
            ).scalar()
            if not got:
                return
            rows = conn.execute(text("""
                UPDATE clips
                   SET status = 'expired'
                 WHERE status = 'uploaded'
                   AND COALESCE(uploaded_at, created_at) < now() - (:days * interval '1 day')
             RETURNING id
            """), {"days": CLIP_RETENTION_DAYS + 1}).all()
        if rows:
            app.logger.warning(
                "[ClipExpirer] %d clip(s) marked expired (retention %dd) ids=%s",
                len(rows), CLIP_RETENTION_DAYS, [int(r[0]) for r in rows[:20]],
            )
        else:
            app.logger.info("[ClipExpirer] nothing to expire.")
    except Exception as e:
        app.logger.error("[ClipExpirer] failed: %s", e)


# ---------------------------
# Incidents 스트림 집계 (Medallion Silver, migration 007)
# ---------------------------
# raw events(Bronze) → incidents(Silver) 자동 집계.
# 정책:
#   - 같은 patient + 같은 event_type
#   - 시간 갭 <= 10초 = 같은 incident
#   - 15초 grace period (created_at >= NOW() - 15s 인 raw는 다음 사이클로 미룸,
#     진행 중 burst를 prematurely 잘라서 1 사건 = 2 incidents로 쪼개지는 거 방지)
#   - 매 30초마다 실행 → 보호자 알림 지연 최대 ~45초 (15s grace + 30s cycle)
INCIDENT_GAP_THRESHOLD_SEC = 10
INCIDENT_GRACE_PERIOD_SEC  = 15
# 한 incident 최대 지속시간. 연속 검출 스트림이 10초 갭에 안 끊겨 수십 분짜리
# 거대 incident로 뭉치는 것 방지 ("한 사건"의 상식적 길이로 분리). 초과하면
# 같은 환자+event_type이라도 새 incident로 시작.
INCIDENT_MAX_DURATION_SEC  = 300  # 5분


def _flush_incident_group(conn, g: Dict[str, Any]) -> None:
    """한 그룹 (incidents INSERT + events.incident_id UPDATE) 처리.
    호출자가 transaction을 열어둔 상태에서 실행 — 실패 시 raise해서 그룹 단위로 롤백.
    """
    count = len(g["event_ids"])
    avg_conf = g["confidence_sum"] / count if count > 0 else 0.0

    new_id = conn.execute(text("""
        INSERT INTO incidents (
            patient_id, household_id, incident_type,
            started_at, ended_at, raw_event_count,
            max_confidence, max_severity, avg_confidence,
            first_event_id, last_event_id, status
        )
        VALUES (
            :patient_id, :household_id, :incident_type,
            :started_at, :ended_at, :raw_event_count,
            :max_confidence, :max_severity, :avg_confidence,
            :first_event_id, :last_event_id, 'closed'
        )
        RETURNING id
    """), {
        "patient_id":      g["patient_id"],
        "household_id":    g["household_id"],
        "incident_type":   g["event_type"],
        "started_at":      g["first_at"],
        "ended_at":        g["last_at"],
        "raw_event_count": count,
        "max_confidence":  g["max_confidence"],
        "max_severity":    g["max_severity"],
        "avg_confidence":  avg_conf,
        "first_event_id":  g["first_event_id"],
        "last_event_id":   g["last_event_id"],
    }).scalar_one()

    conn.execute(text("""
        UPDATE events SET incident_id = :inc_id
         WHERE id = ANY(:event_ids)
    """), {
        "inc_id":    int(new_id),
        "event_ids": g["event_ids"],
    })


def aggregate_events_into_incidents():
    """매 30초 실행. incident_id NULL인 raw events를 시간 윈도우로 그룹핑 후 incidents INSERT.

    워커 간 중복: pg_try_advisory_lock(LOCK_KEY_AGGREGATOR)으로 한 워커만 실행.
    그룹 간 격리: 그룹별 독립 nested transaction (savepoint) — 한 그룹 실패가
                 다른 그룹 롤백시키지 않음.
    """
    try:
        with engine.connect() as conn:
            got = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"),
                {"k": LOCK_KEY_AGGREGATOR}
            ).scalar()
            if not got:
                return  # 다른 워커가 처리 중

            try:
                # 1단계: 처리 대상 raw events 추출 (read)
                rows = conn.execute(text("""
                    SELECT id, patient_id, household_id, event_type,
                           occurred_at, confidence, severity
                      FROM events
                     WHERE incident_id IS NULL
                       AND patient_id IS NOT NULL
                       AND created_at < NOW() - (:grace_sec || ' seconds')::interval
                     ORDER BY patient_id, event_type, occurred_at
                """), {"grace_sec": INCIDENT_GRACE_PERIOD_SEC}).mappings().all()

                # 읽기로 시작된 autobegin 트랜잭션을 닫는다. 이게 없으면 아래
                # 그룹별 `with conn.begin():`이 "이미 트랜잭션이 시작됨" 에러로 전부
                # 실패해 incident가 하나도 생성되지 않음(미집계 raw 누적).
                # 세션 스코프 advisory lock(pg_try_advisory_lock)은 rollback에도
                # 유지되므로 워커 간 중복 가드는 그대로 동작.
                conn.rollback()

                if not rows:
                    return

                # 2단계: 그룹핑 (메모리 안에서, DB I/O 없음)
                groups = []
                current = None
                for evt in rows:
                    same_stream = (
                        current is not None
                        and current["patient_id"] == evt["patient_id"]
                        and current["event_type"] == evt["event_type"]
                        and (evt["occurred_at"] - current["last_at"]).total_seconds() <= INCIDENT_GAP_THRESHOLD_SEC
                        # 지속시간 cap: 첫 이벤트로부터 5분 넘으면 새 incident로 분리
                        and (evt["occurred_at"] - current["first_at"]).total_seconds() <= INCIDENT_MAX_DURATION_SEC
                    )

                    if same_stream:
                        current["event_ids"].append(int(evt["id"]))
                        current["last_at"] = evt["occurred_at"]
                        current["last_event_id"] = int(evt["id"])
                        conf = float(evt["confidence"] or 0)
                        sev  = int(evt["severity"] or 0)
                        if conf > current["max_confidence"]:
                            current["max_confidence"] = conf
                        if sev  > current["max_severity"]:
                            current["max_severity"] = sev
                        current["confidence_sum"] += conf
                    else:
                        if current:
                            groups.append(current)
                        current = {
                            "patient_id":      int(evt["patient_id"]),
                            "household_id":    int(evt["household_id"]) if evt["household_id"] is not None else None,
                            "event_type":      evt["event_type"],
                            "event_ids":       [int(evt["id"])],
                            "first_event_id":  int(evt["id"]),
                            "last_event_id":   int(evt["id"]),
                            "first_at":        evt["occurred_at"],
                            "last_at":         evt["occurred_at"],
                            "max_confidence":  float(evt["confidence"] or 0),
                            "max_severity":    int(evt["severity"] or 0),
                            "confidence_sum":  float(evt["confidence"] or 0),
                        }
                if current:
                    groups.append(current)

                # 3단계: 그룹별 독립 트랜잭션 — 한 그룹 실패해도 다른 그룹은 살아남음
                new_incidents = 0
                updated_events = 0
                failed_groups = 0
                for g in groups:
                    try:
                        with conn.begin():
                            _flush_incident_group(conn, g)
                        new_incidents  += 1
                        updated_events += len(g["event_ids"])
                    except Exception as e:
                        failed_groups += 1
                        app.logger.error(
                            "[Aggregator] group failed (patient=%s, type=%s, raw=%d): %s",
                            g["patient_id"], g["event_type"], len(g["event_ids"]), e,
                        )
                        # continue — 다음 그룹 계속

                if new_incidents > 0 or failed_groups > 0:
                    app.logger.info(
                        "[Aggregator] %d incidents created from %d raw events "
                        "(failed groups: %d)",
                        new_incidents, updated_events, failed_groups,
                    )
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": LOCK_KEY_AGGREGATOR}
                )
    except Exception as e:
        app.logger.error("[Aggregator] failed: %s", e)


scheduler = BackgroundScheduler()
scheduler.add_job(trigger_ai2_analysis, "interval", minutes=10)
scheduler.add_job(detect_orphan_data, "cron", hour=0, minute=0)  # 매일 00:00 UTC = 09:00 KST
scheduler.add_job(expire_lifecycle_deleted_clips, "cron", hour=0, minute=10)  # sweeper 직후
scheduler.add_job(aggregate_events_into_incidents, "interval", seconds=30, max_instances=1)
scheduler.start()

# ---------------------------
# Auth: AI팀 Bearer 토큰
# ---------------------------
def require_token() -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        abort(401, description="Missing Bearer token")

    token = auth.split(" ", 1)[1].strip()
    if token != APP_TOKEN:
        abort(403, description="Invalid token")


# ---------------------------
# Auth: JWT 권한 체크 헬퍼
# ---------------------------
def get_current_role() -> str:
    return get_jwt().get("role", "guardian")


def check_patient_access(conn, user_id: int, patient_code: str) -> bool:
    row = conn.execute(
        text("""
            SELECT 1 FROM patient_guardians pg
            JOIN patients p ON pg.patient_id = p.id
            WHERE pg.user_id = :user_id AND p.patient_code = :patient_code
        """),
        {"user_id": user_id, "patient_code": patient_code}
    ).first()
    return row is not None


def guardian_patient_codes(conn, user_id: int):
    rows = conn.execute(
        text("""
            SELECT p.patient_code FROM patient_guardians pg
            JOIN patients p ON pg.patient_id = p.id
            WHERE pg.user_id = :user_id
        """),
        {"user_id": user_id}
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------
# Utils
# ---------------------------
def parse_iso8601(ts_str: Optional[str]) -> datetime:
    if not ts_str:
        return datetime.now(timezone.utc)

    s = ts_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def clamp_int(val: Any, lo: int, hi: int, default: int) -> int:
    try:
        i = int(val)
        return max(lo, min(hi, i))
    except Exception:
        return default


def maybe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None


# ---------------------------
# FK helpers
# ---------------------------
def get_or_create_device(conn, device_key: str) -> int:
    """device_key로 디바이스 조회 또는 생성. ON CONFLICT로 TOCTOU race 차단 (audit Logic H-2).

    DO UPDATE SET (no-op)은 RETURNING id를 보장하기 위한 idiomatic 패턴.
    DO NOTHING은 conflict 시 RETURNING이 빈 결과를 줘서 follow-up SELECT 필요.
    """
    return int(conn.execute(
        text("""
            INSERT INTO devices (device_key, status)
            VALUES (:device_key, 'active')
            ON CONFLICT (device_key) DO UPDATE SET device_key = EXCLUDED.device_key
            RETURNING id
        """),
        {"device_key": device_key}
    ).scalar_one())


def get_or_create_patient(conn, patient_code: Optional[str]) -> Optional[int]:
    """patient_code로 환자 조회 또는 생성. ON CONFLICT로 TOCTOU race 차단 (audit Logic H-2)."""
    if not patient_code:
        return None

    return int(conn.execute(
        text("""
            INSERT INTO patients (patient_code, status)
            VALUES (:patient_code, 'active')
            ON CONFLICT (patient_code) DO UPDATE SET patient_code = EXCLUDED.patient_code
            RETURNING id
        """),
        {"patient_code": patient_code}
    ).scalar_one())


# ---------------------------
# Common response builders
# ---------------------------
def event_row_to_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    # 1차 AI 추론 결과는 payload_json 안에 들어옴. 프론트가 top-level로 기대하는
    # 라벨들을 평탄화해서 같이 노출.
    #   - activity_label ← source_label (행동·상황 분류, event_type보다 세분화)
    #   - risk_label     ← risk_label   (위험 등급: suspicious/danger/normal)
    #   - risk_score     ← risk_score   (1차 AI 계약: 위험도별 결정값 1~5. 옛 데이터는 0~1 float)
    #   - raw_score      ← raw_score    (원본 raw 값 0~1. 현재는 참고용, 설계 정리 후 활용)
    payload = r["payload_json"] if isinstance(r["payload_json"], dict) else {}
    return {
        "id": int(r["id"]),
        "device_key": r["device_key"],
        "patient_id": r["patient_code"],
        "event_type": r["event_type"],
        "confidence": r["confidence"],
        "severity": int(r["severity"]),
        "event_status": r["event_status"],
        "ts_utc": r["occurred_at"].isoformat(),
        "clip_url": r["clip_url"],
        "activity_label": payload.get("source_label"),
        "risk_label": payload.get("risk_label"),
        "risk_score": payload.get("risk_score"),
        "raw_score": payload.get("raw_score"),
        "payload": r["payload_json"],
    }


def alert_row_to_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(r["id"]),
        "source": r["source"],
        "device_key": r["device_key"],
        "patient_id": r["patient_code"],
        "alert_type": r["alert_type"],
        "level": r["alert_level"],
        "message": r["message"],
        "ref_event_id": r["event_id"],
        "ts_utc": r["occurred_at"].isoformat(),
        "payload": r["payload_json"],
        "is_read": bool(r["is_read"]),
    }


def risk_score_row_to_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(r["id"]),
        "patient_id": r["patient_code"],
        "score": int(r["score"]),
        "risk_level": r["risk_level"],
        "reason": r["reason"],
        "analyzed_from_utc": r["analyzed_from"].isoformat() if r["analyzed_from"] else None,
        "analyzed_to_utc": r["analyzed_to"].isoformat() if r["analyzed_to"] else None,
        "created_at_utc": r["created_at"].isoformat(),
    }


def incident_row_to_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    # 사건(Silver) 응답 빌더. /incidents 목록과 /dashboard recent_incidents가 공유.
    # 행에는 incidents 컬럼 + patient_code(JOIN)가 있어야 함.
    return {
        "incident_id":     int(r["id"]),
        "patient_id":      r["patient_code"],
        "incident_type":   r["incident_type"],
        "started_at_utc":  r["started_at"].isoformat(),
        "ended_at_utc":    r["ended_at"].isoformat(),
        "duration_sec":    round((r["ended_at"] - r["started_at"]).total_seconds(), 2),
        "raw_event_count": int(r["raw_event_count"]),
        "max_confidence":  float(r["max_confidence"]) if r["max_confidence"] is not None else None,
        "max_severity":    int(r["max_severity"])    if r["max_severity"]   is not None else None,
        "avg_confidence":  float(r["avg_confidence"]) if r["avg_confidence"] is not None else None,
        "status":          r["status"],
        "created_at_utc":  r["created_at"].isoformat(),
    }


def clip_presigned_get_url(s3_key: Optional[str]) -> Optional[str]:
    """클립 재생용 presigned GET URL 발급. S3 미설정/발급 실패 시 None (목록 자체는 살림)."""
    if s3_client is None or not S3_BUCKET or not s3_key:
        return None
    try:
        return s3_client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_key},
            ExpiresIn=CLIP_VIEW_URL_EXPIRES,
        )
    except ClientError as e:
        # IAM/S3 내부 메시지 leak 방지 — 로그만, 응답엔 None
        app.logger.error("presign_get_failed for %s: %s", s3_key, e)
        return None


def clip_row_to_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(r["id"]),
        "clip_id": str(r["clip_uuid"]),
        "patient_id": r["patient_code"],
        "device_key": r["device_key"],
        "event_type": r["event_type"],
        "status": r["status"],
        "occurred_at_utc": r["occurred_at"].isoformat() if r["occurred_at"] else None,
        "uploaded_at_utc": r["uploaded_at"].isoformat() if r["uploaded_at"] else None,
        "requested_duration_sec": int(r["requested_duration_sec"]) if r["requested_duration_sec"] is not None else None,
        "actual_duration_sec": float(r["actual_duration_sec"]) if r["actual_duration_sec"] is not None else None,
        "file_size_bytes": int(r["file_size_bytes"]) if r["file_size_bytes"] is not None else None,
        "related_alert_id": int(r["related_alert_id"]) if r["related_alert_id"] is not None else None,
        "video_url": clip_presigned_get_url(r["s3_key"]),
        "url_expires_in": CLIP_VIEW_URL_EXPIRES,
    }


# ---------------------------
# Health
# ---------------------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "db": "postgres"
    }


# ==========================
# 인증 API
# ==========================

@app.post("/api/v1/auth/login")
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


@app.post("/api/v1/admin/patient-assign")
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


@app.post("/api/v1/admin/users/register")
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


@app.get("/api/v1/admin/users")
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


@app.delete("/api/v1/admin/users/<int:user_id>")
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


@app.post("/api/v1/admin/patient-unassign")
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

ALLOWED_DEVICE_TYPES = {"camera", "raspberry_pi", "jetson", "other"}


def _resolve_device_context(conn, device_fk: int) -> Dict[str, Optional[int]]:
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


# --------------------------
# Households
# --------------------------

@app.post("/api/v1/admin/households/register")
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


@app.get("/api/v1/admin/households")
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


# --------------------------
# Patients (admin 등록 + 가구 매핑)
# --------------------------

@app.post("/api/v1/admin/patients/register")
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


@app.post("/api/v1/admin/patients/<patient_code>/assign-household")
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


# --------------------------
# Devices (cameras 자동 동기화)
# --------------------------

@app.post("/api/v1/admin/devices/register")
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


@app.get("/api/v1/admin/devices")
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


# REMOVED (v2 정규화 모델):
#   POST   /api/v1/admin/device-assign
#   DELETE /api/v1/admin/device-assign
# 이유: patient ↔ device M:N junction(patient_devices)을 migration 006에서 drop.
#       대신 patient → household → device 흐름:
#         1) POST /api/v1/admin/households/register      가구 등록
#         2) POST /api/v1/admin/patients/register        환자 등록 (home_code 동시 매핑 가능)
#            또는 POST /api/v1/admin/patients/<code>/assign-household  매핑 변경
#         3) POST /api/v1/admin/devices/register         디바이스 등록 (home_code로 가구 매핑)


# ==========================
# 2차 AI용 일별 요약 API
# ==========================

@app.get("/api/v1/daily-summary")
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


# ==========================
# Ingest API (AI팀 Bearer 토큰)
# ==========================


@app.post("/api/v1/events/batch")
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
            ctx = _resolve_device_context(conn, device_id)

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


@app.post("/api/v1/alerts/immediate")
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
        ctx        = _resolve_device_context(conn, device_id)

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


@app.post("/api/v1/alerts/trend")
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
        ctx        = _resolve_device_context(conn, device_id)

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


@app.post("/api/v1/risk-scores")
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


# ==========================
# 위험 클립 (1차 AI → S3 Presigned URL 방식)
# ==========================

@app.post("/api/v1/clips/upload-url")
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
            ctx = _resolve_device_context(conn, device_fk)

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


@app.post("/api/v1/clips/confirm")
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


# ==========================
# Query API (JWT 인증 - 프론트용)
# ==========================

@app.get("/api/v1/events")
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


@app.get("/api/v1/incidents")
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


@app.get("/api/v1/alerts")
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


@app.get("/api/v1/risk-scores")
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


@app.get("/api/v1/clips")
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
        # S3 lifecycle이 지웠을 수 있는 클립은 expire_lifecycle_deleted_clips가 expired로
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


@app.get("/api/v1/patients")
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


@app.get("/api/v1/dashboard")
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


# ---------------------------
# Entry
# ---------------------------
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true"
    )
