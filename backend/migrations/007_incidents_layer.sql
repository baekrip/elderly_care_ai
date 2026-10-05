-- migration 007: incidents 계층 (스트림 집계 / Medallion Silver)
--
-- 목적:
--   1차 AI 추론 결과 raw events(Bronze)를 시간 윈도우 + 환자별 그룹핑으로
--   사건 단위 incidents(Silver)에 자동 집계. raw는 보존(시계열 분석),
--   incidents는 보호자 알림/대시보드 사건 카드용.
--
-- 집계 정책 (app.py aggregate_events_into_incidents()):
--   - 같은 patient + 같은 event_type
--   - 시간 갭 <= 10초 = 같은 incident
--   - 매 30초마다 스케줄러 실행, 15초 grace period (진행 중 burst 보호)
--
-- 외부 계약: 영향 없음 (1차 AI ingest endpoint 무변경, FK는 백엔드 내부).

BEGIN;

-- ============================================================
-- incidents 테이블 (사건 단위 메타)
-- ============================================================
CREATE TABLE incidents (
    id                  BIGSERIAL PRIMARY KEY,
    patient_id          BIGINT      NOT NULL REFERENCES patients(id)   ON DELETE CASCADE,
    household_id        BIGINT               REFERENCES households(id) ON DELETE SET NULL,
    incident_type       VARCHAR(64) NOT NULL,            -- 'fall_detected', 'abnormal_posture' 등 (event_type과 동일 값)
    started_at          TIMESTAMPTZ NOT NULL,            -- 첫 raw event occurred_at
    ended_at            TIMESTAMPTZ NOT NULL,            -- 마지막 raw event occurred_at
    raw_event_count     INTEGER     NOT NULL,            -- 묶인 raw event 수 (예: 낙상 22건)
    max_confidence      REAL,                            -- 묶인 raw 중 최대 confidence
    max_severity        INTEGER,                         -- 묶인 raw 중 최대 severity
    avg_confidence      REAL,                            -- 평균 confidence
    first_event_id      BIGINT               REFERENCES events(id) ON DELETE SET NULL,
    last_event_id       BIGINT               REFERENCES events(id) ON DELETE SET NULL,
    status              VARCHAR(16) NOT NULL DEFAULT 'closed',  -- 'closed' (집계 완료) / 'reviewed' / 'dismissed'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT incidents_status_chk CHECK (status IN ('closed','reviewed','dismissed')),
    CONSTRAINT incidents_time_chk   CHECK (ended_at >= started_at),
    CONSTRAINT incidents_count_chk  CHECK (raw_event_count >= 1)
);

-- 대시보드 환자별 최근 incidents 조회
CREATE INDEX idx_incidents_patient_started     ON incidents(patient_id,   started_at DESC);
-- admin: 가구별 최근 incidents
CREATE INDEX idx_incidents_household_started   ON incidents(household_id, started_at DESC) WHERE household_id IS NOT NULL;
-- 상태별 (운영자 검토 대기 등)
CREATE INDEX idx_incidents_status              ON incidents(status);
-- event_type별 통계
CREATE INDEX idx_incidents_type_started        ON incidents(incident_type, started_at DESC);

-- ============================================================
-- events.incident_id (raw → silver 역추적 FK)
-- ============================================================
-- NULL 허용 (집계 안 된 raw는 NULL, 집계 완료되면 incident id 박힘)
-- ON DELETE SET NULL: incident 삭제해도 raw events 보존
ALTER TABLE events
    ADD COLUMN incident_id BIGINT REFERENCES incidents(id) ON DELETE SET NULL;

-- 집계 대상 추출 쿼리 핵심 인덱스 (incident_id NULL인 raw events 빠르게 찾음)
CREATE INDEX idx_events_incident_pending
    ON events(patient_id, event_type, occurred_at)
    WHERE incident_id IS NULL;

-- incident → raw events 역조회
CREATE INDEX idx_events_incident_id
    ON events(incident_id)
    WHERE incident_id IS NOT NULL;

COMMIT;
