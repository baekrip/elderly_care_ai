"""Silent Orphan Sweeper (soft mode).

매일 1회 NULL FK row 감지 + 로그. 자동 삭제/거부는 안 한다 (1차 AI 통신 안전성 우선).
STRICT 모드(미등록 device_key 거부)는 향후 환경변수 토글 예정.

배경: get_or_create_device 안전망이 미등록 device_key를 메타 NULL로 자동 생성하는데,
그렇게 만들어진 row는 household_id/camera_id 비정규화가 깨진 상태로 누적된다.
"""
from sqlalchemy import text

from core.config import LOCK_KEY_SWEEPER
from core.db import engine


def detect_orphan_data(logger) -> None:
    """매일 09:00 KST(00:00 UTC) 실행. devices/events/patients의 NULL FK 감지.

    logger를 인자로 받는 이유: 이 함수는 요청 컨텍스트 밖(백그라운드 스레드)에서
    돌기 때문에 current_app을 쓸 수 없다. app 객체를 import하면 순환 import가
    되므로, 등록 시점에 app.logger를 주입받는다.
    """
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

                    logger.warning(
                        "[Sweeper] Orphan detected — "
                        "devices=%d, events(household NULL)=%d, "
                        "events(camera NULL)=%d, patients=%d, sample_device_keys=%s",
                        stats["orphan_devices"], stats["orphan_events"],
                        stats["events_no_camera"], stats["orphan_patients"],
                        sample_keys,
                    )
                else:
                    logger.info("[Sweeper] No orphans detected.")
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": LOCK_KEY_SWEEPER}
                )
    except Exception as e:
        logger.error("[Sweeper] failed: %s", e)
