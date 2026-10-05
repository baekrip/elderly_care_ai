"""2차 AI(LSTM) 분석 트리거.

10분마다 {AI2_SERVER_URL}/analyze 로 POST. 실패는 조용히 삼킨다 —
2차 AI가 죽어 있어도 백엔드 본연의 수신/조회는 계속 돌아야 하기 때문.

AI2_SERVER_URL이 비어 있으면 no-op이지만 스케줄러 job 자체는 계속 등록된다.
"""
from datetime import datetime, timezone

import requests as http_requests

from core.config import AI2_SERVER_URL, APP_TOKEN


def trigger_ai2_analysis() -> None:
    if not AI2_SERVER_URL:
        return
    try:
        http_requests.post(
            f"{AI2_SERVER_URL}/analyze",
            json={"trigger": "scheduled", "ts_utc": datetime.now(timezone.utc).isoformat()},
            headers={"Authorization": f"Bearer {APP_TOKEN}"},
            timeout=10
        )
    except Exception:
        pass
