"""테스트 공통 설정.

실행 방법 (컨테이너 안에서):
    docker compose exec api pytest -q

호스트에서 직접 돌리려면 pytest 설치 후:
    API_BASE_URL=http://localhost:5001 pytest -q

이 테스트들은 HTTP 경계만 두드리는 블랙박스 테스트다. app.py 를 import 하지
않기 때문에, 모듈화로 내부 구조가 어떻게 바뀌든 테스트는 그대로 통과해야 한다.
바로 그 성질이 "쪼개기 전후가 동일한가"를 증명해 준다.
"""
import os

import pytest
import requests

# 컨테이너 안에서 자기 자신을 호출 (gunicorn이 5000에 바인딩).
# 호스트에서 돌릴 때는 5001 — macOS AirPlay가 5000을 점유하기 때문.
BASE_URL = os.getenv("API_BASE_URL", "http://localhost:5000").rstrip("/")

# docker-compose.yml 에 박힌 로컬 전용 더미 값과 일치해야 한다.
APP_TOKEN = os.getenv("APP_TOKEN", "0123456789abcdef0123456789abcdef")

# seeds/local_seed.sql 의 계정
GUARDIAN = {"email": "guardian@local.test", "password": "localguardian"}
ADMIN = {"email": "admin@local.test", "password": "localadmin"}


def api(path: str) -> str:
    return f"{BASE_URL}{path}"


def _login(creds: dict) -> str:
    r = requests.post(api("/api/v1/auth/login"), json=creds, timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def guardian_token() -> str:
    return _login(GUARDIAN)


@pytest.fixture(scope="session")
def admin_token() -> str:
    return _login(ADMIN)


@pytest.fixture(scope="session")
def guardian_headers(guardian_token: str) -> dict:
    return {"Authorization": f"Bearer {guardian_token}"}


@pytest.fixture(scope="session")
def admin_headers(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def ingest_headers() -> dict:
    """AI팀/엣지가 쓰는 정적 APP_TOKEN 인증 (JWT와 다른 체계)."""
    return {"Authorization": f"Bearer {APP_TOKEN}"}
