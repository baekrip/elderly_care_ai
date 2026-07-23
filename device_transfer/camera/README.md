# camera1 bundle

Raspberry Pi 5 + wide v3 카메라로 옮기는 최소 실행 묶음이다.

## 포함 역할

- YOLO pose ONNX/PT skeleton 추출
- 행동속도/feature JSON WebSocket 전송
- 위험 clip Face blur 후 REST 업로드
- RTSP H.264 송출 command template

## 기본 실행

```bash
cd ~/elderly_care_ai
python3 -m venv .venv_edge --system-site-packages
source .venv_edge/bin/activate
python -m pip install --upgrade pip
python -m pip install -r edge/requirements_edge.txt
python -m edge.main --config edge/config.raspi_cam01.yaml --max-frames 300
```

실행 전 `edge/config.raspi_cam01.yaml`의 `ORIN_IP`, `PI5_IP`, `camera.source`를 실제 값으로 바꾼다.
