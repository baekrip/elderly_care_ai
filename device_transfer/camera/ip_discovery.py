import socket
import threading
import time
UDP_PORT = 9999
HOSTS_PATH = "/etc/hosts"
def broadcast_identity(hostname):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            # 현재 기기의 로컬 IP 획득
            current_ip = socket.gethostbyname(socket.gethostname())
            message = f"ELDERLY_CARE:{hostname}:{current_ip}"
            sock.sendto(message.encode(), ('<broadcast>', UDP_PORT))
        except Exception:
            pass
        time.sleep(15)
def listen_and_update():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', UDP_PORT))
    while True:
        data, addr = sock.recvfrom(1024)
        msg = data.decode()
        if msg.startswith("ELDERLY_CARE:"):
            _, target_host, target_ip = msg.split(":")
            update_hosts_file(target_host, target_ip)
def update_hosts_file(host, ip):
    # /etc/hosts를 읽어서 target_host 주소를 ip로 안전하게 치환하는 로직 (sudo 권한 필요)
    pass