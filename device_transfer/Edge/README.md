# Edge bundle

Jetson Orin Nano 엣지 허브로 옮기는 실행 묶음이다.

## 포함 역할

- Pi5 skeleton WebSocket 수신 서버
- XGBoost + TriggerEngine + ST-GCN 위험 분석
- 위험 timestamp 기준 Pi5 clip 요청
- clip/metadata 서버 저장 및 외부 백엔드 event/alert 전송

## 기본 실행

터미널 1:

```bash
cd ~/elderly_care_ai
source .venv_edge/bin/activate
python -m server.main --config server/config.orin.yaml --host 0.0.0.0 --port 8000
```

터미널 2:

```bash
cd ~/elderly_care_ai
source .venv_edge/bin/activate
python -m edge.main --config edge/config.orin_cam02.yaml --max-frames 300
```

실행 전 `edge/config.orin_cam02.yaml`의 `ORIN_IP`, `camera.source`와 `server/config.orin.yaml`의 backend 설정을 확인한다.
