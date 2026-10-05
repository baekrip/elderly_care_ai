-- 로컬 개발/테스트 전용 시드. 운영 DB에 절대 적용하지 말 것.
--
-- migrations/ 와 분리해 둔 이유: migrations/ 는 서버에 배포되어 운영 스키마에
-- 적용되는 파일들이다. 시드가 거기 섞이면 운영 DB에 더미 데이터가 들어갈 수 있다.
-- 이 파일은 docker-compose가 로컬 db 컨테이너에만 마운트한다.
--
-- 값은 전부 가짜다. 운영 .env / docs/명령어.txt 의 실제 시드와 무관하다.
--
-- 계정 (로컬 전용):
--   admin@local.test    / localadmin      (role=admin)
--   guardian@local.test / localguardian   (role=guardian, P001만 매핑)

BEGIN;

-- ---------------------------------------------------------------
-- users — bcrypt 해시는 위 비밀번호로 생성한 고정값
-- ---------------------------------------------------------------
INSERT INTO users (id, email, password_hash, role) VALUES
  (1, 'admin@local.test',    '$2b$12$kJM7uqKnmfWTx34PkQxPuOY/FGyf9/ufczR3EvPX8ZCWjrk.JTYsu', 'admin'),
  (2, 'guardian@local.test', '$2b$12$c9kOeOToqoGvhC8LJ22OuuBCsbntFXu.lDDdC1zrMqecKqbQ2WZp.', 'guardian');

-- ---------------------------------------------------------------
-- households — 가구 2개.
-- home002는 guardian에게 매핑하지 않는다: 역할 필터링 검증용.
-- ---------------------------------------------------------------
INSERT INTO households (id, home_code, address) VALUES
  (1, 'home001', '로컬 테스트 주소 1'),
  (2, 'home002', '로컬 테스트 주소 2');

-- ---------------------------------------------------------------
-- patients
--   P001 — guardian@local.test 에게 매핑됨 (보여야 함)
--   P002 — 아무 guardian 에게도 매핑 안 됨 (guardian 에게 안 보여야 함)
-- ---------------------------------------------------------------
INSERT INTO patients (id, patient_code, name, gender, birth_date, status, household_id) VALUES
  (1, 'P001', '테스트환자일', 'F', '1940-03-11', 'active', 1),
  (2, 'P002', '테스트환자이', 'M', '1938-09-02', 'active', 2);

-- guardian(user 2) ↔ P001 만 연결. P002는 의도적으로 미연결.
INSERT INTO patient_guardians (user_id, patient_id) VALUES
  (2, 1);

-- ---------------------------------------------------------------
-- devices — CLAUDE.md 의 명명 규칙 준수: pi5-<home>-cam<N>, jetson-<home>
-- 메타(device_type/household_id)를 채워 넣는다. 비워두면 ingest 시
-- get_or_create_device 안전망이 NULL 메타로 자동 생성 → Silent Orphan H-3 재현됨.
-- ---------------------------------------------------------------
INSERT INTO devices (id, device_key, device_name, location, status, device_type, household_id, parent_device_id) VALUES
  (1, 'jetson-home001',   '거실 젯슨',   '거실', 'active', 'jetson', 1, NULL),
  (2, 'pi5-home001-cam1', '거실 카메라', '거실', 'active', 'camera', 1, 1),
  (3, 'jetson-home002',   '안방 젯슨',   '안방', 'active', 'jetson', 2, NULL),
  (4, 'pi5-home002-cam1', '안방 카메라', '안방', 'active', 'camera', 2, 3);

-- cameras — device_type='camera' 인 디바이스와 1:1 (migration 006)
INSERT INTO cameras (id, device_id, stream_path, resolution, fps) VALUES
  (1, 2, '/patient_test',  '1920x1080', 15),
  (2, 4, '/patient_test2', '1920x1080', 15);

-- ---------------------------------------------------------------
-- 명시적 id로 넣었으므로 시퀀스를 뒤로 밀어준다.
-- 안 하면 이후 애플리케이션 INSERT가 id=1 부터 시도해 PK 충돌난다.
-- ---------------------------------------------------------------
SELECT setval('users_id_seq',      (SELECT MAX(id) FROM users));
SELECT setval('households_id_seq', (SELECT MAX(id) FROM households));
SELECT setval('patients_id_seq',   (SELECT MAX(id) FROM patients));
SELECT setval('devices_id_seq',    (SELECT MAX(id) FROM devices));
SELECT setval('cameras_id_seq',    (SELECT MAX(id) FROM cameras));

COMMIT;

