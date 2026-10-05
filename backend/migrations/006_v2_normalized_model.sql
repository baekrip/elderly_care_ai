-- 006_v2_normalized_model.sql
--
-- v2 정규화 모델 도입. v1의 flat 모델(devices.home_group 문자열, patient_devices M:N)을
-- 가구(households) first-class 모델로 전환.
--
-- 변경 요약:
--   1. households 신규 (가구 = 물리적 집, account_id로 admin 연결)
--   2. devices에 household_id / parent_device_id / serial_number / mac_address 추가
--   3. cameras 신규 (device_type='camera'와 1:1, stream_path/resolution 메타)
--   4. patients에 household_id FK
--   5. events / alerts / clips에 household_id (비정규화) + camera_id 추가
--   6. events에 frame_id (영상-결과 동기화용)
--   7. patient_devices 테이블 drop (patient.household_id로 간접 연결)
--   8. devices.home_group 컬럼 drop (households.home_code로 승격)
--
-- 데이터 처리: 사용자 결정에 따라 기존 테스트 데이터 전부 삭제 후 새 스키마 적용
-- (운영 DB이지만 실 트래픽 없는 검증 단계, 1차/2차 AI 통합 전이라 안전)
--
-- 외부 API 계약 보존:
--   - device_key (string) — 1차 AI는 그대로 사용
--   - patient_code (string, JSON에서 patient_id) — 그대로
--   - 모든 ingest/query 엔드포인트 응답 shape 변경 없음 (필드 추가만)
--
-- 적용:
--   ssh capstone "sudo -u postgres psql -v ON_ERROR_STOP=1 -d capstone_db" < migrations/006_v2_normalized_model.sql

BEGIN;

-- ============================================================
-- Phase 1: 기존 데이터 삭제 (테스트 데이터 only, 의도된 와이프)
-- ============================================================
-- FK 의존 순서대로 (자식 먼저)
DELETE FROM clips;
DELETE FROM alerts;
DELETE FROM events;
DELETE FROM risk_scores;
DELETE FROM patient_guardians;
DELETE FROM patient_devices;
DELETE FROM patients;
DELETE FROM devices;
-- users(admin/guardian 계정)는 보존 (인증/시연용)

-- ============================================================
-- Phase 2: 옛 구조 제거
-- ============================================================
DROP TABLE IF EXISTS patient_devices;

-- devices.home_group 컬럼 + 관련 인덱스 제거 (households로 승격)
DROP INDEX IF EXISTS idx_devices_home_group;
ALTER TABLE devices DROP COLUMN IF EXISTS home_group;

