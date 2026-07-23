#!/bin/bash
# connect_wifi.sh - NetworkManager Wi-Fi 연결 자동화 헬퍼 스크립트
# 사용법: sudo ./connect_wifi.sh <SSID> <PASSWORD>

if [ "$#" -ne 2 ]; then
    echo "사용법: sudo $0 <SSID> <PASSWORD>"
    exit 1
fi

SSID=$1
PASSWORD=$2

echo "[1/4] Wi-Fi 디바이스 Rescan 진행 중..."
nmcli device wifi rescan
sleep 3

echo "[2/4] 기존 프로필 충돌 방지를 위해 기존 SSID '$SSID' 커넥션 프로필 삭제 중..."
# 중복되거나 비정상적인 기존 커넥션 프로필을 모두 지웁니다.
nmcli connection show | grep -i "$SSID" | while read -r line; do
    # connection 이름 추출 (보통 첫 번째 열)
    CONN_NAME=$(echo "$line" | awk '{print $1}')
    if [ -n "$CONN_NAME" ]; then
        echo "기존 커넥션 프로필 제거: $CONN_NAME"
        nmcli connection delete "$CONN_NAME"
    fi
done

echo "[3/4] 신규 SSID '$SSID'에 연결 시도 중..."
# 802-11-wireless-security.key-mgmt 프로필 누락 방지를 위해 깨끗하게 새로 연결합니다.
nmcli device wifi connect "$SSID" password "$PASSWORD"
CONNECT_STATUS=$?

if [ $CONNECT_STATUS -eq 0 ]; then
    echo "[4/4] Wi-Fi 연결 성공! 네트워크 연동을 위해 에지 서비스를 재시작합니다..."
    # IP 변경을 동적으로 동기화하기 위해 디스커버리 데몬 및 메인 에지 서비스를 리셋합니다.
    systemctl restart elderly-discovery.service
    sleep 2
    if systemctl list-units --full -all | grep -Fq 'elderly-edge-cam01.service'; then
        systemctl restart elderly-edge-cam01.service
    fi
    if systemctl list-units --full -all | grep -Fq 'elderly-orin-server.service'; then
        systemctl restart elderly-orin-server.service
    fi
    echo "서비스 재시작 완료. 정상 통신 상태를 확인하십시오."
else
    echo "[오류] Wi-Fi 연결에 실패했습니다. (Exit Code: $CONNECT_STATUS)"
    exit 2
fi
