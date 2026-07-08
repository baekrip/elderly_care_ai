# 독거노인 케어 AI — 개선 구현 계획

> 기준 문서: `endtask.md v2.6`, `구성.md`, `진행상황.md (2026-05-31)`, `백엔드.md (2026-05-29)`  
> 작성일: 2026-06-01  
> 목적: 현재 확인된 병목·최적화 문제·보안 취약점을 구현 가능한 단위로 분류하고, 각 항목의 원인·영향·해결방법·파일 목록·테스트 기준을 정의한다.

---

## 목차

1. [프로젝트 현황 요약](#1-프로젝트-현황-요약)
2. [병목 문제 및 개선 계획](#2-병목-문제-및-개선-계획)
3. [최적화 문제 및 개선 계획](#3-최적화-문제-및-개선-계획)
4. [보안 문제 및 개선 계획](#4-보안-문제-및-개선-계획)
5. [인터페이스 계약 및 공유 모듈 안전성](#5-인터페이스-계약-및-공유-모듈-안전성)
6. [구현 순서 및 의존 관계](#6-구현-순서-및-의존-관계)
7. [완료 기준 및 운영 gate](#7-완료-기준-및-운영-gate)
8. [승인 요청 항목](#8-승인-요청-항목)

---

## 1. 프로젝트 현황 요약

### 1.1 확정된 구조

```
[Pi5 + wide v3 카메라]
  → Picamera2 캡처 (640×360, 30 FPS 목표)
  → YOLO pose ONNX CPU 추론 (~5 FPS 현재 한계)
  → skeleton JSON WebSocket → Orin
  → segment_ring 로컬 clip 보관
  → Orin REST clip 요청 수신 → clip export → Orin 전송
  → RTSP/WebRTC H.264 raw fallback (MediaMTX)

[Jetson Orin Nano]
  → FastAPI Edge Hub
  → Pi5 skeleton WebSocket 수신
  → XGBoost + TriggerEngine + ST-GCN + TensorRT FP16
  → 위험 timestamp 기준 Pi5 clip REST 요청
  → face blur 적용/검증 후 S3 presigned 업로드
  → POST /api/v1/events/batch, alerts/immediate → EC2 백엔드

[EC2 백엔드 (Flask 단일 app.py)]
  → events/batch, alerts/immediate, clips/upload-url, clips/confirm
  → SSE /api/v1/alerts/stream → 프론트엔드
  → S3 capstone-elderly-clips

[프론트엔드 (Vite + React + TypeScript + Tailwind + shadcn/ui)]
  → MediaMTX WebRTC 영상 수신
  → overlay JSON: Orin 별도 WS 경로 (EC2 경유 불가)
```

### 1.2 최신 검증 결과 (2026-05-31)

| 항목 | 수치 | 상태 |
|------|------|------|
| Pi5 카메라 FPS | 30.0 FPS (캡처/RTSP) | ✅ 달성 |
| Pi5 pose 추론 FPS | ~5 FPS | ⚠️ 한계치 |
| pose latency p95 | 184.6 ms | ⚠️ 높음 |
| loop latency p95 | 47.8 ms | ✅ 양호 |
| drop rate | 0.0 | ✅ 달성 |
| DANGER e2e | event_count=8, state=CONFIRMED | ✅ 성공 |
| overlay WS | 미구현 | ❌ 없음 |
| PatternAnalyzer snapshot | 미구현 | ❌ 없음 |
| 외부 백엔드 실전송 검증 | IP/TOKEN 확정 대기 | 🔶 대기 |

---

## 2. 병목 문제 및 개선 계획

### 2.1 Pi5 pose 추론 FPS 병목 (최우선)

**현상:** Pi5 CPU ONNX 추론이 ~95~100 ms/frame 소요, 결과적으로 pose 분석 FPS ~5. 카메라/RTSP는 30 FPS 달성했으나 skeleton 시간 해상도가 부족하다.

**영향:** 빠른 낙상 동작에서 skeleton window가 듬성해 ST-GCN 판정 신뢰도 저하 가능. NEAR_FALL_STUMBLE 같은 짧은 전조 동작 누락 위험.

**원인 분류:**

| 원인 | 가중치 |
|------|--------|
| ONNX CPU 추론 자체 한계 (ARM64, Pi5 4코어) | 높음 |
| 캡처 루프와 추론 worker가 동일 스레드 공유 가능성 | 중간 |
| imgsz=320임에도 전처리 포함 latency 높음 | 낮음 |

**해결 방안 비교:**

| 방안 | FPS 예상 개선 | 구조 변경 | 정확도 위험 | 우선순위 |
|------|--------------|-----------|------------|---------|
| A. capture/stream 스레드와 inference worker 완전 분리 | 5→8 FPS 추정 | 낮음 | 없음 | **1** |
| B. 경량 pose 모델 교체 (더 작은 ONNX) | 5→10 FPS 추정 | 낮음 | 중간 (정확도 확인 필요) | **2** |
| C. inference_stride 증가 (현재 3) | 즉시 적용 가능 | 없음 | skeleton 해상도 저하 | **3 (임시)** |
| D. Orin pose offload (Pi5 영상 relay → Orin 추론) | 5→15+ FPS 가능 | 높음 | 지연/대역폭 리스크 | **4 (검증 후)** |

**2.1-A: capture/inference 스레드 분리 (즉시 구현 가능)**

```
현재:
  [capture loop] → frame → [pose 추론] → skeleton → WS 전송
  (동일 스레드 순차 실행)

개선:
  [capture thread] → frame queue → [inference worker thread]
                                         → skeleton → WS 전송
  capture: 30 FPS 유지
  inference: 비동기, queue에서 가장 최신 frame만 소비 (drop old)
```

수정 파일:
- `edge/main.py`: 스레드 분리 구조로 재작성
- `edge/pose_estimator.py`: worker 루프 추가
- `edge/config.raspi_cam01.yaml`: `inference_queue_maxsize` 파라미터 추가
- `tests/test_capture_inference_split.py`: 신규

완료 기준:
- capture FPS ≥ 28 유지
- pose inference FPS ≥ 8 달성
- skeleton 누락 없이 WS 전송 확인

---

**2.1-B: baseline 고정 후 경량 모델 비교 (benchmark 필요)**

baseline 측정 프로토콜 (현재 없음 → 추가 필요):

```yaml
# tools/benchmark_pose_baseline.py 추가
replay_clips:
  - normal_adl_5min.mp4       # 정상 ADL
  - fall_10samples_3min.mp4   # 낙상 10회 포함
  - night_lowlight_3min.mp4   # 야간 저조도
repeat: 3                     # 중앙값 사용
metrics:
  - pose_fps
  - skeleton_confidence_mean
  - DANGER_recall
  - FP_per_hour
env: Pi5 단독, CPU 온도 안정화 후 측정
```

비교 후보:
- 현재: `yolo26s-pose.onnx` imgsz=320
- 후보1: `yolo11n-pose.onnx` imgsz=256
- 후보2: `yolo11s-pose.onnx` imgsz=320

수정 파일:
- `tools/benchmark_pose_models.py`: 신규, 후보 모델 비교 스크립트
- `experiments/behavior_training/reports/pose_benchmark.json`: 결과 저장

완료 기준:
- 후보 모델 FPS/정확도/latency 비교표 생성
- DANGER recall이 현재(heuristic 기준) 대비 유지 또는 향상 확인

---

**2.1-C: Orin pose offload (구조 변경, 별도 승인 필요)**

```
현재:
  Pi5: 캡처 + 추론 → skeleton WS
개선 후보:
  Pi5: 캡처 → raw/encoded frame WS or RTSP → Orin
  Orin: frame 수신 + pose 추론 + skeleton 생성 + AI 분류
```

latency 예산 (현재 미정의):

```
목표 end-to-end: capture_ts → risk_label ≤ 500 ms
  Pi5 캡처 → Orin 수신: ~50 ms (LAN)
  Orin pose 추론: TBD (측정 대상)
  Orin AI 분류: ~30 ms
  허용 여유: ~420 ms
```

구현 전 필수 측정:
- Orin GPU pose 추론 latency (TensorRT FP16 기준)
- Pi5 → Orin 640×360 H.264 relay bitrate/지연
- Orin 동시 부하 (skeleton 수신 + AI + clip + WS)

수정 파일 (설계 문서만 먼저):
- `tools/remote_pose_offload_probe.py`: 신규, Pi5→Orin 전송 latency 측정
- `tests/test_pose_offload_contract.py`: 신규, offload 인터페이스 계약 테스트
- `docs/pose_offload_design.md`: 설계 문서

---

### 2.2 PatternAnalyzer 재시작 후 상태 손실

**현상:** Orin 서버 재시작 시 PatternAnalyzer 메모리 상태가 초기화된다. 1일/1~2주 패턴 분석 연속성이 끊긴다.

**원인:** 상태를 파일로 저장하는 snapshot 메커니즘이 없다.

**영향:** 재시작 후 anomaly 판정 기준이 리셋. 특히 `inactivity_timer`, `anomaly_vote` 누적값이 사라져 일과성 이상 감지 품질 저하.

**해결 방안:**

snapshot 저장/복원 구조:

```python
# server/storage/results/pattern_state_snapshot.json
{
  "schema_version": "1.0",
  "created_at": "2026-06-01T00:00:00Z",
  "camera_id": "raspi_cam01",
  "window_start_ts": 1748736000000,
  "window_end_ts": 1748822400000,
  "state": {
    "tracks": {...},
    "inactivity_timer": {...},
    "anomaly_vote": {...},
    "summary_window": {...}
  }
}
```

저장 정책:

```yaml
snapshot:
  interval_sec: 60         # 60초마다 또는 N segment마다
  path: server/storage/results/pattern_state_snapshot.json
  atomic: true             # tmp → rename (POSIX atomic)
  max_track_age_sec: 3600  # 1시간 비활성 track 제외
  max_size_kb: 512         # 초과 시 오래된 track 우선 제거
```

복원 정책:

```python
# 서버 시작 시
if snapshot exists and valid:
    if (now - snapshot.window_end_ts) > config.max_gap_sec (300):
        restore tracks only, reset anomaly_vote/inactivity_timer
        log: "gap too large, partial restore"
    else:
        full restore
else:
    start fresh, log warning
```

atomic write 필수 구현:

```python
import pathlib, json, os

def save_snapshot_atomic(path: str, state: dict):
    tmp = pathlib.Path(path).with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False))
    os.rename(tmp, path)  # POSIX atomic
```

수정 파일:
- `server/services/pattern_analyzer.py`: snapshot save/restore 메서드 추가
- `server/result_archive.py`: snapshot 경로 관리
- `server/main.py`: 시작 시 snapshot 복원 호출
- `server/config.yaml`, `server/config.orin.yaml`: snapshot 설정 추가
- `tests/test_pattern_state_snapshot.py`: 신규
- `tests/test_pattern_restore.py`: 신규
- `tests/test_snapshot_atomic_write.py`: 신규 (쓰기 중 강제 종료 시 이전 snapshot 보존)

완료 기준:
- 재시작 후 PatternAnalyzer가 snapshot에서 상태 복원
- 300초 이상 gap이면 partial restore + warning 로그
- 깨진 snapshot은 무시하고 빈 상태로 시작
- schema version mismatch는 복원하지 않음

---

### 2.3 overlay JSON 실시간 경로 미구현

**현상:** 프론트엔드에서 bbox/skeleton/action/risk label을 MediaMTX WebRTC 영상 위에 Canvas로 표시할 수 있는 경로가 없다.

**제약:** EC2 백엔드에 WebSocket endpoint가 없으므로 Orin 직접 WS로만 가능하다.

**해결 방안:** Orin에서 `/ws/overlay/{camera_id}` WebSocket endpoint를 구현하고, 프론트가 이 경로에서 overlay JSON을 수신한다.

overlay JSON 스키마 (확정 필요):

```json
{
  "camera_id": "raspi_cam01",
  "frame_id": 12345,
  "sequence_id": "seq-001",
  "capture_ts": 1748736000000,
  "analysis_ts": 1748736000200,
  "source_width": 640,
  "source_height": 360,
  "display_policy": "show_all",
  "analysis_stage": "stgcn",
  "latency_ms": 200,
  "stale": false,
  "drop_count": 0,
  "fps": 5.0,
  "tracks": [
    {
      "track_id": "t001",
      "bbox": [120, 80, 240, 360],
      "keypoints": [[x, y, conf], ...],
      "action_label": "WALKING",
      "risk_label": "NORMAL",
      "risk_score": 0.12,
      "event_state": "NORMAL"
    }
  ]
}
```

설계 원칙:
- 좌표는 원본 frame 기준 pixel (640×360). 프론트에서 video element 크기로 scale.
- 느린 subscriber는 최신 frame만 수신 (drop old 정책).
- subscriber 연결 끊김 감지: heartbeat ping/pong + 10초 timeout으로 subscriber 목록에서 제거.
- overlay frame 생성 시점: skeleton 수신 직후 기본 bbox/keypoint → action/risk 판정 후 label 추가.

수정 파일:
- `shared/protocol.py`: overlay JSON 스키마 상수 추가
- `server/main.py`: `/ws/overlay/{camera_id}` 라우트 등록
- `server/api/overlay_ws.py`: 신규, WS endpoint
- `server/services/overlay_broadcaster.py`: 신규, camera_id별 subscriber 관리
- `server/services/pi5_pipeline.py`: 분석 결과를 overlay frame으로 변환
- `tools/overlay_ws_probe.py`: 신규, 10초 동안 frame 수/평균 latency/필드 유효성 검사
- `tests/test_overlay_protocol.py`: 신규
- `tests/test_overlay_broadcaster.py`: 신규

완료 기준:
- Orin `/ws/overlay/raspi_cam01` 에서 overlay JSON 실시간 수신
- EC2 백엔드 수정 없이 동작
- probe 도구로 10초 frame 수 ≥ 40, 평균 latency ≤ 300 ms 확인

---

### 2.4 외부 백엔드 events/alerts/clips 실전송 미완

**현상:** 백엔드 연동 코드는 구현됐으나 운영 IP와 APP_TOKEN 확정 대기 중. 실제 DANGER 이벤트 전체 경로 검증이 없다.

**현재 구현 상태:** backend_forwarder, batch scheduler, pending retry 구현 완료. 단, 외부 서버 smoke 미실행.

**필요 추가 작업:**

pending retry 상한 (현재 미정의):

```yaml
retry_policy:
  max_pending_events: 10000
  max_pending_age_hours: 24
  backoff_seconds: [5, 30, 120, 600]
  overflow_action: drop_oldest
```

clip upload fallback 정책 (현재 미정의):

```yaml
clip_fallback:
  local_dir: server/storage/clips/pending/
  max_size_gb: 2.0
  retention_hours: 48
  oversized_action: skip
```

payload contract 버전 관리 (현재 없음):

```python
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Contract-Version": "2026-05-29"  # 계약 변경 추적용
}
```

4xx 오류 처리 분리 (현재 없음):

```python
if response.status_code >= 500 or network_error:
    pending_queue.append(event)       # retry 대상
elif response.status_code >= 400:
    error_report.append(event)        # retry 하지 않음, report에만 기록
    log.warning(f"contract error: {response.json()}")
```

수정 파일:
- `server/services/backend_forwarder.py`: 4xx/5xx 분리, retry 상한, contract version 헤더
- `server/services/backend_clip_uploader.py`: 신규, clip upload adapter
- `server/config.yaml`, `server/config.orin.yaml`: retry 설정 추가
- `tests/test_backend_payload_contract.py`: 신규
- `tests/test_backend_clip_uploader.py`: 신규
- `tests/test_backend_forwarder_retry.py`: 신규

smoke 단계 분리:

| 단계 | 대상 | 조건 |
|------|------|------|
| 1 | mock 서버 대상 payload 검증 | 항상 실행 가능 |
| 2 | 실 서버 events/batch smoke | APP_TOKEN 확보 후 |
| 3 | 실 서버 clip upload smoke | APP_TOKEN + AWS key 확보 후 |

완료 기준:
- mock 서버 대상 events/batch, alerts/immediate, clip upload 전체 경로 통과
- 4xx는 pending에 남지 않고 error report에만 기록
- pending 최대 10,000건 초과 시 oldest drop 동작 확인
- 코드/config에 IP, token, AWS key 없음

---

## 3. 최적화 문제 및 개선 계획

### 3.1 일상행동 모델 부재 — heuristic 의존

**현상:** 현재 행동 분류는 heuristic fallback에 의존. `LYING_BED` vs `LYING_FLOOR`, `BENDING_REACHING`, `EXERCISING_STRETCHING`, `NEAR_FALL_STUMBLE`을 구분하지 못한다.

**영향:** FP/hour가 높고 NORMAL batch로 보내는 데이터 품질이 낮다. 2차 AI 패턴 분석 정밀도 저하.

**해결 방안:** 라벨 schema → feature schema → 모델 분리 순서로 확장.

라벨 스키마 (확정 필요):

```jsonl
// action_state_labels.jsonl
{
  "camera_id": "raspi_cam01",
  "track_id": "t001",
  "start_ts_ms": 1748736000000,
  "end_ts_ms": 1748736005000,
  "label": "LYING_BED",
  "source": "manual",
  "confidence": 0.9,
  "notes": "침대 위 수면 확인"
}

// risk_event_labels.jsonl
{
  "camera_id": "raspi_cam01",
  "track_id": "t001",
  "start_ts_ms": 1748736000000,
  "end_ts_ms": 1748736003000,
  "label": "NEAR_FALL_STUMBLE",
  "source": "manual",
  "confidence": 0.85,
  "notes": "발 걸림 후 회복"
}
```

feature schema version (현재 없음):

```python
FEATURE_SCHEMA_VERSION = "1.0.0"

ACTION_FEATURE_COLUMNS = [
    "speed_mean", "speed_std", "accel_mean",
    "jerk_max", "motion_energy", "pose_delta",
    "instability", "static_duration_ms",
    "bed_roi_overlap", "chair_roi_overlap",
    "floor_roi_overlap", "table_roi_overlap",
    "bathroom_roi_overlap",
    "speed_1s_mean", "speed_3s_mean", "speed_5s_mean",
    "speed_1s_std", "speed_3s_std", "speed_5s_std",
    "label_duration_ms", "lying_floor_duration_ms",
]
```

모델 분리:

| 모델 | 입력 | 출력 | 파일 |
|------|------|------|------|
| action model | ACTION_FEATURE_COLUMNS | 일상행동 상태 | `edge/models/xgboost_action.json` |
| risk event model | RISK_FEATURE_COLUMNS | 낙상/전조/정적 위험 | (기존 `xgboost_fall_binary.json` 대체 후보) |

운영 gate 수치 기준:

| 지표 | 조건 | 미달 시 처리 |
|------|------|------------|
| DANGER recall | ≥ 0.90 | 모델 교체 금지 |
| LYING_BED F1 | ≥ 0.80 | 재학습 또는 ROI feature 보완 |
| LYING_FLOOR F1 | ≥ 0.80 | 재학습 |
| FP/hour (정상 구간) | ≤ 2.0 | threshold 재조정 |
| NEAR_FALL_STUMBLE precision | ≥ 0.70 | feature 보완 |

학습 재현성 보장 (현재 없음):

```
experiments/behavior_training/runs/YYYYMMDD_HHMMSS/
  run_manifest.json
    - data_sha256: {...}  # 사용된 데이터 파일 해시
    - python_version: "3.11.x"
    - library_versions: {xgboost: "2.x", numpy: "1.x"}
    - random_seed: 42
    - feature_schema_version: "1.0.0"
```

데이터 품질 gate (현재 없음):

```
tools/validate_label_quality.py
  - 클래스별 최소 샘플: NEAR_FALL_STUMBLE ≥ 50 window
  - confidence < 0.6 비율 ≤ 20%
  - 동일 track_id 내 label 1초 내 3회 초과 변화: 경고
  - LYING_BED vs LYING_FLOOR overlap window: 오류
```

수정 파일:
- `edge/feature_extractor.py`: ActionFeatureSet / RiskEventFeatureSet 분리
- `edge/action_classifier.py`: action model 로딩
- `shared/training_dataset.py`: 라벨 스키마 상수
- `tools/validate_label_quality.py`: 신규
- `tools/export_xgboost_tier_features.py`: feature schema version 추가
- `tools/train_xgboost_tier.py`: run_manifest 저장
- `tests/test_action_label_schema.py`: 신규
- `tests/test_feature_schema_version.py`: 신규
- `tests/test_action_export.py`: 신규
- `tests/test_risk_event_export.py`: 신규

완료 기준 (학습 전 단계):
- 라벨 JSONL schema 테스트 통과
- feature column 순서/길이 고정 검증 통과
- run_manifest 생성 확인
- 데이터 품질 gate 실행 확인

실제 학습 실행, 모델 교체는 별도 승인 후 진행.

---

### 3.2 12~24시간 장기 안정성 미검증

**현상:** 장기 운영 시 process 안정성, JSONL 누적, memory leak, pending queue 증가 여부를 자동으로 측정하는 report가 없다.

**해결 방안:** 5분 단위 sample 누적 stability report 자동화.

실패 조건 수치 기준 (현재 없음):

```yaml
failure_thresholds:
  edge_process_down_sec: 30
  server_process_down_sec: 60
  fps_below_threshold: 20.0
  fps_duration_sec: 60          # 60초 지속 시 failure
  pending_queue_max: 500
  jsonl_write_gap_sec: 120
  memory_rss_growth_mb_per_hr: 50
  reconnect_count_per_hr: 10    # WS 재연결 과다 경고
```

synthetic probe 주기 삽입 (미결정 → 확정):

```
30분마다 DANGER synthetic skeleton을 주입해 pipeline 전체 생존 확인.
이벤트가 없는 야간 구간에서 모델/FSM이 silently broken 상태인지 확인하기 위해 필수.
```

report 구조:

```json
{
  "duration_sec": 43200,
  "avg_fps": 29.8,
  "drop_rate_mean": 0.002,
  "pose_inference_fps": 5.1,
  "orin_health_ok_rate": 0.999,
  "pending_queue_max": 12,
  "restart_count": 0,
  "memory_rss_max_mb": 420,
  "synthetic_probe_results": [
    {"ts": "2026-06-01T01:00:00Z", "danger_detected": true, "latency_ms": 310}
  ],
  "failure_events": []
}
```

수정 파일:
- `tools/remote_device_ops.py`: 장기 관찰 action, 5분 sample 누적, synthetic probe 삽입
- `tests/test_remote_device_ops.py`: report schema, timeout/partial failure, pending parser
- `server/config.yaml`: failure_thresholds 설정

완료 기준:
- 12~24시간 report 자동 생성
- 장애 발생 시 어느 단계에서 멈췄는지 report만으로 확인 가능
- synthetic probe 30분 주기 동작 확인

장기 실행은 장비 사용 가능 시간 확보 후 별도 승인.

---

## 4. 보안 문제 및 개선 계획

### 4.1 APP_TOKEN/AWS key 노출 위험 (현재 부분 조치)

**현황:** `server/config.yaml`, `server/config.orin.yaml`에서 과거 EC2 IP/APP_TOKEN 하드코딩 제거 완료 (2026-05-30). 런타임 환경변수 주입 구현 완료.

**남은 위험:**

| 위험 | 파일/경로 | 심각도 |
|------|----------|--------|
| backend_pending.jsonl에 Authorization 헤더 로깅 가능 | `server/services/backend_forwarder.py` | 높음 |
| 로그 파일(perf_stats, trigger_debug)에 URL 포함 가능 | `edge/storage/results/` | 중간 |
| tools/test_backend_batch.py 실행 시 환경변수 평문 노출 | `tools/test_backend_batch.py` | 중간 |
| deploy bundle 생성 시 .env 파일 포함 여부 미확인 | `tools/build_deploy_bundles.py` | 높음 |
| clip upload pending JSONL에 presigned URL 포함 가능 | `server/storage/results/` | 중간 |

**개선 방안:**

4.1-A: forwarder 로그에서 토큰 마스킹:

```python
def _mask_sensitive(data: dict) -> dict:
    masked = data.copy()
    if "authorization" in str(masked).lower():
        # headers에서 Bearer 토큰 마스킹
        if "headers" in masked:
            auth = masked["headers"].get("Authorization", "")
            if auth.startswith("Bearer "):
                masked["headers"]["Authorization"] = "Bearer ***"
    return masked

# pending JSONL 저장 시
safe_record = _mask_sensitive(failed_record)
jsonl_writer.write(safe_record)
```

4.1-B: deploy bundle에서 .env 제외 확인:

```python
# tools/build_deploy_bundles.py
EXCLUDED_PATTERNS = [".env", "*.env", "*.token", "secrets/"]
for pattern in EXCLUDED_PATTERNS:
    assert not any(bundle_path.glob(pattern)), f"Secret file found: {pattern}"
```

4.1-C: clip pending JSONL에서 presigned URL 단축:

```python
# presigned URL은 저장하지 않고 s3_key만 저장
pending_record = {
    "s3_key": s3_key,  # upload_url 아님
    "error": str(e),
    "ts": now_iso()
}
```

4.1-D: tools/test_backend_batch.py 환경변수 마스킹:

```python
import os
token = os.environ.get("APP_TOKEN", "")
if not token:
    raise SystemExit("APP_TOKEN 환경변수 필요. .env 파일에서 설정.")
# 출력 로그에서 토큰 마스킹
print(f"Using token: {'*' * (len(token) - 4)}{token[-4:]}")
```

수정 파일:
- `server/services/backend_forwarder.py`: 토큰 마스킹 유틸 추가
- `server/services/backend_clip_uploader.py`: presigned URL 비저장
- `tools/build_deploy_bundles.py`: .env 제외 검증
- `tools/test_backend_batch.py`: 환경변수 마스킹
- `tests/test_token_masking.py`: 신규, pending JSONL에 토큰 미포함 검증

---

### 4.2 Pi5 → 백엔드 직접 전송 경로 잔류 위험

**현황:** `edge/config.raspi_cam01.yaml`에 `base_url=http://192.168.45.241:8000` (Orin LAN)만 설정. 외부 백엔드 직접 전송은 정책상 금지.

**남은 위험:** Pi5 edge 코드에 외부 backend_forwarder 인스턴스가 남아 있을 경우, 설정값이 실수로 변경되면 Pi5가 직접 외부로 이벤트를 보낼 수 있다.

**개선 방안:**

Pi5 skeleton_sender 모드에서 backend_forwarder 인스턴스 생성 자체를 코드 레벨에서 차단:

```python
# edge/main.py
if config.runtime.role == "skeleton_sender":
    assert config.backend.base_url == "" or \
           config.backend.base_url.startswith("http://192.168."), \
           "skeleton_sender mode: external backend URL not allowed"
    backend_forwarder = None  # 인스턴스 생성 금지
```

```yaml
# edge/config.raspi_cam01.yaml
runtime:
  role: skeleton_sender
backend:
  base_url: ""   # 반드시 비워둠. Orin LAN URL도 여기에 넣지 않음
  token: ""      # Pi5는 백엔드 토큰 불필요
```

수정 파일:
- `edge/main.py`: skeleton_sender 모드 외부 URL 차단 assertion
- `edge/config.raspi_cam01.yaml`: backend.base_url 빈 값 강제
- `tests/test_pi5_no_external_forward.py`: 신규, skeleton_sender 모드에서 forwarder 미생성 확인

---

### 4.3 face blur 적용 검증 미완

**현황:** `edge/clip_blur.py` 구현 완료. Orin upload-stage에서 blur 적용/검증 정책 확정. 단, 실제 blur 적용 후 품질 검증(얼굴 감지 여부 재확인)이 없다.

**위험:** blur가 실패하거나 얼굴 영역이 누락된 clip이 S3에 업로드될 수 있다.

**개선 방안:**

blur 후 재검증 단계 추가:

```python
# server/services/clip_blur_verifier.py (신규)
def verify_blur_applied(clip_path: str) -> BlurVerifyResult:
    """
    blur된 clip에서 YOLO face detector로 잔여 얼굴 영역 감지.
    감지 결과가 있으면 업로드 거부 후 재blur 또는 manual_review 큐 이동.
    """
    faces = detect_faces(clip_path)
    if faces:
        return BlurVerifyResult(ok=False, faces=faces, action="reblur_or_hold")
    return BlurVerifyResult(ok=True)
```

```yaml
# server/config.orin.yaml
clip_blur:
  verify_after_blur: true
  verify_fail_action: hold  # hold | reblur | skip_upload
  manual_review_dir: server/storage/clips/manual_review/
```

수정 파일:
- `server/services/clip_blur_verifier.py`: 신규
- `server/services/backend_clip_uploader.py`: blur 검증 후 업로드
- `tests/test_clip_blur_verifier.py`: 신규

완료 기준:
- blur 후 재감지에서 얼굴 영역 없음 확인
- 검증 실패 시 `manual_review/` 이동 동작 확인
- 실제 clip 대상 blur 시간 측정 (목표: ≤ 30초/clip)

---

### 4.4 LAN 내부 통신 인증 부재

**현황:** Pi5 → Orin WS/REST 통신은 LAN 내부이므로 인증 없이 동작. 이것은 현재 설계 의도이지만, Orin FastAPI endpoint가 LAN 외부에 노출될 경우 위험하다.

**현재 영향:** 낮음 (LAN 전용). 단, systemd 서비스 설정에 bind address가 명시되지 않으면 실수로 외부 노출 가능.

**개선 방안:**

Orin FastAPI bind address 명시:

```python
# server/main.py
uvicorn.run(app, host="0.0.0.0", port=8000)
# → 아래로 변경
uvicorn.run(app, host=config.server.bind_host, port=config.server.port)
```

```yaml
# server/config.orin.yaml
server:
  bind_host: "192.168.45.241"  # LAN IP만 바인딩
  port: 8000
```

clip REST server (Pi5) bind 확인:

```python
# edge/clip_rest_server.py
# port 8091이 LAN IP에만 바인딩되는지 확인
HTTPServer(("192.168.45.29", 8091), ClipHandler)
# 0.0.0.0 바인딩 금지
```

수정 파일:
- `server/main.py`: bind_host 설정 반영
- `server/config.orin.yaml`: bind_host 추가
- `edge/clip_rest_server.py`: bind address LAN IP 명시
- `edge/config.raspi_cam01.yaml`: clip_server.bind_host 추가
- `tests/test_server_bind_address.py`: 신규, 외부 IP 바인딩 시 오류 확인

---

## 5. 인터페이스 계약 및 공유 모듈 안전성

### 5.1 shared/protocol.py 변경 시 하위 호환성 정책 (현재 없음)

**위험:** `shared/protocol.py`와 `shared/training_dataset.py`는 edge, server, tools가 모두 의존한다. Pi5와 Orin의 protocol version이 어긋나면 silent 오류 발생.

**개선 방안:**

protocol version 필드 추가:

```python
# shared/protocol.py
PROTOCOL_VERSION = "2.1.0"

class SkeletonFrameBatch:
    schema_version: str = PROTOCOL_VERSION  # 모든 메시지에 포함

# 수신 측 (Orin)
def validate_protocol_version(msg: dict):
    if msg.get("schema_version") != PROTOCOL_VERSION:
        log.warning(f"Protocol version mismatch: {msg.get('schema_version')} != {PROTOCOL_VERSION}")
        # 하위 호환 가능 여부 판단 (major version 동일이면 허용)
```

변경 정책:
- optional 필드만 추가 가능 (기존 필드 제거/이름 변경 금지)
- 필드 추가 시 minor version 증가
- 기존 필드 의미 변경 시 major version 증가 → Pi5/Orin 동시 배포 필요

```python
# tests/test_protocol_version_compat.py
def test_missing_optional_field():
    # optional 필드 없어도 처리 가능한지 확인
    msg = minimal_skeleton_batch()
    result = parse_skeleton_batch(msg)
    assert result is not None

def test_version_mismatch_warning():
    msg = {"schema_version": "1.0.0", ...}
    with caplog.at_level(logging.WARNING):
        parse_skeleton_batch(msg)
    assert "version mismatch" in caplog.text
```

수정 파일:
- `shared/protocol.py`: PROTOCOL_VERSION, schema_version 필드, validate 함수
- `tests/test_protocol_version_compat.py`: 신규

---

### 5.2 운영 모델 rollback 정책 (현재 없음)

**위험:** 신규 모델 배포 후 DANGER recall이 저하될 때 이전 모델로 되돌리는 절차가 없다.

**개선 방안:**

model 디렉토리 관리:

```
edge/models/
  xgboost_fall_binary.json       ← 현재 운영
  xgboost_fall_binary.prev.json  ← 직전 버전 (자동 보관)
  xgboost_action.json            ← (신규, 추가 예정)
  xgboost_action.staging.json    ← (검증 대기)
```

rollback 조건:
- DANGER recall 2회 연속 < 0.90 → 자동 경고 + 수동 rollback 가이드

```python
# tools/check_model_gate.py
def check_production_gate(eval_report: dict) -> GateResult:
    danger_recall = eval_report["class_metrics"]["DANGER"]["recall"]
    if danger_recall < 0.90:
        return GateResult(ok=False, reason=f"DANGER recall {danger_recall:.2f} < 0.90")
    return GateResult(ok=True)
```

수정 파일:
- `tools/check_model_gate.py`: 신규
- `tools/build_deploy_bundles.py`: staging → production 승격 시 .prev 자동 생성

---

## 6. 구현 순서 및 의존 관계

### 6.1 단계별 구현 순서

병렬 진행 가능한 항목을 명시한다.

| 단계 | 항목 | 선행 조건 | 병렬 가능 |
|------|------|-----------|----------|
| 1 | 2.1-A capture/inference 스레드 분리 | 없음 | 1과 병렬 |
| 1 | 4.1 토큰 마스킹 + deploy bundle 검증 | 없음 | 1과 병렬 |
| 1 | 4.2 Pi5 외부 전송 차단 assertion | 없음 | 1과 병렬 |
| 1 | 5.1 protocol version 추가 | 없음 | 1과 병렬 |
| 2 | 2.2 PatternAnalyzer snapshot/restore | 없음 | 2와 병렬 |
| 2 | 2.3 overlay WS + broadcaster | 5.1 완료 | 2와 병렬 |
| 3 | 2.4 백엔드 adapter 보완 | APP_TOKEN/IP 확보 | 3과 병렬 |
| 3 | 3.1 라벨 schema + feature schema | 없음 | 3과 병렬 |
| 3 | 4.3 blur 검증 | 없음 | 3과 병렬 |
| 4 | 2.1-B pose model benchmark | 2.1-A 완료 | — |
| 5 | 3.2 12~24h 안정성 자동화 | 2.2 + 2.4 완료 | — |
| 6 | 2.1-C Orin pose offload | 2.1-B 결과 기반 결정 | — |

### 6.2 외부 선행 조건

| 조건 | 현재 상태 | 미충족 시 처리 |
|------|----------|--------------|
| EC2 Elastic IP 확정 | 미확정 | .env 미설정 시 외부 전송 비활성 |
| APP_TOKEN 전달 | 미확정 | 외부 smoke 금지 |
| AWS S3 key 전달 | 미확정 | clip upload smoke 금지 |
| 라벨 데이터 수집 | 부족 | schema/test 먼저 진행 |
| 장기 실기기 사용 시간 확보 | 미확정 | 3.2 실행 보류 |

---

## 7. 완료 기준 및 운영 gate

### 7.1 전체 완료 기준

| 항목 | 기준 | 측정 방법 |
|------|------|----------|
| Pi5 pose FPS | ≥ 8 FPS (스레드 분리 후) | perf_stats.jsonl |
| overlay WS | frame ≥ 4/sec, latency ≤ 300 ms | overlay_ws_probe.py |
| PatternAnalyzer 재시작 복원 | 재시작 후 5초 내 상태 복원 | test_pattern_restore.py |
| 외부 전송 mock smoke | events/batch + alerts + clips 전체 통과 | test_backend_payload_contract.py |
| 토큰 미노출 | pending JSONL에 Bearer 토큰 없음 | test_token_masking.py |
| Pi5 외부 전송 차단 | skeleton_sender 모드에서 assertion 통과 | test_pi5_no_external_forward.py |
| blur 검증 | blur 후 얼굴 감지 0건 | test_clip_blur_verifier.py |
| protocol version | version mismatch 시 warning 로그 | test_protocol_version_compat.py |
| 모델 gate | DANGER recall ≥ 0.90 확인 | check_model_gate.py |

### 7.2 운영 교체 금지 조건

아래 조건 중 하나라도 해당하면 운영 모델 교체 금지:

```
DANGER recall < 0.90
LYING_BED F1 < 0.80
LYING_FLOOR F1 < 0.80
FP/hour > 2.0 (정상 ADL 구간)
```

---

## 8. 승인 요청 항목

아래 순서대로 승인 후 구현 진행. 승인 없이 진행 가능한 항목은 즉시 시작 가능.

### 즉시 시작 가능 (승인 불필요)

- [ ] 2.1-A capture/inference 스레드 분리
- [ ] 4.1 토큰 마스킹 + deploy bundle 보안 검증
- [ ] 4.2 Pi5 외부 전송 차단 assertion
- [ ] 4.4 LAN bind address 명시
- [ ] 5.1 protocol version 필드 추가
- [ ] 5.2 model rollback 디렉토리 구조 정비

### 승인 필요

| 번호 | 항목 | 작업 범위 | 제외 항목 |
|------|------|-----------|----------|
| A | 3.1 라벨/feature schema + quality gate | schema 설계, 검증 테스트, run_manifest | 실제 학습 실행, 모델 교체 |
| B | 2.2 PatternAnalyzer snapshot/restore | schema, atomic save/restore, fallback, 테스트 | 12~24h 실기기 실행 |
| C | 2.3 overlay WS + broadcaster | protocol 확정, Orin WS, probe 도구, 테스트 | EC2 WebSocket 추가 |
| D | 2.4 백엔드 adapter 보완 + smoke | mock smoke, retry 상한, 4xx 분리, clip pending | 실 서버 전송 (토큰 확보 후) |
| E | 4.3 face blur 검증 | blur_verifier, manual_review, 테스트 | — |
| F | 2.1-B pose model benchmark | benchmark 프로토콜, 비교 스크립트 | 모델 교체 적용 |
| G | 3.2 12~24h 안정성 자동화 | stability report, synthetic probe, 실패 조건 | 장기 실기기 실행 (시간 확보 후) |
| H | 2.1-C Orin pose offload | F 결과 기반, 설계 문서 + 측정 도구 | 구조 변경 적용 |

---

## 부록: 신규 파일 목록

| 파일 | 분류 | 섹션 |
|------|------|------|
| `edge/main.py` | 수정 | 2.1-A, 4.2 |
| `edge/pose_estimator.py` | 수정 | 2.1-A |
| `edge/config.raspi_cam01.yaml` | 수정 | 2.1-A, 4.2, 4.4 |
| `edge/clip_rest_server.py` | 수정 | 4.4 |
| `server/services/pattern_analyzer.py` | 수정 | 2.2 |
| `server/services/backend_forwarder.py` | 수정 | 2.4, 4.1 |
| `server/services/backend_clip_uploader.py` | 신규 | 2.4, 4.1, 4.3 |
| `server/services/overlay_broadcaster.py` | 신규 | 2.3 |
| `server/services/clip_blur_verifier.py` | 신규 | 4.3 |
| `server/api/overlay_ws.py` | 신규 | 2.3 |
| `server/main.py` | 수정 | 2.2, 2.3, 4.4 |
| `server/config.yaml` | 수정 | 2.2, 2.4, 3.2 |
| `server/config.orin.yaml` | 수정 | 2.2, 2.4, 4.3, 4.4 |
| `shared/protocol.py` | 수정 | 5.1 |
| `shared/training_dataset.py` | 수정 | 3.1 |
| `edge/feature_extractor.py` | 수정 | 3.1 |
| `edge/action_classifier.py` | 수정 | 3.1 |
| `tools/build_deploy_bundles.py` | 수정 | 4.1, 5.2 |
| `tools/test_backend_batch.py` | 수정 | 4.1 |
| `tools/remote_device_ops.py` | 수정 | 3.2 |
| `tools/overlay_ws_probe.py` | 신규 | 2.3 |
| `tools/benchmark_pose_models.py` | 신규 | 2.1-B |
| `tools/benchmark_pose_baseline.py` | 신규 | 2.1-B |
| `tools/remote_pose_offload_probe.py` | 신규 | 2.1-C |
| `tools/validate_label_quality.py` | 신규 | 3.1 |
| `tools/check_model_gate.py` | 신규 | 5.2 |
| `tests/test_capture_inference_split.py` | 신규 | 2.1-A |
| `tests/test_pattern_state_snapshot.py` | 신규 | 2.2 |
| `tests/test_pattern_restore.py` | 신규 | 2.2 |
| `tests/test_snapshot_atomic_write.py` | 신규 | 2.2 |
| `tests/test_overlay_protocol.py` | 신규 | 2.3 |
| `tests/test_overlay_broadcaster.py` | 신규 | 2.3 |
| `tests/test_backend_payload_contract.py` | 신규 | 2.4 |
| `tests/test_backend_clip_uploader.py` | 신규 | 2.4 |
| `tests/test_backend_forwarder_retry.py` | 신규 | 2.4 |
| `tests/test_token_masking.py` | 신규 | 4.1 |
| `tests/test_pi5_no_external_forward.py` | 신규 | 4.2 |
| `tests/test_clip_blur_verifier.py` | 신규 | 4.3 |
| `tests/test_server_bind_address.py` | 신규 | 4.4 |
| `tests/test_action_label_schema.py` | 신규 | 3.1 |
| `tests/test_feature_schema_version.py` | 신규 | 3.1 |
| `tests/test_action_export.py` | 신규 | 3.1 |
| `tests/test_risk_event_export.py` | 신규 | 3.1 |
| `tests/test_protocol_version_compat.py` | 신규 | 5.1 |
| `tests/test_remote_device_ops.py` | 수정 | 3.2 |
| `docs/pose_offload_design.md` | 신규 | 2.1-C |
