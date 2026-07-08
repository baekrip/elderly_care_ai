@echo off
REM ===========================================
REM Server (Windows 노트북) 환경 설정 스크립트
REM 실행: setup_server.bat
REM ===========================================

echo ==========================================
echo  [Server] Windows 노트북 환경 설정 시작
echo ==========================================

REM --- 1. Python 가상환경 생성 ---
echo [1/4] Python 가상환경 생성...
python -m venv server_env
call server_env\Scripts\activate.bat

REM --- 2. Python 패키지 설치 ---
echo [2/4] Python 패키지 설치...
pip install --upgrade pip
pip install -r requirements_server.txt

REM --- 3. 디렉토리 생성 ---
echo [3/4] 필요 디렉토리 생성...
mkdir models 2>nul
mkdir storage\videos 2>nul
mkdir output\features 2>nul

REM --- 4. 안내 ---
echo [4/4] 추가 설정 안내...
echo.
echo  [필수] PostgreSQL 설치 및 DB 생성:
echo    1. https://www.postgresql.org/download/windows/ 에서 설치
echo    2. psql 에서 실행:
echo       CREATE USER elderly_admin WITH PASSWORD 'your_password';
echo       CREATE DATABASE elderly_care OWNER elderly_admin;
echo.
echo  [필수] RTMPose 모델 다운로드:
echo    server/models/ 에 rtmpose-s ONNX 파일 배치
echo.
echo  [선택] GPU 없는 경우:
echo    pip uninstall onnxruntime-gpu
echo    pip install onnxruntime
echo.
echo ==========================================
echo  [Server] 설정 완료!
echo  실행: server_env\Scripts\activate
echo        python -m server.main --config server/config.yaml
echo ==========================================
pause