-- ===============================================================
-- 응답 빌더(serializers)를 실제로 실행시키기 위한 데이터.
--
-- 이게 없으면 alerts/risk_scores/clips 테이블이 비어 있어서
-- alert_row_to_dict / risk_score_row_to_dict / clip_row_to_dict /
-- clip_presigned_get_url 이 테스트 중 한 번도 안 불린다.
-- 필드명을 잘못 옮겨도 전부 초록불이 뜨는 상태가 된다.
--
-- 값은 지어낸 게 아니라 CLAUDE.md에 기록된 실제 데이터의 특이 케이스를 재현한다:
--   - risk_score 형식 혼재 (신규 1~5 int / 옛 데이터 0~1 float)
--   - household_id/camera_id NULL 고아 행 (Silent Orphan H-3)
--   - payload에 기대 키가 없는 경우 (.get -> None 경로)
--   - alerts source 구분 (EDGE = 1차 AI 즉시 / ANALYZER = 2차 AI 추세)
--
-- incident_id를 미리 박아두는 이유: 집계 스케줄러가 이 행들을 건드리면
-- 응답이 시시각각 변해서 원본↔신규 대조가 불가능해진다. 이미 집계된
-- 상태로 두면 데이터가 고정된다.
--
-- 삽입 순서 주의: events.incident_id -> incidents.id 이고
-- incidents.first_event_id -> events.id 라 FK가 서로를 가리킨다.
-- 그래서 events(incident_id NULL) -> incidents -> events UPDATE 순으로 넣는다.
-- ===============================================================

BEGIN;

-- 1단계: events (Bronze). incident_id는 아직 NULL — incidents가 없으므로.
INSERT INTO events (
    id, device_id, patient_id, event_type, confidence, severity, event_status,
    occurred_at, clip_url, payload_json, household_id, camera_id, incident_id
) VALUES
  -- 신규 형식: risk_score가 1~5 결정값
  (901, 2, 1, 'fall_detected', 0.83, 90, 'detected', '2026-07-01T10:00:00Z', NULL,
   '{"source_label":"lying_down","risk_label":"danger","risk_score":5,"raw_score":0.83}'::jsonb,
   1, 1, NULL),
  (902, 2, 1, 'fall_detected', 0.91, 90, 'detected', '2026-07-01T10:00:09Z', NULL,
   '{"source_label":"lying_down","risk_label":"danger","risk_score":5,"raw_score":0.91}'::jsonb,
   1, 1, NULL),
  -- 옛 형식: risk_score가 0~1 float (CLAUDE.md 기록)
  (903, 2, 1, 'fall_detected', 0.78, 85, 'detected', '2026-07-01T10:00:18Z', NULL,
   '{"source_label":"lying_down","risk_label":"danger","risk_score":0.78,"raw_score":0.78}'::jsonb,
   1, 1, NULL),
  -- payload에 기대 키가 없음 -> activity_label/risk_label 등이 전부 None으로 나가는 경로
  (904, 2, 1, 'abnormal_posture', 0.62, 30, 'detected', '2026-07-01T11:00:00Z', NULL,
   '{"note":"키가 다른 payload"}'::jsonb,
   1, 1, NULL),
  -- Silent Orphan H-3: 미등록 디바이스 -> household_id/camera_id NULL, confidence도 NULL
  (905, 1, 2, 'fall_detected', NULL, 0, 'detected', '2026-07-01T12:00:00Z', NULL,
   NULL, NULL, NULL, NULL);

-- 2단계: incidents (Silver). 이제 events가 있으므로 first/last_event_id를 채울 수 있다.
INSERT INTO incidents (
    id, patient_id, household_id, incident_type, started_at, ended_at,
    raw_event_count, max_confidence, max_severity, avg_confidence,
    first_event_id, last_event_id, status
) VALUES
  (901, 1, 1, 'fall_detected',    '2026-07-01T10:00:00Z', '2026-07-01T10:00:18Z',
   3, 0.91, 90, 0.84, 901, 903, 'closed'),
  (902, 1, 1, 'abnormal_posture', '2026-07-01T11:00:00Z', '2026-07-01T11:00:05Z',
   1, 0.62, 30, 0.62, 904, 904, 'closed'),
  -- 고아 incident: household_id NULL (미등록 디바이스에서 온 이벤트가 뭉친 경우)
  (903, 2, NULL, 'fall_detected', '2026-07-01T12:00:00Z', '2026-07-01T12:00:02Z',
   1, NULL, 0, NULL, 905, 905, 'closed');

-- 3단계: events를 incidents에 연결. 이걸로 집계 스케줄러가 이 행들을 건드리지 않게 된다.
UPDATE events SET incident_id = 901 WHERE id IN (901, 902, 903);
UPDATE events SET incident_id = 902 WHERE id = 904;
UPDATE events SET incident_id = 903 WHERE id = 905;

