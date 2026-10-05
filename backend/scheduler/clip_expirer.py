"""Clip Expirer — S3 lifecycle로 지워진 클립 row 정리.

S3 lifecycle(CLIP_RETENTION_DAYS)이 객체를 지워도 DB는 모른다. 그 row가 status='uploaded'로
남으면 list_clips가 죽은 presigned URL을 내보내 프론트 재생이 403으로 실패한다
(2026-09-18 실발현 — 8/9 업로드 클립 79건 전부).
매일 1회, 보관 기간을 넘긴 row를 status='expired'로 바꿔 목록(uploaded만 노출)에서 뺀다.
row 자체는 남긴다 (이력·감사용, 되돌리기 가능).

S3에 HEAD로 실존 여부를 묻지 않는 이유: IAM 사용자에 ListBucket이 없어 '객체 없음'도 403으로
와서 권한 오류와 구분이 안 된다. lifecycle은 만료일 다음 자정(UTC)에 도니 하루 여유
(CLIP_RETENTION_DAYS + 1)를 두고 시간만으로 판단한다 — 살아있는 클립을 expired로 잘못
바꾸는 쪽이 더 나쁜 버그이므로 보수적으로 잡는다. 그 사이 하루의 공백은 routes/clips.py
list_clips의 나이 필터가 메운다.
"""
from sqlalchemy import text

from core.config import CLIP_RETENTION_DAYS, LOCK_KEY_CLIP_EXPIRER
from core.db import engine


def expire_lifecycle_deleted_clips(logger) -> None:
    """매일 00:10 UTC 실행. 보관 기간을 넘긴 uploaded 클립을 expired로 전환.

    logger 주입 이유는 sweeper.detect_orphan_data와 같다 (요청 컨텍스트 밖 + 순환 import 회피).
    """
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
            logger.warning(
                "[ClipExpirer] %d clip(s) marked expired (retention %dd) ids=%s",
                len(rows), CLIP_RETENTION_DAYS, [int(r[0]) for r in rows[:20]],
            )
        else:
            logger.info("[ClipExpirer] nothing to expire.")
    except Exception as e:
        logger.error("[ClipExpirer] failed: %s", e)
