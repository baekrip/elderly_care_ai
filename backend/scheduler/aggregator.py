"""Incidents 스트림 집계 (Medallion Silver, migration 007).

raw events(Bronze) → incidents(Silver) 자동 집계.
엣지가 초당 ~0.8건씩 밀어넣는 raw 추론을 "사건" 단위로 묶는다.
검증된 예: 낙상 1회 = raw 22건 → incident 1건.

집계 정책 상수는 config.py 참고 (INCIDENT_GAP_THRESHOLD_SEC 등).
"""
from typing import Any, Dict

from sqlalchemy import text

from core.config import (
    INCIDENT_GAP_THRESHOLD_SEC,
    INCIDENT_GRACE_PERIOD_SEC,
    INCIDENT_MAX_DURATION_SEC,
    LOCK_KEY_AGGREGATOR,
)
from core.db import engine


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


def aggregate_events_into_incidents(logger) -> None:
    """매 30초 실행. incident_id NULL인 raw events를 시간 윈도우로 그룹핑 후 incidents INSERT.

    워커 간 중복: pg_try_advisory_lock(LOCK_KEY_AGGREGATOR)으로 한 워커만 실행.
    그룹 간 격리: 그룹별 독립 nested transaction (savepoint) — 한 그룹 실패가
                 다른 그룹 롤백시키지 않음.

    logger는 등록 시점에 주입받는다 (요청 컨텍스트 밖이라 current_app 불가).
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
                        logger.error(
                            "[Aggregator] group failed (patient=%s, type=%s, raw=%d): %s",
                            g["patient_id"], g["event_type"], len(g["event_ids"]), e,
                        )
                        # continue — 다음 그룹 계속

                if new_incidents > 0 or failed_groups > 0:
                    logger.info(
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
        logger.error("[Aggregator] failed: %s", e)
