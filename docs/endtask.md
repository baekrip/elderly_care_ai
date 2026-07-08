# 독거노인 케어 AI 프로젝트 목표 문서

## 문서 목적
- 이 문서는 프로젝트의 `목표`, `성능`, `기능`만을 기록하는 참고용 문서다.
- 세부 설계와 현재 승인 대기 중인 계획은 `docs/구성.md`에서 관리한다.
- 완료된 작업 이력과 현재 구현 상태는 `docs/진행상황.md`에서 관리한다.

## 문서 정보
- 문서명: `endtask.md`
- 현재 버전: `v2.8`
- 역할: `목표/성능/기능 참고용`
- 최종 수정: 2026-06-08 03:20 (XGBoost와 ST-GCN 병렬 동시 구동 및 가중치 융합 모델 최종 반영, 조건부 프리필터 가동 정책 공식 폐기)

## 1. 프로젝트 목표

### 1.1 최종 목표
독거노인을 24시간 촬영하며, 행동과 생활 패턴을 분석하여 `정상`, `이상`, `위험` 3레벨로 상태를 구분하고, 이상 또는 위험 행동 발생 시 보호자 웹으로 알람을 전송하는 것이 목표다.

### 1.2 최종적으로 가능해야 하는 결과
- 이상 행동 발생 시 보호자 웹에 알람이 도착해야 한다.
- 위험 행동 발생 시 해당 시점 전후의 `위험 영상 3분`을 웹에서 바로 확인할 수 있어야 한다.
- 24시간 동안의 활동 내역을 세부 타임라인으로 확인할 수 있어야 한다.
- 1~2주 단위의 활동 패턴 분석 결과를 제공할 수 있어야 한다.
- 분석 결과를 기반으로 건강 케어를 위한 조언을 제공할 수 있어야 한다.

### 1.3 상태 레벨 정의
- `정상`: 일반적인 일상 행동과 생활 흐름
- `이상`: 평소와 다른 활동 패턴 또는 주의가 필요한 상태
- `위험`: 즉시 확인이 필요하거나 사고 가능성이 높은 상태

## 2. 성능 및 품질 요구

### 2.1 정확도 관련 요구
- 점진적으로 넘어지는 상황은 일반 낙상보다 판별이 어렵기 때문에, 이를 정확히 구분할 수 있도록 추가 학습 또는 별도 feature 설계가 가능해야 한다.
- 실제 위험이 아닌 운동, 스트레칭, 빠른 움직임을 위험으로 오판하지 않도록 해야 한다.
- 강한 움직임과 위험 움직임을 분리할 수 있어야 한다.
- 순간적인 오탐을 줄이기 위해 여러 프레임의 시간 흐름을 반영할 수 있어야 한다.
- 위험 후보 발생 이후에는 상태 기반으로 `의심`, `위험`, `확정`, `해제` 단계를 관리할 수 있어야 한다.

### 2.2 촬영 환경 대응 요구
- 천장 각도에서 발생하는 가려짐을 고려할 수 있어야 한다.
- 어두운 환경에서도 분석 성능을 유지할 수 있도록 보정이 가능해야 한다.
- 몸의 일부 가려짐, 이불 등의 장애물로 인한 가려짐 상황을 고려할 수 있어야 한다.
- OpenCV 기반의 카메라 환경 보정, 전처리, ROI 전략을 적용할 수 있어야 한다.

### 2.3 행동 분류 품질 요구
- 단순히 `서있음`, `앉음`, `누움`처럼 하나의 상태만으로 분류하지 않고, 필요 시 여러 상태를 결합한 복합 행동 분류로 확장할 수 있어야 한다.
- 24시간 생활 패턴 분석을 위해 정상 행동도 `NORMAL` 단일 라벨로만 저장하지 않는다.
- 정상 행동은 최소한 `standing`, `walking`, `sitting`, `sitting_down`, `standing_up`, `lying_rest`, `no_move_short`처럼 세분화되어야 한다.
- 이상/위험/낙상 판단은 정상 행동 흐름에서 벗어나는 전이와 지속 시간을 함께 사용해야 한다.
- 예:
  - `식사중 = 앉음 + 손 행동 + 식탁 영역`
  - `걷는중 = 서있음 + 전체 좌표 이동`

