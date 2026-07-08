from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dotenv import load_dotenv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit


@dataclass(frozen=True)
class BackendConfig:
    enabled: bool = False
    base_url: str = ""
    token: str = ""
    events_batch_path: str = "/api/v1/events/batch"
    alerts_immediate_path: str = "/api/v1/alerts/immediate"
    clip_upload_url_path: str = "/api/v1/clips/upload-url"
    clip_confirm_path: str = "/api/v1/clips/confirm"
    batch_interval_sec: float = 10.0
    normal_batch_interval_sec: float = 300.0
    request_timeout_sec: float = 10.0
    pending_file: str = "server/storage/results/backend_pending.jsonl"
    contract_error_file: str = "server/storage/results/backend_contract_errors.jsonl"
    max_pending_events: int = 10000
    max_pending_age_hours: float = 24.0

    @property
    def events_batch_url(self) -> str:
        return join_url(self.base_url, self.events_batch_path)

    @property
    def alerts_immediate_url(self) -> str:
        return join_url(self.base_url, self.alerts_immediate_path)

    @property
    def clip_upload_url(self) -> str:
        return join_url(self.base_url, self.clip_upload_url_path)

    @property
    def clip_confirm_url(self) -> str:
        return join_url(self.base_url, self.clip_confirm_path)


@dataclass(frozen=True)
class BackendResponse:
    ok: bool
    status_code: int
    text: str
    json_body: Any | None = None


@dataclass(frozen=True)
class PendingReplayResult:
    attempted: int = 0
    sent: int = 0
    kept: int = 0


@dataclass(frozen=True)
class BackendBatchSubmitResult:
    queued: int = 0
    forwarded: int = 0
    response: BackendResponse | None = None
    pending_replay: PendingReplayResult | None = None


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

def load_project_dotenv(dotenv_path: Path | None = None) -> None:
    """Load project .env values without overriding already exported environment variables."""
    candidates: list[Path] = []

    if dotenv_path is not None:
        candidates.append(dotenv_path)
    else:
        candidates.append(Path.cwd() / ".env")
        try:
            candidates.append(Path(__file__).resolve().parents[2] / ".env")
        except IndexError:
            pass

    for path in candidates:
        if not path.exists():
            continue

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()

            if key.startswith("export "):
                key = key[len("export "):].strip()

            if not key:
                continue

            value = value.strip()

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            os.environ.setdefault(key, value)

        return


_load_project_dotenv = load_project_dotenv


def config_from_project_config(project_config: dict[str, Any]) -> BackendConfig:
    backend = project_config.get("backend", {}) or {}

    env_backend_url = os.getenv("BACKEND_URL", "").strip()
    if env_backend_url == "54.116.119.98":
        env_backend_url = "https://homecare.p-e.kr"

    if env_backend_url:
        if not env_backend_url.startswith(("http://", "https://")):
            if ":" not in env_backend_url:
                env_backend_url = f"http://{env_backend_url}:5000"
            else:
                env_backend_url = f"http://{env_backend_url}"

    base_url = str(
        env_backend_url
        or os.getenv("BACKEND_BASE_URL")
        or os.getenv("AI_BACKEND_BASE_URL")
        or os.getenv("AI2_SERVER_URL")
        or backend.get("base_url", "")
        or ""
    ).strip().rstrip("/")

    if "54.116.119.98" in base_url:
        base_url = "https://homecare.p-e.kr"

    token = str(
        os.getenv("APP_TOKEN")
        or os.getenv("BACKEND_APP_TOKEN")
        or backend.get("token", "")
        or ""
    ).strip()

    backend_enabled = bool(backend.get("enabled", False))
    enabled = backend_enabled and bool(base_url)

    if backend_enabled and base_url and not token:
        raise ValueError(
            "BACKEND_BASE_URL/AI_BACKEND_BASE_URL/AI2_SERVER_URL은 설정됐지만 APP_TOKEN이 없습니다. "
            "/home/eagleeye/elderly_care_ai/.env에 APP_TOKEN=토큰값 을 넣으세요."
        )

    return BackendConfig(
        enabled=enabled,
        base_url=base_url,
        token=token,
        events_batch_path=str(backend.get("events_batch_path", "/api/v1/events/batch")),
        alerts_immediate_path=str(backend.get("alerts_immediate_path", "/api/v1/alerts/immediate")),
        clip_upload_url_path=str(backend.get("clip_upload_url_path", "/api/v1/clips/upload-url")),
        clip_confirm_path=str(backend.get("clip_confirm_path", "/api/v1/clips/confirm")),
        batch_interval_sec=float(backend.get("batch_interval_sec", 10.0)),
        normal_batch_interval_sec=float(backend.get("normal_batch_interval_sec", 300.0)),
        request_timeout_sec=float(backend.get("request_timeout_sec", 10.0)),
        pending_file=str(backend.get("pending_file", "server/storage/results/backend_pending.jsonl")),
        contract_error_file=str(backend.get("contract_error_file", "server/storage/results/backend_contract_errors.jsonl")),
        max_pending_events=int(backend.get("max_pending_events", 10000)),
        max_pending_age_hours=float(backend.get("max_pending_age_hours", 24.0)),
    )


