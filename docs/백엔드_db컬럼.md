핵심 컬럼 상세
users

컬럼   타입   비고
id   bigint PK   
email   varchar NOT NULL   
password_hash   varchar NOT NULL   bcrypt
role   varchar NOT NULL   admin / guardian (기본 guardian)
households

컬럼   타입   비고
id   bigint PK   
home_code   varchar NOT NULL   외부 식별자 (예: home001)
address   text   
account_id   bigint   → users.id (SET NULL)
patients

컬럼   타입   비고
id   varchar PK   환자 식별 코드 (예: P001, P002 등)
name, gender, birth_date      
status   varchar   기본 active
household_id   bigint   → households (SET NULL)
devices

컬럼   타입   비고
id   bigint PK   
device_key   varchar NOT NULL   예: pi5-home001-cam1, jetson-home001
device_type   varchar   camera / jetson 등
household_id   bigint   → households
parent_device_id   bigint   self-FK (카메라 ↔ 부모 디바이스 트리)
serial_number, mac_address, location, status      
cameras (device_type='camera'와 1:1)

컬럼   타입   비고
id   bigint PK   
device_id   bigint NOT NULL   → devices (CASCADE)
stream_path   varchar   예: /P001
resolution   varchar   예: 1920x1080
fps   smallint   
events (1차 AI ingest)

컬럼   타입   비고
id   bigint PK   
device_id   bigint NOT NULL   → devices (CASCADE)
household_id, camera_id   bigint   자동 비정규화
patient_id   varchar   외부 JSON의 patient_id 값(P001) 매핑
event_type   varchar NOT NULL   
confidence   double   
severity   int NOT NULL   기본 0
event_status   varchar   기본 detected
occurred_at   timestamptz NOT NULL   
clip_url   text   
payload_json   jsonb   
frame_id   bigint   
alerts

컬럼   타입   비고
id   bigint PK   
source   varchar NOT NULL   immediate / trend
event_id   bigint   → events (SET NULL)
device_id   bigint NOT NULL   → devices (CASCADE)
household_id, camera_id   bigint   비정규화
patient_id   varchar   
alert_type, alert_level   varchar NOT NULL   
message   text NOT NULL   
is_read   bool   기본 false
payload_json   jsonb   
clips (S3 메타)

컬럼   타입   비고
id   bigint PK   
clip_uuid   uuid   자동 생성
s3_key   text NOT NULL UNIQUE   (migration 004)
event_id   bigint   → events (CASCADE)
related_alert_id   bigint   → alerts (SET NULL)
device_id, household_id, camera_id   bigint   
patient_id   varchar   
event_type, started_at, ended_at, occurred_at      
requested_duration_sec, actual_duration_sec      
file_size_bytes, file_name      
uploaded_at   timestamptz   
status   varchar   기본 pending → uploaded
risk_scores (2차 AI)

컬럼   타입   비고
id   bigint PK   
patient_id   varchar NOT NULL   → patients.id (CASCADE)
score   int NOT NULL   
risk_level   varchar   
reason   text   
analyzed_from, analyzed_to   timestamptz   분석 구간
patient_guardians (M:N)

컬럼   타입   비고
user_id   bigint NOT NULL   → users (CASCADE)
patient_id   varchar NOT NULL   → patients.id (CASCADE)
FK Cascade 정책 요약
CASCADE 삭제 — 자식이 부모 없이 의미 없을 때: cameras.device_id, events.device_id, alerts.device_id, clips.event_id, risk_scores.patient_id, patient_guardians.*
SET NULL — 부모 삭제돼도 자식 기록은 보존: *.household_id, *.patient_id, *.camera_id, alerts.event_id, clips.related_alert_id, devices.parent_device_id, households.account_id
