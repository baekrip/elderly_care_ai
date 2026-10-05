"""인증·권한 헬퍼.

서로 다른 두 인증 체계가 있다. 절대 섞지 말 것:

1. require_token() — 정적 `Authorization: Bearer $APP_TOKEN`.
   AI/엣지 수신 endpoint(events/batch, alerts/*, risk-scores, daily-summary)와
   등록 endpoint가 쓴다.

2. @jwt_required() — /auth/login이 발급하는 사용자별 JWT (role 클레임: admin|guardian).
   프론트 조회 endpoint(events, incidents, alerts, risk-scores, dashboard)가 쓴다.

역할 기반 행 필터링: JWT 보호 목록 endpoint는 role == "guardian"일 때
patient_guardians에 매핑된 환자로만 범위를 좁혀야 한다. admin은 전체를 본다.
**새 조회 endpoint를 만들 때 이 필터링을 빠뜨리면 보호자 간 데이터가 샌다.**
"""
from flask import abort, request
from flask_jwt_extended import get_jwt
from sqlalchemy import text

from .config import APP_TOKEN


def require_token() -> None:
    """AI팀/엣지용 정적 Bearer 토큰 검증.

    참고: abort(401/403)은 Flask 기본 HTML 에러 페이지를 반환한다.
    다른 endpoint의 {"error": "snake_case"} JSON 규약과 어긋나지만,
    외부 협력자(1차 AI)가 이미 이 동작에 맞춰져 있어 현재 형태를 유지한다.
    변경하려면 PRD 계약 갱신 + 1차 AI 공지가 선행돼야 한다.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        abort(401, description="Missing Bearer token")

    token = auth.split(" ", 1)[1].strip()
    if token != APP_TOKEN:
        abort(403, description="Invalid token")


def get_current_role() -> str:
    """JWT의 role 클레임. 없으면 최소 권한인 guardian으로 간주."""
    return get_jwt().get("role", "guardian")


def check_patient_access(conn, user_id: int, patient_code: str) -> bool:
    """이 보호자가 해당 환자에 접근 가능한가 (단건 확인)."""
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
    """이 보호자에게 매핑된 환자 코드 목록 (목록 조회 범위 제한용)."""
    rows = conn.execute(
        text("""
            SELECT p.patient_code FROM patient_guardians pg
            JOIN patients p ON pg.patient_id = p.id
            WHERE pg.user_id = :user_id
        """),
        {"user_id": user_id}
    ).fetchall()
    return [r[0] for r in rows]