def utc_iso_now() -> str:
    return _format_utc(datetime.now(timezone.utc))


def utc_iso_from_ms(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return utc_iso_now()

    try:
        value = int(timestamp_ms)
    except (TypeError, ValueError):
        return utc_iso_now()

    # Real epoch timestamps are used for backend ts. Relative video timestamps
    # stay in payload and use current UTC for backend storage time.
    if value >= 946_684_800_000:
        return _format_utc(datetime.fromtimestamp(value / 1000.0, timezone.utc))
    return utc_iso_now()


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def map_event_type(source_label: str | None, trigger_flags: list[str] | None = None) -> str:
    label = str(source_label or "").upper()
    flags = {str(flag).upper() for flag in (trigger_flags or [])}

    if label == "NORMAL":
        return "normal_activity_summary"

    fall_tokens = {
        "DANGER_DROP",
        "DROP",
        "FALL",
        "GRADUAL_FALL",
        "DANGER_GRADUAL_FALL",
        "GRADUAL_COLLAPSE",
        "SUDDEN_COLLAPSE",
        "FORWARD_COLLAPSE_FROM_STANDING",
        "FORWARD_COLLAPSE_FROM_SITTING",
        "SIDEWAYS_COLLAPSE",
        "BACKWARD_FALL",
        "CHAIR_SLIDE_FALL",
        "BED_ROLL_FALL",
        "SYNCOPE_COLLAPSE",
        "TIER_DROP_SUSPECT",
    }
    if label in fall_tokens or "FALL" in label or "COLLAPSE" in label:
        return "fall_detected"

    if "INACTIVITY" in label or "NO_MOVEMENT" in label or "INACTIVITY_DURATION" in flags:
        return "no_movement"

    if "LYING" in label or "FLOOR" in label:
        return "lying_down"

    if "POSE_LOST" in label:
        return "pose_lost"

    return "abnormal_posture"


def should_forward_level(level: str | None) -> bool:
    return str(level or "").lower() in {"normal", "abnormal", "danger"}


def _severity_for_level(level: str | None) -> int:
    normalized = str(level or "").lower()
    if normalized == "danger":
        return 90
    if normalized == "abnormal":
        return 60
    return 0


def _optional_int(value: Any | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_backend_event(
    *,
    device_key: str,
    patient_id: str,
    confidence: float,
    severity: int,
    timestamp_ms: int | None = None,
    ts: str | None = None,
    event_type: str | None = None,
    source_label: str | None = None,
    payload: dict[str, Any] | None = None,
    trigger_flags: list[str] | None = None,
    frame_id: Any | None = None,
) -> dict[str, Any]:
    resolved_event_type = event_type or map_event_type(source_label, trigger_flags)
    resolved_ts = ts or utc_iso_from_ms(timestamp_ms)
    event_payload = dict(payload or {})
    if source_label is not None:
        event_payload.setdefault("source_label", source_label)
    if timestamp_ms is not None:
        event_payload.setdefault("timestamp_ms", int(timestamp_ms))
    if trigger_flags:
        event_payload.setdefault("trigger_flags", trigger_flags)
    event_payload.setdefault("source", "elderly_care_ai")
    event_payload.setdefault(
        "dedup_key",
        f"{device_key}:{patient_id}:{resolved_ts}:{resolved_event_type}",
    )

    result = {
        "device_key": device_key,
        "patient_id": patient_id,
        "event_type": resolved_event_type,
        "confidence": round(float(confidence), 6),
        "severity": int(severity),
        "ts": resolved_ts,
        "payload": event_payload,
    }
    resolved_frame_id = _optional_int(frame_id)
    if resolved_frame_id is not None:
        result["frame_id"] = resolved_frame_id
    return result


def build_candidate_backend_event(
    *,
    device_key: str,
    patient_id: str,
    candidate_type: str,
    candidate_category: str,
    final_label: str,
    effective_level: str,
    confidence: float,
    timestamp_ms: int,
    capture_ts: str | None = None,
    analysis_ts: str | None = None,
    trigger_flags: list[str] | None = None,
    stgcn_inference_ms: float | None = None,
    frame_id: Any | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_label = final_label
    payload = {
        "candidate_type": candidate_type,
        "candidate_category": candidate_category,
        "final_label": final_label,
        "effective_level": effective_level,
        "analysis_ts": analysis_ts,
        "stgcn_inference_ms": stgcn_inference_ms,
    }
    payload.update(extra_payload or {})
    return build_backend_event(
        device_key=device_key,
        patient_id=patient_id,
        source_label=source_label,
        confidence=float(confidence),
        severity=_severity_for_level(effective_level),
        timestamp_ms=timestamp_ms,
        ts=capture_ts,
        payload=payload,
        trigger_flags=trigger_flags,
        frame_id=frame_id,
    )


def build_timeline_pattern_backend_event(
    *,
    device_key: str,
    patient_id: str,
    window_start_ms: int,
    window_end_ms: int,
    segments: list[dict[str, Any]],
    anomalies: list[dict[str, Any]],
    ts: str | None = None,
) -> dict[str, Any]:
    has_anomaly = bool(anomalies)
    event_type = "pattern_anomaly_detected" if has_anomaly else "normal_activity_summary"
    payload = {
        "payload_kind": "timeline_pattern_batch",
        "delivery_policy": "five_min_batch_force_on_anomaly" if has_anomaly else "five_min_batch",
        "window_start_ms": int(window_start_ms),
        "window_end_ms": int(window_end_ms),
        "window_duration_ms": max(int(window_end_ms) - int(window_start_ms), 0),
        "segment_count": len(segments),
        "segments": segments,
        "anomalies": anomalies,
        "pattern_level": "ABNORMAL" if has_anomaly else "NORMAL",
    }
    return build_backend_event(
        device_key=device_key,
        patient_id=patient_id,
        event_type=event_type,
        source_label="PATTERN_ANOMALY" if has_anomaly else "NORMAL_ACTIVITY_SUMMARY",
        confidence=1.0,
        severity=60 if has_anomaly else 0,
        timestamp_ms=window_end_ms,
        ts=ts,
        payload=payload,
    )


def build_events_batch(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"events": events}


def build_immediate_alert(
    *,
    device_key: str,
    patient_id: str,
    alert_type: str,
    alert_level: str,
    message: str,
    ts: str,
    payload: dict[str, Any] | None = None,
    ref_event_id: Any | None = None,
) -> dict[str, Any]:
    alert: dict[str, Any] = {
        "device_key": device_key,
        "patient_id": patient_id,
        "alert_type": alert_type,
        "alert_level": alert_level,
        "level": alert_level,
        "message": message,
        "ts": ts,
        "payload": dict(payload or {}),
    }
    if ref_event_id is not None:
        alert["ref_event_id"] = ref_event_id
    return alert


def extract_first_event_id(response_json: Any | None) -> Any | None:
    if not isinstance(response_json, dict):
        return None

    if response_json.get("event_id") is not None:
        return response_json["event_id"]

    data = response_json.get("data")
    if isinstance(data, dict) and data.get("event_id") is not None:
        return data["event_id"]

    results = response_json.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            return first.get("event_id") or first.get("id")

    return None


def extract_first_alert_id(response_json: Any | None) -> Any | None:
    if not isinstance(response_json, dict):
        return None

    if response_json.get("alert_id") is not None:
        return response_json["alert_id"]

    if response_json.get("id") is not None:
        return response_json["id"]

    data = response_json.get("data")
    if isinstance(data, dict):
        return data.get("alert_id") or data.get("id")

    return None


class BackendForwarder:
    def __init__(self, config: BackendConfig) -> None:
        self.config = config

    def post_events_batch(self, events: list[dict[str, Any]]) -> BackendResponse:
        return self._post_json(self.config.events_batch_url, build_events_batch(events))

    def post_immediate_alert(self, alert: dict[str, Any]) -> BackendResponse:
        return self._post_json(self.config.alerts_immediate_url, alert)

    def retry_pending(self) -> PendingReplayResult:
        return retry_pending(self.config.pending_file, post_json=self._post_json)

    def _post_json(self, url: str, payload: dict[str, Any]) -> BackendResponse:
        if not url.startswith(("http://", "https://")):
            return BackendResponse(ok=False, status_code=0, text=f"invalid_url: {url}", json_body=None)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_sec) as response:
                text = response.read().decode("utf-8", errors="replace")
                return BackendResponse(
                    ok=200 <= int(response.status) < 300,
                    status_code=int(response.status),
                    text=text,
                    json_body=_parse_json(text),
                )
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            return BackendResponse(ok=False, status_code=int(exc.code), text=text, json_body=_parse_json(text))
        except urllib.error.URLError as exc:
            return BackendResponse(ok=False, status_code=0, text=str(exc.reason), json_body=None)
        except TimeoutError as exc:
            return BackendResponse(ok=False, status_code=0, text=str(exc), json_body=None)


class BackendEventBatchScheduler:
    def __init__(
        self,
        forwarder: Any,
        interval_sec: float = 10.0,
        now_func: Any | None = None,
    ) -> None:
        self.forwarder = forwarder
        self.interval_sec = max(float(interval_sec), 0.0)
        self.now_func = now_func or time.monotonic
        self._events: list[dict[str, Any]] = []
        self._last_flush_at = float(self.now_func())

    def submit(self, event: dict[str, Any], *, force: bool = False) -> BackendBatchSubmitResult:
        self._events.append(event)
        if not force and not self._should_flush():
            return BackendBatchSubmitResult(queued=len(self._events))
        return self.flush()

    def flush(self) -> BackendBatchSubmitResult:
        if not self._events:
            return BackendBatchSubmitResult()

        events = list(self._events)
        response = self.forwarder.post_events_batch(events)
        if response.ok:
            self._events.clear()
            self._last_flush_at = float(self.now_func())
            pending_replay = self.forwarder.retry_pending()
            return BackendBatchSubmitResult(
                queued=0,
                forwarded=len(events),
                response=response,
                pending_replay=pending_replay,
            )

        if is_retryable_response(response):
            append_pending(
                self.forwarder.config.pending_file,
                kind="events_batch",
                url=self.forwarder.config.events_batch_url,
                payload={"events": events},
                response=response,
                max_records=getattr(self.forwarder.config, "max_pending_events", 10000),
                max_age_hours=getattr(self.forwarder.config, "max_pending_age_hours", 24.0),
            )
        elif not response.ok:
            append_contract_error(
                getattr(self.forwarder.config, "contract_error_file", "server/storage/results/backend_contract_errors.jsonl"),
                kind="events_batch",
                url=self.forwarder.config.events_batch_url,
                payload={"events": events},
                response=response,
            )
        return BackendBatchSubmitResult(queued=len(self._events), forwarded=0, response=response)

    def _should_flush(self) -> bool:
        return float(self.now_func()) - self._last_flush_at >= self.interval_sec


def _parse_json(text: str) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def is_retryable_response(response: BackendResponse) -> bool:
    if response.ok:
        return False
    return response.status_code == 0 or response.status_code >= 500


SENSITIVE_KEY_PARTS = (
    "authorization",
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "upload_url",
    "presigned",
    "access_key",
)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).lower()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _mask_authorization(value: str) -> str:
    if value.lower().startswith("bearer "):
        return "Bearer ***"
    return "***"


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                if str(key).lower() == "authorization" and isinstance(item, str):
                    masked[str(key)] = _mask_authorization(item)
                else:
                    masked[str(key)] = "***"
                continue
            masked[str(key)] = mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    return value


def _mask_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***" if url else url
    if not parts.query:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "***", parts.fragment))