### 2.4 상황 인지 요구
- 외부 외출, 화장실 등 카메라가 없는 공간으로의 이동을 인식하고 현재 상태 판단에 반영할 수 있어야 한다.
- 장시간 움직임 없음과 같은 위험 상황을 별도 시나리오로 분리하고 추적할 수 있어야 한다.

### 2.5 제약 사항 고려
- 설치가 어려운 공간이나 촬영이 불가능한 공간에 대한 대책이 필요하다.
- 현재 단계에서는 구현 대상은 아니지만, 구조 설계 수준에서는 고려가 필요하다.

## 3. 반드시 되어야 하는 기능 (내가 맡은 부분)

### 3.1 실시간 고화질 영상 전송
- 프론트엔드에서 실시간으로 카메라 화면을 볼 수 있어야 한다.
- 가능한 한 높은 화질을 유지해야 한다.
- 실제 서비스에서는 지연과 화질 사이 균형이 필요하다.
- 보호자/운영자 화면에는 raw 영상만이 아니라 YOLO bbox, skeleton 또는 keypoint 상태, 현재 행동 라벨, 위험 상태, FPS/시간 정보가 함께 표시될 수 있어야 한다.
- 행동 라벨은 Pi5 단독 추정값이 아니라 Orin에서 XGBoost Branch(Static/Posture Classifier, Risk Tier Classifier) 및 ST-GCN Temporal Branch(Shared Backbone 기반 Activity Head + Risk Head + optional Fall Head) 병렬 멀티태스크/멀티헤드 융합 추론 결과와 FSM 상태를 결합해 판정한 결과를 반영할 수 있어야 한다.
- 원본 확인용 raw stream과 분석 확인용 overlay stream은 필요 시 분리해 제공할 수 있어야 한다.
- 최종 보호자 화면은 WebRTC 기반 overlay 화면을 목표로 하며, raw stream은 장애 대응 또는 원본 확인용 fallback으로 유지한다.
- Pi5에서 raw 또는 encoded 영상을 Orin으로 relay하고, Orin이 행동 판별 결과를 overlay한 뒤 WebRTC로 송출하는 구조도 성능 검증 대상에 포함한다.

### 3.2 행동값 및 행동결과 전송
- YOLO 기반 행동 분석 결과를 추출해야 한다.
- **모든 행동 결과(정상, 의심, 위험)를 빠짐없이** `JSON/JSONB` 형태로 DB와 2차 AI로 전송해야 한다.
- 위험 구간만 보내는 것이 아니라, 정상 행동 데이터도 축적해서 2차 AI가 노인의 **생활 패턴을 분석**할 수 있도록 해야 한다.
- 전송되는 데이터는 추후 타임라인 생성, 위험 상황 조회, 1~2주 단위 패턴 분석에 재사용할 수 있어야 한다.
- Pi5는 실시간 skeleton/action feature를 WebSocket으로 Orin에 전송하고, Orin은 이를 activity/timeline/event 데이터로 저장하거나 서버로 전송해야 한다.
- 최종 운영 기준에서는 `NORMAL`, `ABNORMAL`, `DANGER` 모두 누락 없이 저장되어야 하며, `ABNORMAL/DANGER`는 즉시 또는 단기 batch, `NORMAL`은 장기 패턴 분석용 batch 전송을 허용한다.
- **[2026-05-09 회의 추가]** 전송 데이터에 `capture_ts`(영상 촬영 시각)와 `analysis_ts`(AI 분류 시각)를 반드시 분리하여 포함해야 한다.
  - 2차 AI의 1일/2주 행동 분석 시 정확한 촬영 시각 기준으로 타임라인을 구성할 수 있어야 한다.
  - 처리 지연이 발생해도 실제 사건 시각 기준으로 DB에 저장할 수 있어야 한다.

### 3.3 24시간 행동 분석 및 skeleton 기반 행동 분류
- 사람의 행동을 24시간 분석할 수 있어야 한다.
- skeleton 또는 keypoint를 추출할 수 있어야 한다.
- 현재 사람이 무슨 행동을 하고 있는지 분류할 수 있어야 한다.
- 행동 타임라인을 만들 수 있도록 JSONL, SQLite, 서버 DB 중 하나 이상의 구조화된 저장소에 저장해야 한다.
- 저장된 결과는 서버 DB와 2차 AI가 재사용할 수 있어야 한다.
- Orin은 Pi5 skeleton stream을 수신한 뒤, 모든 유효 윈도우에 대해 ST-GCN activity 추론을 실행하고, XGBoost Branch 및 ST-GCN Branch 병렬 추론 결과를 융합(Fusion) 및 FSM을 거쳐 최종 `activity_label`과 `risk_label`, `event_state`를 분리하여 activity/timeline/event 구조로 변환해야 한다.