-- ============================================================
-- Phase 3: households 신규
-- ============================================================
CREATE TABLE households (
    id          BIGSERIAL PRIMARY KEY,
    home_code   VARCHAR(64) NOT NULL UNIQUE,                    -- 'home001' 형식, IDENTIFIER_PATTERN
    address     TEXT,                                            -- 가구 주소 (운영 시 채움)
    account_id  BIGINT REFERENCES users(id) ON DELETE SET NULL,  -- 관리 admin 계정 (optional)
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE INDEX idx_households_account ON households(account_id) WHERE account_id IS NOT NULL;

-- ============================================================
-- Phase 4: devices 확장
-- IF NOT EXISTS로 재실행 안전성 보장 (003/005 컨벤션 일치, audit H-6)
-- ============================================================
ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS household_id     BIGINT REFERENCES households(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS parent_device_id BIGINT REFERENCES devices(id)    ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS serial_number    VARCHAR(128),
    ADD COLUMN IF NOT EXISTS mac_address      VARCHAR(32);

-- 자기참조 self-loop 차단 CHECK 제약 (audit H-5)
-- parent_device_id가 자기 id를 가리키는 row를 DB 차원에서 방지
-- 더 긴 사이클(A→B→A)은 애플리케이션 측에서 트리에 한 번만 INSERT/UPDATE 시 점검
ALTER TABLE devices
    ADD CONSTRAINT devices_no_self_parent
    CHECK (parent_device_id IS NULL OR parent_device_id <> id);

CREATE INDEX IF NOT EXISTS idx_devices_household ON devices(household_id) WHERE household_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_devices_parent    ON devices(parent_device_id) WHERE parent_device_id IS NOT NULL;

-- ============================================================
-- Phase 5: cameras 신규 (device_type='camera'와 1:1)
-- ============================================================
CREATE TABLE cameras (
    id           BIGSERIAL PRIMARY KEY,
    device_id    BIGINT NOT NULL UNIQUE REFERENCES devices(id) ON DELETE CASCADE,
    stream_path  VARCHAR(256),                                              -- mediamtx 경로 (예: '/patient_test')
    resolution   VARCHAR(32),                                                -- '1920x1080' 등
    fps          SMALLINT,                                                   -- 15, 30 등
    created_at   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- device_id가 이미 UNIQUE라 별도 인덱스 불필요 (UNIQUE 제약이 자동 인덱스)

-- ============================================================
-- Phase 6: patients 확장 (IF NOT EXISTS, audit H-6)
-- ============================================================
ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS household_id BIGINT REFERENCES households(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_patients_household ON patients(household_id) WHERE household_id IS NOT NULL;

-- ============================================================
-- Phase 7: events / alerts / clips 확장 (IF NOT EXISTS, audit H-6)
--   household_id: JOIN 줄이는 비정규화
--   camera_id:    영상 관련일 때 (NULL 허용)
--   frame_id:     events만, 영상-결과 동기화용
-- ============================================================
ALTER TABLE events
    ADD COLUMN IF NOT EXISTS household_id BIGINT REFERENCES households(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS camera_id    BIGINT REFERENCES cameras(id)    ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS frame_id     BIGINT;

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS household_id BIGINT REFERENCES households(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS camera_id    BIGINT REFERENCES cameras(id)    ON DELETE SET NULL;

ALTER TABLE clips
    ADD COLUMN IF NOT EXISTS household_id BIGINT REFERENCES households(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS camera_id    BIGINT REFERENCES cameras(id)    ON DELETE SET NULL;

-- 비정규화 컬럼 인덱스 (가구별/카메라별 쿼리 패턴)
CREATE INDEX IF NOT EXISTS idx_events_household_occurred ON events(household_id, occurred_at DESC)
    WHERE household_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_camera             ON events(camera_id)
    WHERE camera_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_alerts_household_occurred ON alerts(household_id, occurred_at DESC)
    WHERE household_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_alerts_camera             ON alerts(camera_id)
    WHERE camera_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_clips_household_created   ON clips(household_id, created_at DESC)
    WHERE household_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_clips_camera              ON clips(camera_id)
    WHERE camera_id IS NOT NULL;

-- ============================================================
-- Phase 8: device_type 값 컨벤션 정정
--   v1 (005): 'pi5_camera' | 'jetson' | 'other'
--   v2:        'camera' | 'raspberry_pi' | 'jetson' | 'other'
--   (cameras 테이블이 있으니 device_type='camera'가 정식 명칭)
--
-- 데이터 와이프 후라 row가 없어 ALTER 효과 없지만 명시적 의도 기록
-- (애플리케이션 코드 ALLOWED_DEVICE_TYPES도 동기화 예정)
-- ============================================================
-- (별도 SQL 없음, app.py에서 ALLOWED_DEVICE_TYPES 갱신)

COMMIT;

-- ============================================================
-- 적용 후 확인 SQL (수동 실행):
--
--   \d households
--   \d devices
--   \d cameras
--   \d patients
--   \d events
--   \d alerts
--   \d clips
--   SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;
--   SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename IN
--     ('households','devices','cameras','patients','events','alerts','clips')
--     ORDER BY tablename, indexname;
-- ============================================================
