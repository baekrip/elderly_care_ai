"""DB row → JSON 응답 변환.

**환자 식별자 두 형태를 혼동하지 말 것** — 이 모듈이 그 경계다:
  - patient_code (문자열) : 외부 식별자. JSON에서는 `patient_id` 라는 이름으로 나간다.
  - patients.id  (정수)   : 내부 FK. events.patient_id 등이 참조한다.
여기 빌더들이 내부 patient_code 컬럼을 JSON의 `patient_id`로 되돌려 놓는다.
이 규약을 유지할 것.

모든 시각은 ISO-8601 UTC 문자열로 내보내며 필드명에 _utc 접미사를 붙인다.
"""
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError
from flask import current_app

from .config import CLIP_VIEW_URL_EXPIRES, S3_BUCKET
from .s3 import s3_client


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
    """클립 재생용 presigned GET URL 발급. S3 미설정/발급 실패 시 None (목록 자체는 살림).

    app.logger 대신 current_app.logger를 쓴다 — 이 함수는 요청 처리 중에만
    불리므로 요청 컨텍스트가 보장되고, app 객체를 import하지 않아도 되어
    순환 import가 생기지 않는다. (스케줄러 쪽은 요청 밖이라 이 방법을 못 쓴다)
    """
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
        current_app.logger.error("presign_get_failed for %s: %s", s3_key, e)
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
