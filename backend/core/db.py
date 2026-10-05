"""DB 엔진과 FK 해결 헬퍼.

SQLAlchemy Core만 쓴다 (ORM 없음) — 모든 쿼리는 conn.execute(text("..."), params).
커넥션은 `with engine.begin() as conn:` 으로 짧게 쓰고 닫는다.
"""
from typing import Dict, Optional

from sqlalchemy import create_engine, text

from .config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)


def get_or_create_device(conn, device_key: str) -> int:
    """device_key로 디바이스 조회 또는 생성. ON CONFLICT로 TOCTOU race 차단 (audit Logic H-2).

    DO UPDATE SET (no-op)은 RETURNING id를 보장하기 위한 idiomatic 패턴.
    DO NOTHING은 conflict 시 RETURNING이 빈 결과를 줘서 follow-up SELECT 필요.

    주의: 미등록 device_key도 메타(device_type/household_id) NULL로 자동 생성된다.
    ingest 끊김 방지용 안전망이지만, 그렇게 만들어진 row는 비정규화가 깨진다
    (Silent Orphan H-3). admin 사전 등록이 권장 경로.
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


def resolve_device_context(conn, device_fk: int) -> Dict[str, Optional[int]]:
    """device.id로부터 ingest 시점 비정규화에 쓸 (household_id, camera_id) 조회.

    events/alerts/clips 는 조회 성능을 위해 household_id/camera_id 를 비정규화해
    들고 있다. 수신 endpoint들이 저장 직전에 이 함수로 그 값을 채운다.
    디바이스가 admin 사전 등록 없이 자동 생성됐다면 둘 다 None이 나온다
    (Silent Orphan H-3).

    원래 app.py 안에서 _resolve_device_context 였으나, ingest 와 clips 양쪽에서
    쓰이므로 공용 DB 헬퍼로 옮겼다. 모듈 경계를 넘어 공유되므로 앞의 밑줄을 뗐다.
    """
    row = conn.execute(text("""
        SELECT d.household_id,
               (SELECT c.id FROM cameras c WHERE c.device_id = d.id) AS camera_id
          FROM devices d
         WHERE d.id = :device_id
    """), {"device_id": device_fk}).first()
    if row is None:
        return {"household_id": None, "camera_id": None}
    return {"household_id": row[0], "camera_id": row[1]}
