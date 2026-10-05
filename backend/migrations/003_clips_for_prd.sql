-- 003_clips_for_prd.sql
--
-- PRD 6.1 (clips/upload-url, clips/confirm) 페이로드 수용을 위한 컬럼/FK/인덱스 추가.
-- 모든 신규 컬럼은 NULL 허용 (기존 row 호환 + 1차 AI 팀 코드 영향 없음).
-- CLAUDE.md 팀원 영향도 분류: "새 컬럼 추가 NULL 허용 = 자유 적용 OK"
--
-- 적용: ssh capstone "sudo -u postgres psql -v ON_ERROR_STOP=1 -d capstone_db" < migrations/003_clips_for_prd.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. 외부 식별자 UUID (응답 clip_id, GET /clips/{clip_id} 조회용)
ALTER TABLE public.clips
    ADD COLUMN IF NOT EXISTS clip_uuid uuid UNIQUE DEFAULT gen_random_uuid();

-- 2. PRD 요청 필드 수용 (upload-url 요청 body)
ALTER TABLE public.clips
    ADD COLUMN IF NOT EXISTS patient_id              bigint,
    ADD COLUMN IF NOT EXISTS device_id               bigint,
    ADD COLUMN IF NOT EXISTS related_alert_id        bigint,
    ADD COLUMN IF NOT EXISTS event_type              varchar(64),
    ADD COLUMN IF NOT EXISTS occurred_at             timestamp with time zone,
    ADD COLUMN IF NOT EXISTS requested_duration_sec  smallint;

-- 3. confirm 단계 필드 수용
ALTER TABLE public.clips
    ADD COLUMN IF NOT EXISTS file_size_bytes         bigint,
    ADD COLUMN IF NOT EXISTS actual_duration_sec     real,
    ADD COLUMN IF NOT EXISTS uploaded_at             timestamp with time zone;

-- 4. 라이프사이클 상태 (pending → uploaded / failed / expired)
ALTER TABLE public.clips
    ADD COLUMN IF NOT EXISTS status                  varchar(16) DEFAULT 'pending';

-- 5. FK 제약 (모두 SET NULL — 참조 대상 삭제 시 클립 메타는 보존)
ALTER TABLE public.clips
    ADD CONSTRAINT clips_patient_id_fkey
        FOREIGN KEY (patient_id) REFERENCES public.patients(id) ON DELETE SET NULL;
ALTER TABLE public.clips
    ADD CONSTRAINT clips_device_id_fkey
        FOREIGN KEY (device_id) REFERENCES public.devices(id) ON DELETE SET NULL;
ALTER TABLE public.clips
    ADD CONSTRAINT clips_related_alert_id_fkey
        FOREIGN KEY (related_alert_id) REFERENCES public.alerts(id) ON DELETE SET NULL;

-- 6. 인덱스 (조회 패턴 예측)
CREATE INDEX IF NOT EXISTS idx_clips_patient_occurred
    ON public.clips (patient_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_clips_related_alert
    ON public.clips (related_alert_id);
CREATE INDEX IF NOT EXISTS idx_clips_event_id
    ON public.clips (event_id);
CREATE INDEX IF NOT EXISTS idx_clips_status_created
    ON public.clips (status, created_at);

COMMIT;