-- alerts — source 두 종류 다 (EDGE = 1차 AI 즉시, ANALYZER = 2차 AI 추세)
INSERT INTO alerts (
    id, source, event_id, device_id, patient_id, alert_type, alert_level,
    message, occurred_at, payload_json, is_read, household_id, camera_id
) VALUES
  (901, 'EDGE', 901, 2, 1, 'FALL', 'critical',
   '거실에서 낙상이 감지되었습니다.', '2026-07-01T10:00:01Z',
   '{"confidence":0.83}'::jsonb, false, 1, 1),
  -- event_id NULL (추세 알림은 특정 이벤트에 안 매임) + payload NULL + 읽음 처리됨
  (902, 'ANALYZER', NULL, 2, 1, 'TREND', 'warning',
   '최근 3일간 야간 활동이 증가했습니다.', '2026-07-01T13:00:00Z',
   NULL, true, 1, 1),
  -- 미매핑 환자(P002)의 알림 — guardian에게 안 보여야 함 (역할 필터 검증용)
  (903, 'EDGE', 905, 1, 2, 'FALL', 'critical',
   'P002 낙상 감지.', '2026-07-01T12:00:01Z',
   '{"confidence":null}'::jsonb, false, NULL, NULL);

-- risk_scores — analyzed_from/to 가 NULL인 경우 포함
INSERT INTO risk_scores (id, patient_id, score, risk_level, reason, analyzed_from, analyzed_to) VALUES
  (901, 1, 72, 'high', '낙상 3회 + 야간 활동 증가',
   '2026-06-28T00:00:00Z', '2026-07-01T00:00:00Z'),
  (902, 1, 31, 'low', NULL, NULL, NULL),
  (903, 2, 55, 'medium', 'P002 참고값', NULL, NULL);

-- clips — 상태별 + 선택 필드 NULL 케이스.
-- S3가 미설정이라 video_url은 전부 None으로 나간다 (clip_presigned_get_url의 None 경로).
-- 시각은 now() 상대값이다: list_clips가 CLIP_RETENTION_DAYS(30일) 지난 클립을 숨기므로
-- (S3 lifecycle로 이미 지워진 객체의 죽은 URL 방지, 2026-09-18) 고정 날짜를 쓰면
-- 시드가 한 달 뒤부터 전부 안 보여 테스트가 무의미해진다.
INSERT INTO clips (
    id, event_id, s3_key, file_name, clip_uuid, patient_id, device_id,
    related_alert_id, event_type, occurred_at, requested_duration_sec,
    file_size_bytes, actual_duration_sec, uploaded_at, status, household_id
) VALUES
  (901, 901, 'clips/home001/P001/20260701_100000.mp4', '20260701_100000.mp4',
   '11111111-1111-4111-8111-111111111111', 1, 2, 901, 'fall_detected',
   now() - interval '1 day', 5, 1048576, 5.2, now() - interval '1 day' + interval '30 seconds', 'uploaded', 1),
  -- pending: 업로드 전이라 크기/길이/업로드시각이 전부 NULL
  (902, 902, 'clips/home001/P001/20260701_100009.mp4', NULL,
   '22222222-2222-4222-8222-222222222222', 1, 2, NULL, 'fall_detected',
   now() - interval '1 day' + interval '9 seconds', 5, NULL, NULL, NULL, 'pending', 1),
  -- 미매핑 환자(P002) 클립 — guardian에게 안 보여야 함
  (903, 905, 'clips/unknown/P002/20260701_120000.mp4', '20260701_120000.mp4',
   '33333333-3333-4333-8333-333333333333', 2, 1, NULL, 'fall_detected',
   now() - interval '22 hours', 5, 2097152, 4.8, now() - interval '22 hours' + interval '40 seconds', 'uploaded', NULL),
  -- uploaded 이지만 선택 필드가 NULL — clip_row_to_dict의 None 가드 경로를 태운다.
  -- 902(pending)로는 이 경로를 못 태운다: list_clips가 status='uploaded'만 노출하므로
  -- pending 클립은 애초에 응답에 안 나온다.
  (904, 904, 'clips/home001/P001/20260701_110000.mp4', NULL,
   '44444444-4444-4444-8444-444444444444', 1, 2, NULL, 'abnormal_posture',
   now() - interval '23 hours', NULL, NULL, NULL, now() - interval '23 hours' + interval '20 seconds', 'uploaded', 1),
  -- S3 lifecycle로 이미 지워졌을 클립 (업로드 40일 전, DB엔 아직 status='uploaded').
  -- 운영에서 2026-09-18 실발현한 케이스. list_clips 나이 필터가 숨겨야 하고,
  -- expire_lifecycle_deleted_clips 가 돌면 status='expired'로 바뀐다.
  (905, NULL, 'clips/home001/P001/20260809_120000.mp4', '20260809_120000.mp4',
   '55555555-5555-4555-8555-555555555555', 1, 2, NULL, 'fall_detected',
   now() - interval '40 days', 5, 1048576, 5.0, now() - interval '40 days' + interval '30 seconds', 'uploaded', 1);

-- 명시적 id를 썼으므로 시퀀스를 뒤로 민다
SELECT setval('incidents_id_seq',   (SELECT MAX(id) FROM incidents));
SELECT setval('events_id_seq',      (SELECT MAX(id) FROM events));
SELECT setval('alerts_id_seq',      (SELECT MAX(id) FROM alerts));
SELECT setval('risk_scores_id_seq', (SELECT MAX(id) FROM risk_scores));
SELECT setval('clips_id_seq',       (SELECT MAX(id) FROM clips));

COMMIT;
