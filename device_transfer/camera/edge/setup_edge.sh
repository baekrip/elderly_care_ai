#!/bin/bash
# ===========================================
# Edge (라즈베리파이 5) 환경 설정 스크립트
# 실행: chmod +x setup_edge.sh && ./setup_edge.sh
# ===========================================

echo "=========================================="
echo " [Edge] 라즈베리파이 5 환경 설정 시작"
echo "=========================================="

# --- 1. 시스템 업데이트 ---
echo "[1/6] 시스템 업데이트..."
sudo apt update && sudo apt upgrade -y

# --- 2. 카메라 + FFmpeg 설치 ---
echo "[2/6] 카메라 + FFmpeg 패키지 설치..."
sudo apt install -y python3-picamera2 python3-libcamera ffmpeg

# --- 3. Python 가상환경 생성 ---
echo "[3/6] Python 가상환경 생성..."
python3 -m venv ~/edge_env --system-site-packages
source ~/edge_env/bin/activate

# --- 4. Python 패키지 설치 ---
echo "[4/6] Python 패키지 설치..."
pip install --upgrade pip
pip install -r requirements_edge.txt

# --- 5. MediaMTX (RTSP 서버) 설치 ---
echo "[5/6] MediaMTX RTSP 서버 설치..."
MEDIAMTX_VERSION="v1.9.0"
wget -q https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_arm64v8.tar.gz -O /tmp/mediamtx.tar.gz
tar -xzf /tmp/mediamtx.tar.gz -C /tmp/
sudo mv /tmp/mediamtx /usr/local/bin/
sudo mv /tmp/mediamtx.yml /usr/local/etc/
rm /tmp/mediamtx.tar.gz

# --- 6. YOLO 모델 준비 (models 디렉토리) ---
echo "[6/6] models 디렉토리 생성..."
mkdir -p models
echo "  → yolov8n.onnx 파일을 edge/models/ 에 복사해주세요"
echo "  → 변환 명령: python -c \"from ultralytics import YOLO; YOLO('yolov8n.pt').export(format='onnx', imgsz=640)\""

echo ""
echo "=========================================="
echo " [Edge] 설정 완료!"
echo " 실행: source ~/edge_env/bin/activate"
echo "       python -m edge.main --config edge/config.yaml"
echo "=========================================="
