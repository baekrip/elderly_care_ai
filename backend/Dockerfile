# 운영 서버(EC2 Ubuntu 22.04)의 Python 3.10.12에 맞춤.
# 로컬 개발 머신(Windows)은 3.14.5라 4버전 차이가 나므로, 이 이미지가 그 격차를 덮는다.
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

WORKDIR /app

# 의존성만 먼저 복사 — app.py가 바뀌어도 이 레이어는 캐시에서 재사용된다.
# 이 이미지는 로컬 개발/테스트 환경이므로 dev 의존성(pytest)도 함께 넣는다.
# 서버는 이 이미지를 쓰지 않는다 — venv + systemd로 requirements.txt만 설치.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# 애플리케이션 코드 전체. compose가 개발 중에는 소스를 덮어씌워 마운트하지만,
# 이미지 자체만으로도 뜰 수 있어야 한다 (마운트에 의존하면 이미지가 반쪽이 된다).
COPY app.py .
COPY core/ ./core/
COPY routes/ ./routes/
COPY scheduler/ ./scheduler/

# 컨테이너를 root로 돌리지 않는다.
RUN useradd --create-home --shell /bin/bash capstone \
    && chown -R capstone:capstone /app
USER capstone

EXPOSE 5000

# 운영(systemd ExecStart)과 동일한 gunicorn 옵션.
#   -w 2 --worker-class gthread --threads 8 --timeout 120 --keep-alive 75
# gthread는 원래 SSE long-poll 때문에 도입했고, SSE 제거(b5338ee) 후에도
# 장시간 요청 대비로 유지 중 (CLAUDE.md 아키텍처 결정 사항 참고).
# 바인딩만 0.0.0.0 — 운영은 nginx 뒤라 127.0.0.1이지만, 컨테이너는
# 네트워크 네임스페이스로 격리되므로 0.0.0.0이 외부 노출을 뜻하지 않는다.
# 공개 범위는 compose의 ports 매핑이 결정한다.
CMD ["gunicorn", \
     "-b", "0.0.0.0:5000", \
     "-w", "2", \
     "--worker-class", "gthread", \
     "--threads", "8", \
     "--timeout", "120", \
     "--keep-alive", "75", \
     "--access-logfile", "-", \
     "app:app"]
