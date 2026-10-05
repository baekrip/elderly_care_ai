"""동등성 대조 테스트 — 모듈화 전 원본 vs 모듈화 후 신규.

특성화 테스트(test_characterization.py)는 "내가 옳다고 생각한 값"을 단언한다.
그래서 내가 옳다고 착각한 부분은 못 잡는다. 여기서는 **커밋된 원본 app.py를
같은 DB에 물려 따로 띄우고** 같은 요청을 양쪽에 던져 응답 전체를 비교한다.
원본이 정답지이므로 기대값을 짐작할 필요가 없다.

이 방식이 잡아내는 것:
  - 응답 빌더의 필드명 오타 / 누락 / 순서 무관 값 변경
  - 역할 필터링이 미묘하게 달라진 경우
  - 에러 응답 형식/코드 변화

전제: seeds/local_seed.sql 이 alerts/risk_scores/clips/events 를 채워 두어
      응답 빌더가 실제로 실행된다. 빈 목록끼리 비교하면 아무것도 증명 못 한다.

실행:
    docker compose up -d api api-legacy
    docker compose exec api pytest tests/test_equivalence.py -v
"""
import os

import pytest
import requests

from conftest import ADMIN, APP_TOKEN, GUARDIAN

TIMEOUT = 10

NEW_URL = os.getenv("API_BASE_URL", "http://localhost:5000").rstrip("/")
# 대조군. 컨테이너 네트워크 안에서는 서비스명으로 접근한다.
OLD_URL = os.getenv("LEGACY_BASE_URL", "http://api-legacy:5000").rstrip("/")