### 3.3.1 생활 패턴 분석용 행동 라벨 목표

정상 생활 분석과 위험 알림은 같은 라벨 체계 안에서 연결되어야 한다. 따라서 서버/2차 AI로 넘어가는 `activity/timeline/event` 데이터는 `NORMAL`, `ABNORMAL`, `DANGER` 레벨만이 아니라 세부 행동 라벨도 함께 가져야 한다.

| 계층 | 목표 라벨 | 사용 목적 |
|------|-----------|-----------|
| 정상 일상 | `standing`, `walking`, `sitting`, `sitting_down`, `standing_up`, `lying_rest`, `no_move_short` | 24시간 타임라인, 하루/주간 생활 패턴 분석 |
| 이상/주의 | `no_move_long`, `bending_long`, `lying_on_floor_uncertain`, `out_of_frame_abnormal`, `unstable_sit_to_stand` | 평소와 다른 패턴 감지, 보호자 확인 후보 |
| 위험 전조 | `near_fall`, `stumble`, `loss_of_balance`, `sudden_drop`, `floor_prone_candidate` | 낙상 전조/자가회복/비틀거림 감지 |
| 낙상 | `fall_candidate`, `fall_confirmed`, `fall_then_no_move`, `collapse_out_of_frame` | 즉시 알림, 위험 clip 요청, 보호자 확인 |

운영 기준:
- `NORMAL 단일 라벨로만 저장하지 않는다`.
- 위험 알림은 세부 행동 라벨과 `NORMAL/ABNORMAL/DANGER` 레벨을 함께 사용한다.
- 라벨 재학습 전까지는 기존 모델 출력과 fallback 라벨을 그대로 유지하되, 신규 라벨 학습/검증 계획은 `docs/구성.md`에서 승인받은 뒤 적용한다.

### 3.4 위험 상황 발생 시 영상 저장 및 전송
- 엣지(Jetson Orin Nano)가 위험 상황으로 분류하면, 해당 타임라인 시점과 연결되는 위험 상황 당시의 영상 `3분`을 확보할 수 있어야 한다.
- 이 영상은 Orin을 거쳐 개인정보 보호 처리를 확인한 뒤 서버 DB 또는 서버 저장소에 업로드되어야 한다.
- 웹에서 팝업 또는 알림과 함께 바로 조회할 수 있어야 한다.
- 개인정보 보호를 위해 위험 clip이 외부 백엔드/미디어 서버로 나가기 전 얼굴/머리 영역 blur 처리가 적용 또는 검증되어야 한다.
- **[2026-05-09 회의 변경]** 위험 영상 clip 저장 방식:
  - 기존: 10초 단위 세그먼트 로컬 파일 저장 → 위험 시 3분 복원
  - 현재 기준: 위험 시점 전후 기본 `3분`을 확보하고, 장시간 미활동/누움 같은 특수 상황에서도 `10분`을 넘기지 않는다.
  - 구현 방식은 장비(Raspberry Pi 5 / Jetson Orin Nano)의 RAM/저장 용량과 실기기 FPS를 기준으로 `segment_ring` 또는 링 버퍼 계열로 조정한다.
- **[2026-05-29 변경]** 위험 clip은 Pi5에서 `segment_ring` 방식으로 임시 보관하고, Orin이 위험 timestamp를 기준으로 REST 요청을 보내면 Pi5가 해당 구간을 export해 Orin으로 전송한다. 운영 기준에서 Pi5는 외부 백엔드/미디어 서버로 직접 위험 clip을 업로드하지 않고, Orin이 face blur 적용 또는 검증 후 서버 업로드를 담당한다.

## 3.5 기기 구성 및 통신 방식 (2026-05-27 최신 기준)

### 3.5.1 최종 기기 구성