def _collect_sensitive_strings(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_sensitive_key(key):
                if isinstance(item, str) and item:
                    if str(key).lower() == "authorization" and item.lower().startswith("bearer "):
                        token = item.split(" ", 1)[1]
                        if token:
                            found.add(token)
                    found.add(item)
            found.update(_collect_sensitive_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_sensitive_strings(item))
    elif isinstance(value, str) and value.startswith(("http://", "https://")):
        try:
            found.update(value for _, value in parse_qsl(urlsplit(value).query) if value)
        except ValueError:
            pass
    return found


def _redact_text(text: str, secrets: set[str]) -> str:
    redacted = text
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted[:2000]


def _parse_utc_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _prune_pending_records(
    records: list[dict[str, Any]],
    *,
    max_records: int | None = None,
    max_age_hours: float | None = None,
) -> list[dict[str, Any]]:
    kept = list(records)
    if max_age_hours is not None and max_age_hours > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - float(max_age_hours) * 3600.0
        filtered: list[dict[str, Any]] = []
        for record in kept:
            created_at = _parse_utc_iso(record.get("created_at"))
            if created_at is None or created_at.timestamp() >= cutoff:
                filtered.append(record)
        kept = filtered
    if max_records is not None and max_records > 0 and len(kept) > max_records:
        kept = kept[-int(max_records):]
    return kept


def append_pending(
    path: str | Path,
    *,
    kind: str,
    url: str,
    payload: dict[str, Any],
    response: BackendResponse,
    max_records: int | None = None,
    max_age_hours: float | None = None,
) -> None:
    pending_path = Path(path)
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    secrets = _collect_sensitive_strings({"url": url, "payload": payload, "response": response.text})
    record = {
        "created_at": utc_iso_now(),
        "kind": kind,
        "url": _mask_url(url),
        "payload": mask_sensitive(payload),
        "response_status": response.status_code,
        "response_text": _redact_text(response.text, secrets),
    }
    records: list[dict[str, Any]] = []
    if pending_path.exists():
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    records.append(record)
    records = _prune_pending_records(records, max_records=max_records, max_age_hours=max_age_hours)
    pending_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )


