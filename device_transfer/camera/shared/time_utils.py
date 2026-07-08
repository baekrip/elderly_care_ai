from __future__ import annotations

from datetime import datetime, timezone


def utc_iso_now() -> str:
    return utc_iso_from_datetime(datetime.now(timezone.utc))


def utc_iso_from_ms(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return utc_iso_now()
    try:
        value = int(timestamp_ms)
    except (TypeError, ValueError):
        return utc_iso_now()
    return utc_iso_from_datetime(datetime.fromtimestamp(value / 1000.0, timezone.utc))


def utc_iso_from_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
