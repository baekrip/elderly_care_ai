"""특성화 테스트 (characterization test).

목적은 "이렇게 동작해야 한다"가 아니라 **"지금 실제로 이렇게 동작한다"**를
박제하는 것이다. app.py 2,316줄을 모듈로 쪼갠 뒤 이 테스트가 전부 그대로
통과하면, 외부에서 본 동작이 바뀌지 않았다는 뜻이 된다.

그래서 여기 담긴 기대값 중에는 "바람직하지 않지만 현재 그런 것"도 있다.
그런 항목은 주석으로 표시해 뒀다. 고치는 건 모듈화가 끝나고 동등성이
증명된 다음의 별도 작업이다 — 리팩터링과 버그 수정을 섞으면 무엇이
무엇을 깨뜨렸는지 알 수 없게 된다.
"""
import time
import uuid

import pytest
import requests

from conftest import api

TIMEOUT = 10


# ---------------------------------------------------------------
# health
# ---------------------------------------------------------------
def test_health_ok():
    r = requests.get(api("/health"), timeout=TIMEOUT)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["db"] == "postgres"
    # DB 왕복이 실제로 성공해야 나오는 필드
    assert "time_utc" in body


# ---------------------------------------------------------------
# 인증 — JWT 발급
# ---------------------------------------------------------------
def test_login_guardian_returns_role_claim():
    r = requests.post(api("/api/v1/auth/login"), json={
        "email": "guardian@local.test", "password": "localguardian",
    }, timeout=TIMEOUT)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "guardian"
    assert body["email"] == "guardian@local.test"
    assert body["user_id"] == 2
    assert body["access_token"]


def test_login_admin_returns_role_claim():
    r = requests.post(api("/api/v1/auth/login"), json={
        "email": "admin@local.test", "password": "localadmin",
    }, timeout=TIMEOUT)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "admin"
    assert body["user_id"] == 1


def test_login_email_is_case_insensitive():
    # login()이 email을 .strip().lower() 하므로 대문자로 보내도 통과한다.
    r = requests.post(api("/api/v1/auth/login"), json={
        "email": "  GUARDIAN@LOCAL.TEST  ", "password": "localguardian",
    }, timeout=TIMEOUT)
    assert r.status_code == 200
    assert r.json()["role"] == "guardian"


def test_login_wrong_password_is_401():
    r = requests.post(api("/api/v1/auth/login"), json={
        "email": "guardian@local.test", "password": "틀린비번",
    }, timeout=TIMEOUT)
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_credentials"}


def test_login_unknown_email_is_401_with_same_message():
    # 존재하지 않는 계정도 "invalid_credentials" — 계정 존재 여부를 흘리지 않는다.
    r = requests.post(api("/api/v1/auth/login"), json={
        "email": "nobody@local.test", "password": "whatever",
    }, timeout=TIMEOUT)
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_credentials"}


def test_login_missing_fields_is_400():
    r = requests.post(api("/api/v1/auth/login"), json={}, timeout=TIMEOUT)
    assert r.status_code == 400
    assert r.json() == {"error": "email_and_password_required"}


# ---------------------------------------------------------------
# 인증 — 두 체계가 섞이지 않는지
#   require_token(): 정적 APP_TOKEN (AI/엣지 수신용)
#   @jwt_required(): 사용자 JWT (프론트 조회용)
# ---------------------------------------------------------------
def test_query_endpoint_without_jwt_is_401():
    r = requests.get(api("/api/v1/events"), timeout=TIMEOUT)
    assert r.status_code == 401
    # flask-jwt-extended 자체 포맷 — {"error": ...} 규약이 아니다 (현재 동작)
    assert r.json()["msg"] == "Missing Authorization Header"


def test_ingest_without_token_is_401():
    r = requests.post(api("/api/v1/events/batch"), json={"events": []}, timeout=TIMEOUT)
    assert r.status_code == 401


def test_ingest_with_wrong_token_is_403_and_returns_html_not_json():
    """현재 동작 박제 — 바람직하진 않음.

    require_token()이 abort(403)을 쓰기 때문에 Flask 기본 HTML 에러 페이지가
    나간다. CLAUDE.md의 {"error": "snake_case"} JSON 규약과 어긋나며,
    1차 AI가 토큰을 틀리면 기계가 읽을 수 없는 응답을 받는다.
    모듈화 이후 별도 과제로 다룰 것 (외부 계약이라 협의 필요).
    """
    # HTTP 헤더는 latin-1만 허용하므로 토큰 값은 ASCII로 둔다.
    r = requests.post(api("/api/v1/events/batch"),
                      headers={"Authorization": "Bearer deadbeefdeadbeefdeadbeefdeadbeef"},
                      json={"events": []}, timeout=TIMEOUT)
    assert r.status_code == 403
    assert "text/html" in r.headers["Content-Type"]


