"""애플리케이션 진입점 — 조립만 한다.

**이 파일 이름과 `app` 객체 이름을 바꾸지 말 것.**
운영 서버의 systemd 유닛이 gunicorn을 `app:app` 으로 띄운다:

    ExecStart=.../venv/bin/gunicorn -w 2 --worker-class gthread --threads 8 \
              -b 127.0.0.1:5000 --timeout 120 --keep-alive 75 \
              --access-logfile - app:app

즉 "app.py 라는 모듈에서 app 이라는 객체를 찾아라"는 뜻이다. 여기를 바꾸려면
서버의 /etc/systemd/system/capstone.service 도 같이 고쳐야 하고, 안 그러면
git pull + restart 직후 gunicorn이 부팅에 실패한다. 유닛에 Restart=always 가
박혀 있어 3초마다 무한 재시작이 돌고, systemctl status 로는 "active
(auto-restart)" 로 보여서 살아있는 것처럼 착각하기 쉽다 — journalctl 로 확인할 것.

실제 로직은 각 모듈에 있다:
    config.py       환경변수·상수
    db.py           엔진, FK 해결 헬퍼
    s3.py           S3 클라이언트
    auth.py         두 인증 체계 + 역할 필터
    utils.py        스칼라 강제 변환
    serializers.py  DB row → JSON 응답
    scheduler/      백그라운드 job (AI2 트리거 / sweeper / incidents 집계)
    routes/         Blueprint 별 라우트
"""
import os
from datetime import timedelta

from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager

from core import config
from core.config import JWT_SECRET
from routes import register_blueprints
from scheduler import init_scheduler

# 필수 환경변수(APP_TOKEN/DATABASE_URL/JWT_SECRET) 검증 — 없으면 여기서 기동 실패.
# 다른 모듈이 import 시점에 이 값들을 쓰므로 가장 먼저 확인한다.
config.validate()

app = Flask(__name__)
CORS(app)

app.config["JWT_SECRET_KEY"] = JWT_SECRET
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=12)
jwt = JWTManager(app)

# 라우트 등록. URL 규칙은 각 routes/ 모듈 안에 그대로 있다.
register_blueprints(app)

# 백그라운드 job 3개 등록 + 시작 (AI2 트리거 / orphan sweeper / incidents 집계).
# 워커 중복 실행은 각 job 내부의 advisory lock이 막는다 (config.LOCK_KEY_*).
init_scheduler(app)


# ---------------------------
# Entry
# ---------------------------
# 개발용 Flask 내장 서버. 운영은 위 주석의 gunicorn이 app 객체를 직접 가져간다.
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true"
    )
