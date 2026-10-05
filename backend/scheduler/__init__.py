"""백그라운드 스케줄러 등록.

app.py가 init_scheduler(app)을 한 번 호출하면 job 4개가 등록되고 시작된다.

app 객체를 여기서 import하지 않고 인자로 받는 이유:
job 함수들이 app.logger를 쓰는데, 모듈 import 시점에 app.py를 가져오면
app.py → scheduler → app.py 순환이 된다. Flask의 init_app(app) 관례가
바로 이 문제를 푸는 방식이다.

주의: gunicorn -w N 이면 워커마다 스케줄러가 따로 뜬다. 같은 job이 N번
동시에 도는 걸 막는 건 각 job 안의 pg_try_advisory_lock 이다 (config.py의
LOCK_KEY_* 참고). 워커 수를 늘려도 job은 한 번만 실행된다.
"""
from apscheduler.schedulers.background import BackgroundScheduler

from .aggregator import aggregate_events_into_incidents
from .ai2 import trigger_ai2_analysis
from .clip_expirer import expire_lifecycle_deleted_clips
from .sweeper import detect_orphan_data

__all__ = [
    "init_scheduler",
    "trigger_ai2_analysis",
    "detect_orphan_data",
    "expire_lifecycle_deleted_clips",
    "aggregate_events_into_incidents",
]


def init_scheduler(app) -> BackgroundScheduler:
    """job 4개를 등록하고 스케줄러를 시작한다. app.logger를 각 job에 주입."""
    logger = app.logger

    scheduler = BackgroundScheduler()
    scheduler.add_job(trigger_ai2_analysis, "interval", minutes=10)
    # 매일 00:00 UTC = 09:00 KST
    scheduler.add_job(detect_orphan_data, "cron", hour=0, minute=0, args=[logger])
    # sweeper 직후 — S3 lifecycle이 지운 클립 row를 expired로
    scheduler.add_job(expire_lifecycle_deleted_clips, "cron", hour=0, minute=10, args=[logger])
    scheduler.add_job(
        aggregate_events_into_incidents, "interval", seconds=30,
        max_instances=1, args=[logger],
    )
    scheduler.start()
    return scheduler