def test_ingest_jwt_does_not_work_as_app_token(guardian_token):
    # 사용자 JWT로 수신 endpoint를 뚫을 수 없어야 한다.
    r = requests.post(api("/api/v1/events/batch"),
                      headers={"Authorization": f"Bearer {guardian_token}"},
                      json={"events": []}, timeout=TIMEOUT)
    assert r.status_code == 403


# ---------------------------------------------------------------
# ingest — 수신 경로
# ---------------------------------------------------------------
def test_ingest_events_batch_stores_event(ingest_headers):
    r = requests.post(api("/api/v1/events/batch"), headers=ingest_headers, json={
        "events": [{
            "device_key": "pi5-home001-cam1",
            "event_type": "fall_detected",
            "patient_id": "P001",
            "ts": "2026-07-17T09:00:00Z",
            "payload": {"confidence": 0.83},
        }]
    }, timeout=TIMEOUT)
    assert r.status_code == 201
    body = r.json()
    assert body["count"] == 1
    assert body["results"][0]["stored"] is True
    assert isinstance(body["results"][0]["event_id"], int)


def test_ingest_silently_skips_events_missing_required_fields(ingest_headers):
    """현재 동작 박제 — CLAUDE.md '미구현' 항목에 적힌 알려진 문제.

    device_key 또는 event_type이 없으면 조용히 건너뛰고(continue) count: 0으로
    201을 반환한다. 외부 협력자가 응답 코드만 보고 "통과"로 오인할 수 있다.
    명시적 400이 정석이나 PRD 계약 변경이라 deferred 상태.
    """
    r = requests.post(api("/api/v1/events/batch"), headers=ingest_headers, json={
        "events": [{"patient_id": "P001", "ts": "2026-07-17T09:00:00Z"}]
    }, timeout=TIMEOUT)
    assert r.status_code == 201
    assert r.json()["count"] == 0


# ---------------------------------------------------------------
# 백그라운드 스케줄러 — HTTP 표면에 안 드러나므로 별도로 확인해야 한다.
#
# 이 테스트가 없으면 스케줄러가 통째로 죽어도 나머지 테스트는 전부 통과한다.
# 실제로 모듈화 중 app.logger 의존성 때문에 이 부분의 구조를 바꿨으므로
# (scheduler/init_scheduler 주입 패턴) 커버가 필요하다.
#
# 느리다(최대 ~45초: 15초 grace + 30초 집계 주기). 급할 때는:
#   pytest -m "not slow"
# ---------------------------------------------------------------
@pytest.mark.slow
def test_scheduler_aggregates_events_into_incidents(ingest_headers, guardian_headers):
    # 다른 테스트 이벤트와 섞이지 않게 고유 event_type을 쓴다.
    event_type = f"synthetic_{uuid.uuid4().hex[:8]}"

    r = requests.post(api("/api/v1/events/batch"), headers=ingest_headers, json={
        "events": [{
            "device_key": "pi5-home001-cam1",
            "event_type": event_type,
            "patient_id": "P001",
            "ts": "2026-07-17T11:00:00Z",
            "payload": {"confidence": 0.9},
        }]
    }, timeout=TIMEOUT)
    assert r.status_code == 201

    # 집계 주기를 기다린다. grace 15초가 지나야 대상이 되고,
    # 그 다음 30초 주기 안에 처리된다.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        r = requests.get(api("/api/v1/incidents"), params={"patient_id": "P001"},
                         headers=guardian_headers, timeout=TIMEOUT)
        assert r.status_code == 200
        match = [i for i in r.json()["items"] if i["incident_type"] == event_type]
        if match:
            assert match[0]["raw_event_count"] == 1
            assert match[0]["status"] == "closed"
            return
        time.sleep(3)

    pytest.fail(f"90초 안에 {event_type} 이 incident로 집계되지 않음 — 스케줄러 확인 필요")