```
카메라1 (Raspberry Pi 5 + wide v3 카메라)
  - Picamera2/libcamera 기반 영상 캡처
  - YOLO pose ONNX 또는 PT skeleton 추출
  - skeleton, bbox, 행동 속도, motion feature JSON WebSocket 전송
  - 필요 시 raw 또는 encoded video stream을 Orin으로 relay
  - 위험 timestamp REST 요청 수신 후 clip export
  - 위험 clip을 Orin으로 REST 전송
  - MediaMTX + H.264 RTSP/WebRTC raw fallback 또는 overlay 스트리밍 대상
        ↕ WebSocket + REST
엣지 (Jetson Orin Nano)
  - FastAPI Edge Hub
  - Pi5 skeleton WebSocket 수신
  - 필요 시 Pi5 영상 relay 수신
  - XGBoost + ST-GCN 병렬 추론 및 가중치 융합 위험 분석
  - TensorRT FP16 최적화 대상
  - 위험 timestamp 기준 Pi5에 clip 전송 요청
  - 분석 결과를 Pi5, 프론트엔드 또는 Orin overlay stream 경로로 피드백
  - bbox/action/risk overlay WebRTC 송출 대상
  - 위험 clip face blur 적용 또는 검증 후 서버 업로드
  - activity/timeline/event/alert/clip metadata를 서버/미디어 서버 DB로 전송
        ↕ WebSocket + REST
서버
  - 백엔드 API
  - 미디어 서버/DB
  - 2차 분류 AI
  - 프론트엔드
```

### 3.5.2 각 기기 역할 및 담당 AI

| 기기 | 역할 | 담당 AI/기능 |
|------|------|--------------|
| 카메라1 (Raspberry Pi 5 + wide v3 카메라) | Picamera2/libcamera 캡처, YOLO pose 추출, skeleton/행동속도 JSON WebSocket 전송, 필요 시 raw/encoded 영상 relay, Orin 요청 시 위험 clip REST 전송, RTSP/WebRTC raw fallback 또는 overlay 스트리밍 대상 | 경량 YOLO pose ONNX 또는 PT (CPU, 추론 입력 640×360) |
| 엣지 (Jetson Orin Nano) | Pi5 skeleton/영상 relay 수신, XGBoost/ST-GCN 위험 분석, TensorRT FP16 최적화 대상, 위험 timestamp 기준 clip 요청, overlay JSON WebSocket 또는 overlay WebRTC 송출 대상, face blur 적용/검증, presigned URL로 S3 clip 업로드, activity/timeline/event/alert/clip metadata 서버 전송 | XGBoost Branch(Static/Posture, Risk Tier) + ST-GCN Temporal Branch(Shared Backbone 기반 Activity Head, Risk Head, optional Fall Head) 병렬 멀티태스크/멀티헤드 추론 (모든 유효 윈도우 대상 ST-GCN activity 추론 실행) 및 융합(Fusion) + TensorRT FP16 최적화 |
| 서버 (EC2) | Flask 단일 app.py 백엔드 API, WebSocket endpoint 없음, SSE `/api/v1/alerts/stream`으로 실시간 알림, `POST /api/v1/clips/upload-url`로 presigned PUT URL 발급, `POST /api/v1/clips/confirm`으로 S3 clip 확정 | 생활 패턴/2차 분류 AI, PostgreSQL DB |
| 프론트엔드 | Vite + React + TypeScript strict + Tailwind + shadcn/ui | 보호자 화면, 타임라인, 알림, 위험 clip 조회, overlay canvas |

### 3.5.3 Pi5 → Orin 부하 이전 근거

Pi5(ARM64 CPU only)는 YOLO ONNX 추론만 해도 CPU 부하가 높다. 아래 항목을 Orin으로 이전하여 Pi5는 최소 부하만 유지한다.

| 이전 항목 | Pi5 (기존) | Orin으로 이전 후 |
|---------|-----------|----------------|
| XGBoost 행동 분류 (action_classifier) | Pi5 실행 | Orin에서 skeleton JSON 수신 후 실행 |
| XGBoost & ST-GCN 병렬 추론 | Pi5 실행 | Orin에서 실행 (XGBoost Branch와 ST-GCN Temporal Branch 동시 병렬 구동. 모든 유효 윈도우 대상 ST-GCN activity 추론 실행) |
| TriggerEngine (물리 위험 임계치 감지) | Pi5 실행 | Orin에서 실행 |
| ST-GCN (정밀 판정) | Orin 실행 | 유지 (stgcn_activity는 모든 유효 윈도우에서 실행하며, stgcn_risk 및 fall_head는 조건부 또는 병렬로 상시 구동하여 융합) |
| CandidateSender (후보 전송) | Pi5가 HTTP로 전송 | Pi5는 skeleton JSON만 WebSocket으로 전송 |