def _login(base: str, creds: dict) -> str:
    r = requests.post(f"{base}/api/v1/auth/login", json=creds, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def legacy_up():
    """대조군이 안 떠 있으면 이 파일 전체를 건너뛴다."""
    try:
        r = requests.get(f"{OLD_URL}/health", timeout=5)
        assert r.status_code == 200
    except Exception as e:
        pytest.skip(f"대조군(api-legacy)이 응답하지 않음 — docker compose up -d api-legacy ({e})")


@pytest.fixture(scope="module")
def tokens(legacy_up):
    """JWT는 양쪽에서 각각 발급받는다.

    토큰 문자열 자체는 iat/jti가 달라 절대 같을 수 없으므로 비교 대상이 아니다.
    비교하는 건 그 토큰으로 얻은 응답이다.
    """
    return {
        "guardian": {
            NEW_URL: _login(NEW_URL, GUARDIAN),
            OLD_URL: _login(OLD_URL, GUARDIAN),
        },
        "admin": {
            NEW_URL: _login(NEW_URL, ADMIN),
            OLD_URL: _login(OLD_URL, ADMIN),
        },
    }


def _hdr(tokens, role: str, base: str) -> dict:
    return {"Authorization": f"Bearer {tokens[role][base]}"}


def _compare(path: str, tokens, role: str = "guardian", params: dict | None = None):
    """같은 GET을 양쪽에 던져 상태코드와 JSON 본문을 비교."""
    new = requests.get(f"{NEW_URL}{path}", params=params,
                       headers=_hdr(tokens, role, NEW_URL), timeout=TIMEOUT)
    old = requests.get(f"{OLD_URL}{path}", params=params,
                       headers=_hdr(tokens, role, OLD_URL), timeout=TIMEOUT)

    assert new.status_code == old.status_code, (
        f"{path} 상태코드 불일치: 신규={new.status_code} 원본={old.status_code}"
    )
    assert new.json() == old.json(), f"{path} 응답 본문 불일치"
    return new.json()


# ---------------------------------------------------------------
# 조회 endpoint — 응답 빌더가 실제로 도는 경로들
# ---------------------------------------------------------------
@pytest.mark.parametrize("path,params", [
    ("/api/v1/events", {"patient_id": "P001"}),
    ("/api/v1/events", None),
    ("/api/v1/incidents", {"patient_id": "P001"}),
    ("/api/v1/incidents", None),
    ("/api/v1/alerts", {"patient_id": "P001"}),
    ("/api/v1/alerts", None),
    ("/api/v1/risk-scores", {"patient_id": "P001"}),
    ("/api/v1/risk-scores", None),
    ("/api/v1/clips", {"patient_id": "P001"}),
    ("/api/v1/clips", None),
    ("/api/v1/patients", None),
    ("/api/v1/dashboard", {"patient_id": "P001"}),
])
def test_guardian_responses_identical(tokens, path, params):
    _compare(path, tokens, "guardian", params)


@pytest.mark.parametrize("path,params", [
    ("/api/v1/events", {"patient_id": "P002"}),
    ("/api/v1/alerts", None),
    ("/api/v1/risk-scores", None),
    ("/api/v1/clips", None),
    ("/api/v1/patients", None),
    ("/api/v1/dashboard", {"patient_id": "P002"}),
    ("/api/v1/admin/users", None),
    ("/api/v1/admin/households", None),
    ("/api/v1/admin/devices", None),
])
def test_admin_responses_identical(tokens, path, params):
    _compare(path, tokens, "admin", params)


# ---------------------------------------------------------------
# 응답 빌더가 정말 실행됐는지 확인 — 빈 목록끼리 비교하면 의미가 없다.
# 이 테스트가 실패하면 시드가 부실해진 것이고, 위 대조들도 무의미해진다.
# ---------------------------------------------------------------
def test_seed_actually_exercises_serializers(tokens):
    for path in ("/api/v1/events", "/api/v1/alerts",
                 "/api/v1/risk-scores", "/api/v1/clips", "/api/v1/incidents"):
        body = _compare(path, tokens, "guardian", {"patient_id": "P001"})
        assert body["items"], f"{path} 가 빈 목록 — 시드 확인 필요 (대조가 무의미해짐)"


def test_serializer_edge_cases_present(tokens):
    """CLAUDE.md에 기록된 실데이터 특이 케이스가 응답에 실제로 나타나는지."""
    events = _compare("/api/v1/events", tokens, "guardian", {"patient_id": "P001"})["items"]

    scores = [e["risk_score"] for e in events if e["risk_score"] is not None]
    # 신규 형식(1~5 int)과 옛 형식(0~1 float)이 섞여 있어야 한다
    assert any(isinstance(s, int) for s in scores), "신규 형식 risk_score 없음"
    assert any(isinstance(s, float) for s in scores), "옛 형식 float risk_score 없음"
    # payload에 키가 없어서 None으로 나가는 경로
    assert any(e["activity_label"] is None for e in events), "activity_label None 케이스 없음"

    clips = _compare("/api/v1/clips", tokens, "guardian", {"patient_id": "P001"})["items"]
    # S3 미설정이므로 재생 URL은 전부 None (clip_presigned_get_url의 None 경로)
    assert all(c["video_url"] is None for c in clips)
    # uploaded 이면서 선택 필드가 NULL인 클립 — clip_row_to_dict의 None 가드 경로
    assert any(c["file_size_bytes"] is None for c in clips), "NULL 선택 필드 케이스 없음"
    assert any(c["related_alert_id"] is None for c in clips)


def test_pending_clips_excluded_from_list(tokens):
    """list_clips는 status='uploaded'만 노출한다 (pending은 재생 불가라 제외).

    시드에 pending 클립(id 902)이 있는데도 목록에 안 나오는 게 정상이다.
    원본도 같은지 대조로 확인한다.
    """
    clips = _compare("/api/v1/clips", tokens, "guardian", {"patient_id": "P001"})["items"]
    assert clips, "클립 목록이 비어 대조가 무의미"
    assert all(c["status"] == "uploaded" for c in clips)


def test_lifecycle_expired_clips_excluded_from_list(tokens):
    """보관 기간(CLIP_RETENTION_DAYS=30)이 지난 클립은 status='uploaded'라도 목록에서 뺀다.

    S3 lifecycle이 객체를 지운 뒤에도 DB row는 남기 때문에, 그대로 노출하면 프론트가
    존재하지 않는 객체의 presigned URL을 받아 재생이 403으로 실패한다 (2026-09-18 운영 실발현).
    시드 905(업로드 40일 전)가 이 케이스. 원본(dc973f1)도 같은 필터를 갖는다.
    """
    clips = _compare("/api/v1/clips", tokens, "guardian", {"patient_id": "P001"})["items"]
    assert clips, "클립 목록이 비어 대조가 무의미"
    ids = {c["clip_id"] for c in clips}
    assert "55555555-5555-4555-8555-555555555555" not in ids, "lifecycle 만료 클립이 노출됨"
    assert "11111111-1111-4111-8111-111111111111" in ids, "최근 클립은 보여야 함"


# ---------------------------------------------------------------
# 에러 응답도 동일해야 한다 — 외부 협력자가 코드/형식에 의존한다
# ---------------------------------------------------------------
def test_access_denied_identical(tokens):
    _compare("/api/v1/events", tokens, "guardian", {"patient_id": "P002"})


def test_missing_jwt_identical():
    new = requests.get(f"{NEW_URL}/api/v1/events", timeout=TIMEOUT)
    old = requests.get(f"{OLD_URL}/api/v1/events", timeout=TIMEOUT)
    assert new.status_code == old.status_code == 401
    assert new.json() == old.json()


def test_login_failure_identical():
    body = {"email": "guardian@local.test", "password": "wrong"}
    new = requests.post(f"{NEW_URL}/api/v1/auth/login", json=body, timeout=TIMEOUT)
    old = requests.post(f"{OLD_URL}/api/v1/auth/login", json=body, timeout=TIMEOUT)
    assert new.status_code == old.status_code == 401
    assert new.json() == old.json()


def test_wrong_app_token_identical(legacy_up):
    hdr = {"Authorization": "Bearer deadbeefdeadbeefdeadbeefdeadbeef"}
    new = requests.post(f"{NEW_URL}/api/v1/events/batch", headers=hdr,
                        json={"events": []}, timeout=TIMEOUT)
    old = requests.post(f"{OLD_URL}/api/v1/events/batch", headers=hdr,
                        json={"events": []}, timeout=TIMEOUT)
    assert new.status_code == old.status_code == 403
    # abort(403)의 HTML 에러 페이지까지 동일해야 한다
    assert new.text == old.text


def test_daily_summary_identical(legacy_up):
    hdr = {"Authorization": f"Bearer {APP_TOKEN}"}
    new = requests.get(f"{NEW_URL}/api/v1/daily-summary", headers=hdr, timeout=TIMEOUT)
    old = requests.get(f"{OLD_URL}/api/v1/daily-summary", headers=hdr, timeout=TIMEOUT)
    assert new.status_code == old.status_code
    assert new.json() == old.json()
