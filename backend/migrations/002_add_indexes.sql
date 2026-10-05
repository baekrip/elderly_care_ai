--
-- 002_add_indexes.sql
--
-- 자주 조회되는 컬럼에 인덱스 추가.
-- 001_init.sql 시점에는 PK/UNIQUE 외 인덱스가 누락되어 있었음.
--
-- 쿼리 패턴 근거 (app.py):
--   list_events/list_alerts/list_risk_scores 모두 ORDER BY occurred_at|created_at DESC + LIMIT
--   patient_id, device_id, event_type, source, risk_level WHERE 필터 빈번
--   dashboard, daily-summary는 patient 단위 + 최근 시간순
--

BEGIN;

-- events
CREATE INDEX IF NOT EXISTS idx_events_occurred_at
    ON public.events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_patient_occurred
    ON public.events (patient_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_device_occurred
    ON public.events (device_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_event_type
    ON public.events (event_type);

-- alerts
CREATE INDEX IF NOT EXISTS idx_alerts_occurred_at
    ON public.alerts (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_patient_occurred
    ON public.alerts (patient_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_device_occurred
    ON public.alerts (device_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_source
    ON public.alerts (source);

-- risk_scores
CREATE INDEX IF NOT EXISTS idx_risk_scores_patient_created
    ON public.risk_scores (patient_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_scores_risk_level
    ON public.risk_scores (risk_level);

COMMIT;