Pi5 최종 역할:
- wide 카메라 v3 캡처(Picamera2/libcamera 우선, v4l2는 USB/UVC fallback)
- 경량 YOLO pose ONNX CPU 추론 (skeleton/bbox 추출)
- WebSocket으로 Orin에 skeleton JSON 실시간 전송
- 필요 시 H.264 raw/encoded 영상 stream을 Orin으로 relay
- 위험 확정 시 Orin 요청을 받아 REST로 clip 전송
- segment_ring 기반 clip 로컬 임시 보관
- H.264 RTSP/WebRTC raw fallback 또는 overlay 송출 대상 생성

Orin 최종 역할:
- Pi5 skeleton JSON WebSocket 수신
- XGBoost Branch (Static/Posture Classifier 및 Risk Tier Classifier) 추론
- ST-GCN Temporal Branch (Shared Backbone 기반 Activity Head 및 Risk Head, optional Fall Head) 추론
- 모든 유효 윈도우에 대해 ST-GCN activity 추론 실행 (위험 후보에만 국한하지 않고 일상세부추론 상시 가동)
- TriggerEngine/FSM/Fusion 엔진 기반 최종 activity_label, risk_label, event_state 판정 및 clip/alert trigger 결정
- Orin 실기기에서는 ST-GCN TensorRT FP16 최적화를 최종 목표로 한다. 단, PyTorch/ONNXRuntime fallback은 유지한다.
- clip 요청 시 Pi5에 REST 요청 → 수신 후 백엔드 업로드
- Pi5에서 받은 위험 clip을 외부 업로드 전 face blur 처리 또는 blur 적용 여부 검증
- Orin 분석 결과를 Pi5 overlay, 프론트엔드 overlay 또는 Orin 자체 overlay stream 경로로 전달
- Pi5 영상 relay를 받을 경우 bbox/action/risk가 표시된 overlay WebRTC stream을 송출한다.
- 분석 결과를 WebSocket/REST로 서버 또는 미디어 서버 DB에 전송한다.
- Pi5 skeleton stream을 activity/timeline/event 저장 구조로 변환한다.
- REST API로 백엔드에 분석 결과 전송 (ABNORMAL/DANGER 우선, NORMAL은 후속 배치 확장)

### 3.5.4 통신 방식

| 구간 | 방식 | 데이터 |
|------|------|--------|
| Pi5 → Orin (실시간) | WebSocket | skeleton JSON, keypoints, bbox, 행동 속도/feature, capture_ts, sequence_id |
| Pi5 → Orin (선택 영상 relay) | RTSP/WebRTC/H.264 stream | raw 또는 encoded video frame. Orin overlay 송출 구조 선택 시 사용 |
| Orin → Pi5 (clip 요청) | REST (HTTP POST) | 위험 timestamp 기준 clip_start_ms, clip_end_ms |
| Pi5 → Orin (clip 전송) | REST (HTTP POST / multipart) | 위험 clip 영상 파일. 운영 기준에서 외부 업로드 전 Orin이 face blur 적용 또는 검증 |
| Orin → 서버 (분석 결과) | REST (HTTP POST) | activity/timeline/risk event, events/batch, alerts/immediate |
| Orin → 서버 (clip 업로드) | REST presigned URL | `POST /api/v1/clips/upload-url` → S3 PUT `video/mp4` → `POST /api/v1/clips/confirm` |
| Orin → 프론트엔드 (overlay 결과) | WebSocket (1차 AI 별도 경로) | action_label, risk_label, event_state, risk_score, track_id, sequence_id. 백엔드 경유 불가 (WebSocket endpoint 없음) |
| Orin → 프론트엔드/미디어 서버 (overlay stream) | WebRTC/RTSP | bbox, skeleton/keypoint 상태, action_label, risk_label, FPS/time이 합성된 영상 |
| 서버 → 프론트엔드 (알림) | SSE `/api/v1/alerts/stream` | 실시간 위험 알림. WebSocket 아님 |
| 서버 → 프론트엔드 (데이터) | REST | 타임라인, 위험 clip 조회 |

### 3.5.5 RTSP/WebRTC 스트리밍

