-- 005_patient_devices.sql
--
-- 환자 ↔ 디바이스 매핑 + devices 테이블 메타 컬럼 확장.
--
-- 배경: 기존엔 이벤트가 들어올 때마다 1차 AI가 patient_id + device_key를 둘 다 보내고
--       백엔드는 그냥 기록만 함. "이 디바이스가 정말 이 환자 거인지"에 대한
--       진실의 원천이 백엔드에 없었음. 이번 마이그레이션으로 관리자가 직접 매핑하고
--       향후 ingest 검증에도 활용 가능하도록 한다.
--
-- 명명 규칙 (PRD 박제):
--   pi5-home001-cam0   = home001 가구의 Pi 5 첫 번째 카메라
--   pi5-home001-cam1   = home001 가구의 Pi 5 두 번째 카메라
--   jetson-home001     = home001 가구의 Jetson (등록은 선택. 백엔드 입장에선 인증된 송신자일 뿐)
--
-- 적용: ssh capstone "sudo -u postgres psql -v ON_ERROR_STOP=1 -d capstone_db" < migrations/005_patient_devices.sql
--
-- CLAUDE.md 팀원 영향도 분류: "새 컬럼 추가 NULL 허용 + 신규 테이블 = 자유 적용 OK"

BEGIN;

-- 1. devices 메타 컬럼 확장
ALTER TABLE public.devices
    ADD COLUMN IF NOT EXISTS device_type VARCHAR(32),  -- 'pi5_camera' | 'jetson' | 'other'
    ADD COLUMN IF NOT EXISTS home_group  VARCHAR(64);  -- 가구별 그룹화 키 ('home001' 등)

-- 2. patient_devices junction table (M:N)
CREATE TABLE IF NOT EXISTS public.patient_devices (
    patient_id   BIGINT NOT NULL REFERENCES public.patients(id) ON DELETE CASCADE,
    device_id    BIGINT NOT NULL REFERENCES public.devices(id)  ON DELETE CASCADE,
    assigned_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (patient_id, device_id)
);

-- 3. 역방향 조회 인덱스 (device → patients)
CREATE INDEX IF NOT EXISTS idx_patient_devices_device
    ON public.patient_devices(device_id);

-- 4. home_group 그룹 조회 인덱스
CREATE INDEX IF NOT EXISTS idx_devices_home_group
    ON public.devices(home_group)
    WHERE home_group IS NOT NULL;

COMMIT;
