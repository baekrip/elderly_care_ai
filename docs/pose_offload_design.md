# Orin Pose Offload 설계

## 목적

Pi5 카메라/RTSP 30 FPS를 유지하면서 pose 분석 FPS 한계를 줄일 수 있는지 검증하기 위한 설계 문서다. 실제 운영 구조 변경은 benchmark가 `capture_ts -> risk_label <= 500ms` 예산과 정확도 gate를 통과한 뒤에만 진행한다.

## 후보 구조

1. Pi5 local pose 유지
   - Pi5는 Picamera2 캡처, YOLO pose, skeleton WebSocket 전송을 계속 담당한다.
   - 장점: 현재 구조와 가장 가깝다.
   - 단점: Pi5 CPU pose FPS가 약 5 FPS 수준으로 제한될 수 있다.

2. Pi5 캡처 + Orin pose offload
   - Pi5는 frame 또는 encoded frame을 Orin으로 전송하고, Orin이 pose/action/risk/ST-GCN을 모두 수행한다.
   - 장점: Pi5 부하를 줄이고 Orin 가속 여지를 활용할 수 있다.
   - 단점: 네트워크 지연, frame 전송량, overlay 동기화 리스크가 있다.

3. Pi5 경량 pose + Orin 정밀 판정
   - Pi5는 더 작은 pose 모델로 skeleton만 만들고, Orin은 현재처럼 action/risk/ST-GCN을 담당한다.
   - 장점: 구조 변경이 작고 지연 예산 관리가 쉽다.
   - 단점: 경량 모델 정확도 저하 가능성이 있다.

## 측정 항목

| 항목 | 기준 |
|------|------|
| end-to-end latency | `capture_ts -> risk_label <= 500ms` |
| DANGER recall | `>= 0.90` |
| 정상 ADL FP/hour | `<= 2.0` |
| visible_joint_ratio | 기존 Pi5 baseline 대비 급락 금지 |
| skeleton_confidence_mean | 기존 Pi5 baseline 대비 급락 금지 |
| Pi5 RTSP FPS | `>= 30 FPS` 유지 |

## 적용 금지 조건

- DANGER recall이 `0.90` 미만이면 운영 적용 금지.
- 정상 ADL FP/hour가 `2.0` 초과면 운영 적용 금지.
- end-to-end latency가 500ms를 넘으면 FPS가 개선되어도 운영 적용 금지.
- Pi5 RTSP 30 FPS가 깨지면 운영 적용 금지.

## 현재 결론

2026-06-01 기준 이 문서는 설계/검증 기준만 확정한다. 실제 Orin pose offload 구조 변경, 모델 교체, 장시간 실기기 실행은 사용자 승인 후 진행한다.