- 카메라1(Pi5)은 MediaMTX 기반 RTSP/WebRTC 경로로 프론트엔드 또는 미디어 서버에 실시간 화면을 제공한다.
- 실사용 보호자 화면은 raw 영상만이 아니라 bbox, skeleton/keypoint 상태, Orin 행동 분류 결과, 위험 상태를 함께 볼 수 있는 overlay 표시를 목표로 한다.
- overlay 표시 방식은 프론트엔드 JSON overlay, Pi5 local overlay, Orin relay overlay 중 실기기 FPS/지연 검증 결과에 따라 선택한다.
- Pi5→Orin 영상 relay 방식은 Orin이 행동 판별과 overlay 송출을 함께 처리하는 후보 구조이며, Pi5 카메라 점유 충돌과 Pi5 FPS 부족을 해결할 수 있는지 검증 대상이다.
- 최신 기본 해상도는 Pi5 YOLO pose 기반 행동 분석과 실기기 성능 테스트 기준 `640×360`이다.
- 이 기준은 이전 영상/라벨 구간 추론 및 해상도별 벤치마크에서 skeleton 검출 품질을 유지하면서 FPS가 가장 현실적으로 나온 값이다.
- `1280×720` 이상은 행동 분석 기본값이 아니라 프론트 실시간 화면 품질 또는 고해상도 스트리밍 단독 확인용 선택 해상도로만 사용한다.
- 송출 코덱은 H.264 기준으로 작성한다.
- MediaMTX는 기본 RTSP 포트 `8554`를 사용한다.
- WebRTC는 MediaMTX 설정의 WebRTC HTTP/UDP 포트를 사용한다.
- Pi Camera wide v3는 `Picamera2/libcamera` 계열 입력을 우선 사용하고, `/dev/video*` 직접 입력은 USB/UVC 카메라 fallback으로만 사용한다.

MediaMTX 실행 기준:

```bash
./mediamtx
```

Pi Camera wide v3 H.264 송출 기준 명령:

```bash
rpicam-vid -t 0 -n \
  --width 640 \
  --height 360 \
  --framerate 15 \
  --codec libav \
  --libav-format mpegts \
  --low-latency \
  --bitrate 2000000 \
  --autofocus-mode continuous \
  -o - | \
gst-launch-1.0 -e -v \
  fdsrc fd=0 ! \
  tsparse ! \
  tsdemux ! \
  h264parse config-interval=-1 ! \
  'video/x-h264,stream-format=byte-stream,alignment=au' ! \
  rtspclientsink protocols=tcp location=rtsp://<MEDIA_SERVER_IP>:8554/raspi_cam01
```

USB/UVC 카메라 fallback 송출 기준 명령:

```bash
ffmpeg -f v4l2 -framerate 15 -video_size 640x360 -i /dev/video0 \
  -an -c:v libx264 -pix_fmt yuv420p -preset ultrafast -tune zerolatency \
  -profile:v baseline -g 30 -b:v 2000k \
  -f rtsp rtsp://127.0.0.1:8554/orin_cam02
```

프론트엔드/미디어 서버 수신 주소 예시:

```text
rtsp://<PI5_IP>:8554/raspi_cam01
rtsp://<ORIN_IP>:8554/orin_cam02
```

WebRTC 수신 주소 예시:

```text
http://<MEDIA_SERVER_IP>:8889/raspi_cam01
```

## 3.6 AI 모델 구동 위치 (2026-05-27 최신 기준)

| 장비 | AI 모델 | 역할 |
|------|---------|------|
| 카메라1 (Raspberry Pi 5 + wide v3 카메라) | 경량 YOLO pose ONNX 또는 PT (CPU) | skeleton/keypoint/bbox 추출, 행동 속도 등 feature JSON 생성, WebSocket 전송 |
| 엣지 (Jetson Orin Nano) | XGBoost Branch(Static/Posture, Risk Tier) + ST-GCN Temporal Branch(Shared Backbone 기반 Activity Head, Risk Head, optional Fall Head) 병렬 멀티태스크/멀티헤드 추론 (모든 유효 윈도우 대상 ST-GCN activity 추론 실행) 및 융합(Fusion) + TensorRT FP16 최적화 | Pi5 skeleton 수신 → 위험 분석 → 위험 timestamp 산정 → Pi5 clip 요청 → face blur 적용/검증 → 서버/미디어 서버 DB 전송 |
| 서버 | 2차 분류 AI, DB, 미디어 서버, 프론트엔드 | 분석 결과 저장, clip 저장/조회, 보호자 알림, 장기 패턴 분석, 프론트엔드 제공 |

