# tools/ip_discovery.py
import socket
import threading
import time
import re
import subprocess

UDP_PORT = 9999
HOSTS_PATH = "/etc/hosts"

# 이 기기의 호스트명 설정 (Pi5는 "pi5cam1", Orin은 "orin")
MY_HOSTNAME = socket.gethostname()  # Pi5에서는 "pi5cam1"으로 변경

def get_local_ip():
    """실제 로컬 IP 획득 (127.0.0.1 제외)"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def broadcast_identity():
    """10~20초 주기로 자신의 IP 브로드캐스트 송출"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    while True:
        try:
            current_ip = get_local_ip()
            message = f"ELDERLY_CARE:{MY_HOSTNAME}:{current_ip}"
            sock.sendto(message.encode(), ('<broadcast>', UDP_PORT))
            print(f"[broadcast] 송출: {message}")
        except Exception as e:
            print(f"[broadcast] 오류: {e}")
        time.sleep(15)

def update_hosts_file(host, new_ip):
    """/etc/hosts에서 해당 호스트 IP를 동적 치환"""
    try:
        with open(HOSTS_PATH, 'r') as f:
            lines = f.readlines()

        pattern = re.compile(
            r'^\s*(\d+\.\d+\.\d+\.\d+)\s+' + re.escape(host) + r'\s*$'
        )
        updated = False
        new_lines = []
        for line in lines:
            if pattern.match(line):
                new_lines.append(f"{new_ip}\t{host}\n")
                updated = True
                print(f"[hosts] {host} → {new_ip} 갱신")
            else:
                new_lines.append(line)

        # 기존에 없던 호스트면 새로 추가
        if not updated:
            new_lines.append(f"{new_ip}\t{host}\n")
            print(f"[hosts] {host} → {new_ip} 신규 추가")

        # 임시파일 경유로 안전하게 저장 (root 권한 필요)
        tmp_path = "/tmp/hosts_tmp"
        with open(tmp_path, 'w') as f:
            f.writelines(new_lines)
        subprocess.run(["sudo", "cp", tmp_path, HOSTS_PATH], check=True)

    except Exception as e:
        print(f"[hosts] 갱신 오류: {e}")

def listen_and_update():
    """상대방 브로드캐스트 수신 후 /etc/hosts 갱신"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', UDP_PORT))
    print(f"[listen] UDP {UDP_PORT} 수신 대기 중...")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            msg = data.decode().strip()
            if msg.startswith("ELDERLY_CARE:"):
                parts = msg.split(":")
                if len(parts) == 3:
                    _, target_host, target_ip = parts
                    # 자기 자신 패킷은 무시
                    if target_host != MY_HOSTNAME:
                        print(f"[listen] 수신: {target_host} = {target_ip}")
                        update_hosts_file(target_host, target_ip)
        except Exception as e:
            print(f"[listen] 오류: {e}")

if __name__ == "__main__":
    t1 = threading.Thread(target=broadcast_identity, daemon=True)
    t2 = threading.Thread(target=listen_and_update, daemon=True)
    t1.start()
    t2.start()
    print(f"[ip_discovery] 시작 (hostname: {MY_HOSTNAME})")
    while True:
        time.sleep(60)
