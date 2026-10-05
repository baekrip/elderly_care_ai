"""들어오는 스칼라 값 강제 변환 헬퍼.

외부(1차/2차 AI, 프론트)에서 온 값은 타입을 신뢰할 수 없다. 새 코드에서
직접 int()/float()/fromisoformat()을 부르지 말고 여기 함수를 쓸 것.

의존성 없음 — Flask도 DB도 모른다. 그래서 가장 먼저 분리했다.
"""
from datetime import datetime, timezone
from typing import Any, Optional


def parse_iso8601(ts_str: Optional[str]) -> datetime:
    """ISO-8601 문자열 → UTC aware datetime. 빈 값이면 현재 시각.

    naive datetime은 UTC로 간주한다. 모든 ts는 UTC로 저장하는 게 규약이므로
    여기서 naive를 만들어 내보내지 말 것.
    """
    if not ts_str:
        return datetime.now(timezone.utc)

    s = ts_str.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def clamp_int(val: Any, lo: int, hi: int, default: int) -> int:
    """int로 변환 후 [lo, hi] 범위로 자른다. 변환 실패 시 default."""
    try:
        i = int(val)
        return max(lo, min(hi, i))
    except Exception:
        return default


def maybe_float(val: Any) -> Optional[float]:
    """float로 변환. None이거나 변환 실패 시 None."""
    if val is None:
        return None
    try:
        return float(val)
    except Exception:
        return None