운영 기준:
- Pi5는 카메라 캡처 + YOLO pose 추론 + skeleton/행동속도 JSON WebSocket 전송 + Orin 요청 기반 위험 clip REST 전송 + H.264 스트리밍 경로를 담당한다.
- Pi5는 성능 부족 또는 overlay 동기화 필요 시 raw/encoded 영상을 Orin으로 relay할 수 있어야 한다.
- Pi5는 운영 환경에서 외부 백엔드/미디어 서버로 위험 clip을 직접 업로드하지 않는다.
- XGBoost/ST-GCN 병렬 멀티태스크 멀티헤드 융합 및 TensorRT FP16 최적화는 Orin에서 담당한다.
- Orin은 Pi5 skeleton을 받아 모든 유효 윈도우에 대해 ST-GCN activity 추론을 실행하고, XGBoost Branch 및 ST-GCN Branch의 병렬 추론 결과를 융합(Fusion) 및 FSM을 거쳐 최종 `activity_label`, `risk_label`, `event_state`를 판정한다. 이후 위험 timestamp 기준 clip 요청 → face blur 적용 또는 검증 → 서버/미디어 서버 DB 전송을 담당한다.
- Orin은 행동 분류 결과를 Pi5 또는 프론트엔드 overlay 경로로 되돌려 실시간 화면에 표시할 수 있어야 한다.
- Orin은 선택 구조에서 Pi5 영상 relay를 받아 bbox/action/risk가 합성된 overlay WebRTC stream을 생성할 수 있어야 한다.
- 서버는 백엔드, 미디어 서버, DB, 2차 분류 AI, 프론트엔드를 포함하는 최종 서비스 계층이다.
- 실기기 테스트에서 Pi5 FPS가 부족하면 해상도 추가 하향 또는 Hailo 보조 가속을 재검토한다.

## 3.7 현재 실장비 목표 구성 (2026-05-27 최신 기준)

| 역할 | 장비/계층 | 담당 기능 | 비고 |
|------|-----------|-----------|------|
| 카메라1 | Raspberry Pi 5 + wide v3 카메라 | Picamera2/libcamera 캡처, YOLO pose skeleton 추출, 행동속도/feature JSON 생성, WebSocket 전송, 필요 시 영상 relay, RTSP/WebRTC raw fallback 또는 overlay 송출 대상, Orin 요청 시 위험 clip REST 전송 | XGBoost/ST-GCN/Trigger/FSM/Fusion 제외, 외부 clip 직접 업로드 금지 |
| 엣지 분석 허브 | Jetson Orin Nano | FastAPI Edge Hub, XGBoost Branch 및 ST-GCN Temporal Branch 병렬 멀티헤드/멀티캐스터 추론 (모든 유효 윈도우 대상 ST-GCN activity 추론 실행), FSM/Fusion 엔진, TensorRT FP16, 위험 timestamp 산정, Pi5 clip 요청, face blur 적용/검증, overlay 결과 피드백 또는 overlay WebRTC stream 생성, 분석 결과 서버 전송 | Pi5 skeleton JSON 수신 후 전 AI 처리. 선택적으로 Pi5 video relay 수신 |
| Pi5 → Orin 실시간 전송 | LAN WebSocket | skeleton JSON, keypoints, bbox, 행동속도/feature, capture_ts | 최종 확정 |
| Pi5 → Orin 영상 relay | LAN RTSP/WebRTC/H.264 | raw 또는 encoded 영상. Orin overlay 구조 선택 시 사용 | 성능 검증 후 적용 |
| Orin → Pi5 clip 요청 | LAN REST | 위험 timestamp 기준 clip_start_ms, clip_end_ms 요청 | 위험 확정 시 Orin이 Pi5에 요청 |
| Pi5 → Orin clip 전송 | LAN REST | 위험 clip 영상. Orin이 외부 전송 전 face blur 적용 또는 검증 | multipart/form-data 또는 binary |
| Orin → 프론트엔드 overlay stream | WebRTC/RTSP | bbox, skeleton/keypoint, action_label, risk_label, FPS/time 표시 영상 | 최종 보호자 화면 후보 |
| Orin → 서버 결과 전송 | WebSocket + REST | 분석 결과, 이벤트, 알림, clip metadata | 미디어 서버 DB/백엔드 저장 |
| 서버 | 백엔드 + 미디어 서버 + DB + 2차 분류 AI + 프론트엔드 | events/batch, alerts/immediate, clip 저장/조회, 보호자 웹 알림, 장기 패턴 분석 | 최종 서비스 계층 |