def append_contract_error(
    path: str | Path,
    *,
    kind: str,
    url: str,
    payload: dict[str, Any],
    response: BackendResponse,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    secrets = _collect_sensitive_strings({"url": url, "payload": payload, "response": response.text})
    record = {
        "created_at": utc_iso_now(),
        "kind": kind,
        "url": _mask_url(url),
        "payload": mask_sensitive(payload),
        "response_status": response.status_code,
        "response_text": _redact_text(response.text, secrets),
        "retryable": False,
    }
    with target.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def retry_pending(
    path: str | Path,
    *,
    post_json: Any,
) -> PendingReplayResult:
    pending_path = Path(path)
    if not pending_path.exists():
        return PendingReplayResult()

    records: list[dict[str, Any]] = []
    for line in pending_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            records.append(
                {
                    "created_at": utc_iso_now(),
                    "kind": "invalid_json",
                    "url": "",
                    "payload": {"raw": "***"},
                    "response_status": 0,
                    "response_text": "invalid pending json line",
                }
            )
            continue
        if isinstance(record, dict):
            records.append(record)

    attempted = 0
    sent = 0
    kept: list[dict[str, Any]] = []
    for record in records:
        url = str(record.get("url", ""))
        payload = record.get("payload")
        if not url or not isinstance(payload, dict):
            kept.append(record)
            continue
        attempted += 1
        response = post_json(url, payload)
        if response.ok:
            sent += 1
            continue
        updated = dict(record)
        updated["last_retry_at"] = utc_iso_now()
        updated["response_status"] = response.status_code
        updated["response_text"] = _redact_text(response.text, _collect_sensitive_strings(record))
        kept.append(updated)

    if kept:
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in kept),
            encoding="utf-8",
        )
    else:
        pending_path.unlink(missing_ok=True)

    return PendingReplayResult(attempted=attempted, sent=sent, kept=len(kept))