# ---------------------------------------------------------------
# 역할 기반 행 필터링 — 모듈화로 가장 깨지기 쉬운 지점.
# CLAUDE.md: "새 조회 endpoint는 이 필터링을 복제해야 하며,
#             아니면 보호자 간 데이터가 샌다"
# 시드: guardian@local.test ↔ P001 만 매핑. P002는 미매핑.
# ---------------------------------------------------------------
def test_guardian_sees_only_mapped_patients(guardian_headers):
    r = requests.get(api("/api/v1/patients"), headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 200
    codes = [p["patient_id"] for p in r.json()["patients"]]
    assert codes == ["P001"]


def test_admin_sees_all_patients(admin_headers):
    r = requests.get(api("/api/v1/patients"), headers=admin_headers, timeout=TIMEOUT)
    assert r.status_code == 200
    codes = sorted(p["patient_id"] for p in r.json()["patients"])
    assert codes == ["P001", "P002"]


def test_guardian_cannot_query_unmapped_patient(guardian_headers):
    r = requests.get(api("/api/v1/events"), params={"patient_id": "P002"},
                     headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 403
    assert r.json() == {"error": "access_denied"}


def test_guardian_can_query_mapped_patient(guardian_headers):
    r = requests.get(api("/api/v1/events"), params={"patient_id": "P001"},
                     headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 200


def test_admin_can_query_any_patient(admin_headers):
    r = requests.get(api("/api/v1/events"), params={"patient_id": "P002"},
                     headers=admin_headers, timeout=TIMEOUT)
    assert r.status_code == 200


def test_guardian_dashboard_blocked_for_unmapped_patient(guardian_headers):
    r = requests.get(api("/api/v1/dashboard"), params={"patient_id": "P002"},
                     headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 403


# ---------------------------------------------------------------
# 조회 endpoint 응답 shape
# ---------------------------------------------------------------
def test_dashboard_shape(guardian_headers):
    r = requests.get(api("/api/v1/dashboard"), params={"patient_id": "P001"},
                     headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 200
    body = r.json()
    for key in ("patient", "latest_risk_score", "recent_events",
                "recent_alerts", "recent_incidents"):
        assert key in body, f"dashboard 응답에 {key} 누락"
    assert body["patient"]["id"] == 1
    assert body["patient"]["name"] == "테스트환자일"


def test_incidents_endpoint_shape(guardian_headers):
    r = requests.get(api("/api/v1/incidents"), headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 200
    assert "items" in r.json()


def test_alerts_endpoint_shape(guardian_headers):
    r = requests.get(api("/api/v1/alerts"), headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 200


def test_risk_scores_endpoint_shape(guardian_headers):
    r = requests.get(api("/api/v1/risk-scores"), headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 200


# ---------------------------------------------------------------
# S3 미설정 상태의 clips 동작
#   CLAUDE.md는 "미설정 시 clips API 503"이라 적었으나 정확히는:
#   목록 조회는 200으로 정상, S3를 실제로 건드리는 endpoint만 503.
# ---------------------------------------------------------------
def test_clips_list_works_without_s3(guardian_headers):
    r = requests.get(api("/api/v1/clips"), params={"patient_id": "P001"},
                     headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 200
    assert "items" in r.json()


# ---------------------------------------------------------------
# 보관 기간 지난 클립 숨김 (2026-09-18 추가)
#   S3 lifecycle(30일)이 지운 객체의 row가 status='uploaded'로 남아 있어도
#   list_clips는 CLIP_RETENTION_DAYS 지난 클립을 노출하지 않는다.
#   시드 905 = 업로드 40일 전. 901/904 = 하루 전 (보여야 함).
# ---------------------------------------------------------------
def test_clips_list_hides_lifecycle_expired(guardian_headers):
    r = requests.get(api("/api/v1/clips"), params={"patient_id": "P001"},
                     headers=guardian_headers, timeout=TIMEOUT)
    assert r.status_code == 200
    ids = {c["clip_id"] for c in r.json()["items"]}
    assert "55555555-5555-4555-8555-555555555555" not in ids
    assert "11111111-1111-4111-8111-111111111111" in ids
    assert "44444444-4444-4444-8444-444444444444" in ids


def test_clips_upload_url_is_503_without_s3(ingest_headers):
    r = requests.post(api("/api/v1/clips/upload-url"), headers=ingest_headers, json={
        "device_key": "pi5-home001-cam1",
        "patient_id": "P001",
        "content_type": "video/mp4",
        "size_bytes": 1024,
    }, timeout=TIMEOUT)
    assert r.status_code == 503
    assert r.json() == {"error": "s3_not_configured"}
