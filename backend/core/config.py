"""환경변수와 상수. 다른 모듈이 os.getenv를 직접 부르지 말고 여기서 가져갈 것.

load_dotenv()가 여기 있는 이유: 아래 os.getenv 들보다 반드시 먼저 실행돼야 한다.
이 모듈을 맨 처음 import 하는 쪽이 그 순서를 보장한다.

.env 파일이 없으면 load_dotenv()는 조용히 아무것도 안 한다. 그래서 도커처럼
환경변수를 직접 주입하는 환경에서도 코드 수정 없이 그대로 동작한다.
운영(systemd)은 WorkingDirectory의 .env를 읽는다.
"""
import os
import re

from dotenv import load_dotenv

load_dotenv()

# ---------------------------
# 필수 — 없으면 기동 실패
# ---------------------------
APP_TOKEN = os.getenv("APP_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET = os.getenv("JWT_SECRET")

AI2_SERVER_URL = os.getenv("AI2_SERVER_URL", "")

# ---------------------------
# AWS S3 (위험 클립 저장용 — 미설정 시 clips API는 503 반환)
# ---------------------------
AWS_REGION = os.getenv("AWS_REGION", "ap-northeast-2")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PRESIGN_EXPIRES = int(os.getenv("S3_PRESIGN_EXPIRES", "300"))
# 클립 재생용 presigned GET URL 만료 (업로드 PUT보다 길게 — 프론트가 목록 보다 클릭/재생까지 여유)
CLIP_VIEW_URL_EXPIRES = int(os.getenv("CLIP_VIEW_URL_EXPIRES", "3600"))
# 클립 보관 기간(일) — S3 버킷 lifecycle 만료 규칙과 맞춘다 (capstone-elderly-clips: 30일).
# lifecycle이 객체를 지워도 DB row가 status='uploaded'로 남으면 목록 API가 죽은 presigned
# URL을 내보내 프론트 재생이 403으로 실패한다 (2026-09-18 실발현, 8/9 클립 79건 전부).
# 0 이하면 만료 처리(스케줄러 + 목록 필터) 비활성.
CLIP_RETENTION_DAYS = int(os.getenv("CLIP_RETENTION_DAYS", "30"))

# 클립 업로드 최대 크기 (50MB = 5초 1080p H.264 충분, 악성 무제한 업로드 방지)
MAX_CLIP_SIZE_BYTES = 50 * 1024 * 1024

# 식별자 화이트리스트 (S3 key prefix 안전성 + DB injection 방지)
# 영문자/숫자/하이픈/언더스코어만 허용, 길이 1~64자
IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

ALLOWED_DEVICE_TYPES = {"camera", "raspberry_pi", "jetson", "other"}

# ---------------------------
# Scheduler advisory lock 키
# ---------------------------
# gunicorn -w N 환경에서 BackgroundScheduler가 워커마다 별도 인스턴스화되어
# 같은 job이 N번 동시 실행되는 문제 방지.
# advisory lock은 세션(connection) 단위 — 잡은 connection이 닫힐 때까지 보유.
# 다른 워커는 try_lock이 False 반환하면 즉시 skip.
LOCK_KEY_SWEEPER = 7001
LOCK_KEY_AGGREGATOR = 7002
LOCK_KEY_CLIP_EXPIRER = 7003

# ---------------------------
# Incidents 집계 정책 (Medallion Silver, migration 007)
# ---------------------------
#   - 같은 patient + 같은 event_type
#   - 시간 갭 <= 10초 = 같은 incident
#   - 15초 grace period (created_at >= NOW() - 15s 인 raw는 다음 사이클로 미룸,
#     진행 중 burst를 prematurely 잘라서 1 사건 = 2 incidents로 쪼개지는 거 방지)
#   - 매 30초마다 실행 → 보호자 알림 지연 최대 ~45초 (15s grace + 30s cycle)
INCIDENT_GAP_THRESHOLD_SEC = 10
INCIDENT_GRACE_PERIOD_SEC = 15
# 한 incident 최대 지속시간. 연속 검출 스트림이 10초 갭에 안 끊겨 수십 분짜리
# 거대 incident로 뭉치는 것 방지 ("한 사건"의 상식적 길이로 분리). 초과하면
# 같은 환자+event_type이라도 새 incident로 시작.
INCIDENT_MAX_DURATION_SEC = 300  # 5분


def validate() -> None:
    """필수 환경변수 검증. app.py가 기동 시 호출한다."""
    if not APP_TOKEN or not DATABASE_URL or not JWT_SECRET:
        raise RuntimeError("필수 환경변수가 설정되지 않았습니다. .env 파일을 확인하세요.")
