#!/bin/bash
# ===========================================
# Server (Linux 클라우드) 환경 설정 스크립트
# 실행: chmod +x setup_server.sh && ./setup_server.sh
# ===========================================

echo "=========================================="
echo " [Server] Linux 클라우드 환경 설정 시작"
echo "=========================================="

# --- 1. 시스템 패키지 ---
echo "[1/5] 시스템 패키지 설치..."
sudo apt update
sudo apt install -y python3-venv ffmpeg

# --- 2. PostgreSQL ---
echo "[2/5] PostgreSQL 설치..."
sudo apt install -y postgresql postgresql-contrib
sudo -u postgres psql -c "CREATE USER elderly_admin WITH PASSWORD 'your_password';" 2>/dev/null
sudo -u postgres psql -c "CREATE DATABASE elderly_care OWNER elderly_admin;" 2>/dev/null

# --- 3. Python 가상환경 ---
echo "[3/5] Python 가상환경 생성..."
python3 -m venv server_env
source server_env/bin/activate

# --- 4. Python 패키지 ---
echo "[4/5] Python 패키지 설치..."
pip install --upgrade pip
pip install -r requirements_server.txt

# --- 5. 디렉토리 ---
echo "[5/5] 디렉토리 생성..."
mkdir -p models storage/videos output/features

echo ""
echo "=========================================="
echo " [Server] 설정 완료!"
echo " [필수] RTMPose 모델을 server/models/ 에 배치"
echo " 실행: source server_env/bin/activate"
echo "       python -m server.main --config server/config.yaml"
echo "=========================================="
