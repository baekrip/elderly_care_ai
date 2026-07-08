from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from server.config import load_config
from server.services.backend_forwarder import (
    BackendConfig,
    BackendForwarder,
    append_pending,
    build_backend_event,
    build_immediate_alert,
    config_from_project_config,
    extract_first_event_id,
    is_retryable_response,
    load_project_dotenv,
)


# ── 백엔드에서 받은 예시 형식 그대로 사용하는 멀티 이벤트 샘플 ──────────────
def build_multi_sample_events(device_key: str, patient_id: str) -> list[dict]:
    """
    백엔드 예시 curl 형식과 동일한 3개 이벤트 페이로드.
    이벤트 내용을 바꾸고 싶으면 아래 리스트를 직접 수정하면 됩니다.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        {
            "device_key": device_key,
            "patient_id": patient_id,
            "event_type": "fall_detected",
            "confidence": 0.91,
            "severity": 85,
            "ts": "2026-05-09T07:00:00Z",
        },
        {
            "device_key": device_key,
            "patient_id": patient_id,
            "event_type": "no_movement",
            "confidence": 0.78,
            "severity": 50,
            "ts": "2026-05-09T07:01:00Z",
        },
        {
            "device_key": device_key,
            "patient_id": patient_id,
            "event_type": "lying_down",
            "confidence": 0.85,
            "severity": 60,
            "ts": "2026-05-09T07:02:00Z",
        },
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a temporary backend events/batch test payload",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  # 백엔드 예시 형식 그대로 3개 이벤트 전송 (--multi)
  python tools/test_backend_batch.py --multi

  # URL/토큰을 직접 지정해서 전송
  python tools/test_backend_batch.py --multi --base-url http://<EC2_IP>:5000 --token <APP_TOKEN>

  # 단일 이벤트 전송 (기존 방식)
  python tools/test_backend_batch.py --source-label DANGER_DROP --confidence 0.9 --severity 80

  # 단일 이벤트 + 즉시 알림
  python tools/test_backend_batch.py --danger-alert --device-key raspi-001

  # 실제 전송 없이 페이로드만 확인
  python tools/test_backend_batch.py --multi --dry-run

URL/토큰 변경 방법:
  방법 1 (권장): server/config.yaml 의 backend.base_url, backend.token 값 수정
  방법 2: 실행 시 --base-url, --token 인수로 직접 지정
""",
    )
    parser.add_argument("--config", default="server/config.yaml")
    # ── 서버 접속 정보 ────────────────────────────────────────────────────────
    parser.add_argument("--base-url", default=None,
                        help="백엔드 주소 (예: http://<EC2_IP>:5000). 생략 시 config.yaml 사용")
    parser.add_argument("--token", default=None,
                        help="인증 토큰. 생략 시 config.yaml 사용")
    # ── 공통 ─────────────────────────────────────────────────────────────────
    parser.add_argument("--device-key", default="cam_livingroom_01",
                        help="장치 키 (예: raspi-001, cam_livingroom_01)")
    parser.add_argument("--patient-id", default="P001")
    # ── 멀티 이벤트 옵션 ─────────────────────────────────────────────────────
    parser.add_argument("--multi", action="store_true",
                        help="백엔드 예시 형식 그대로 3개 이벤트 한번에 전송")
    # ── 단일 이벤트 옵션 (--multi 미사용 시) ─────────────────────────────────
    parser.add_argument("--event-type", default=None,
                        help="단일 이벤트 타입 (예: fall_detected)")
    parser.add_argument("--source-label", default="DANGER_DROP",
                        help="모델 레이블 → event_type 자동 변환에 사용")
    parser.add_argument("--confidence", type=float, default=0.91)
    parser.add_argument("--severity", type=int, default=85)
    parser.add_argument("--danger-alert", action="store_true",
                        help="즉시 알림(alerts/immediate)도 같이 전송 (단일 이벤트 시만 동작)")
    parser.add_argument("--message", default="fall_detected danger sample")
    # ── 기타 ─────────────────────────────────────────────────────────────────
    parser.add_argument("--pending-file", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 전송 없이 URL과 페이로드만 출력")
    return parser.parse_args()


def main() -> int:
    load_project_dotenv()
    args = parse_args()
    config = load_backend_config(args)

    # ── 이벤트 목록 결정 ──────────────────────────────────────────────────────
    if args.multi:
        events = build_multi_sample_events(args.device_key, args.patient_id)
        print(f"[multi] {len(events)}개 이벤트 전송 모드")
    else:
        events = [
            build_backend_event(
                device_key=args.device_key,
                patient_id=args.patient_id,
                event_type=args.event_type,
                source_label=args.source_label,
                confidence=args.confidence,
                severity=args.severity,
                payload={"test_source": "tools.test_backend_batch"},
            )
        ]

    batch_payload = {"events": events}

    # ── dry-run: 전송 없이 페이로드 출력만 ────────────────────────────────────
    if args.dry_run:
        print("=== DRY RUN (실제 전송 안 함) ===")
        print(f"EVENTS_BATCH_URL={config.events_batch_url}")
        print(json.dumps(batch_payload, ensure_ascii=False, indent=2))
        if args.danger_alert and not args.multi:
            alert_payload = _build_alert(args, events[0])
            print(f"ALERTS_IMMEDIATE_URL={config.alerts_immediate_url}")
            print(json.dumps(alert_payload, ensure_ascii=False, indent=2))
        return 0

    # ── 실제 전송 ─────────────────────────────────────────────────────────────
    forwarder = BackendForwarder(config)
    print(f"EVENTS_BATCH_URL={config.events_batch_url}")
    batch_response = forwarder.post_events_batch(events)
    print(f"EVENTS_BATCH_STATUS={batch_response.status_code}")
    print(f"EVENTS_BATCH_OK={str(batch_response.ok).lower()}")
    print(f"EVENTS_BATCH_BODY={batch_response.text}")

    if is_retryable_response(batch_response):
        append_pending(
            config.pending_file,
            kind="events_batch",
            url=config.events_batch_url,
            payload=batch_payload,
            response=batch_response,
        )
        print(f"PENDING_SAVED={config.pending_file}")
    elif not batch_response.ok:
        print(f"NOT_RETRYABLE_STATUS={batch_response.status_code}")
        return 2

    event_id = extract_first_event_id(batch_response.json_body)
    print(f"EVENT_ID={event_id}")

    # ── 단일 이벤트 + --danger-alert 일 때만 즉시 알림 전송 ──────────────────
    if args.danger_alert and not args.multi:
        alert_payload = _build_alert(args, events[0])
        if event_id is not None:
            alert_payload["ref_event_id"] = event_id

        print(f"ALERTS_IMMEDIATE_URL={config.alerts_immediate_url}")
        alert_response = forwarder.post_immediate_alert(alert_payload)
        print(f"ALERTS_IMMEDIATE_STATUS={alert_response.status_code}")
        print(f"ALERTS_IMMEDIATE_OK={str(alert_response.ok).lower()}")
        print(f"ALERTS_IMMEDIATE_BODY={alert_response.text}")

        if is_retryable_response(alert_response):
            append_pending(
                config.pending_file,
                kind="alerts_immediate",
                url=config.alerts_immediate_url,
                payload=alert_payload,
                response=alert_response,
            )
            print(f"PENDING_SAVED={config.pending_file}")
        elif not alert_response.ok:
            print(f"NOT_RETRYABLE_STATUS={alert_response.status_code}")
            return 3

    return 0


def _build_alert(args: argparse.Namespace, event: dict) -> dict:
    return build_immediate_alert(
        device_key=args.device_key,
        patient_id=args.patient_id,
        alert_type=event["event_type"],
        alert_level="danger",
        message=args.message,
        ts=event["ts"],
        payload={"event_type": event["event_type"], "test_source": "tools.test_backend_batch"},
    )


def load_backend_config(args: argparse.Namespace) -> BackendConfig:
    config_path = Path(args.config)
    project_config = load_config(config_path) if config_path.exists() else {"backend": {}}
    config = config_from_project_config(project_config)

    return BackendConfig(
        enabled=config.enabled,
        base_url=args.base_url or config.base_url,
        token=args.token or config.token,
        events_batch_path=config.events_batch_path,
        alerts_immediate_path=config.alerts_immediate_path,
        request_timeout_sec=config.request_timeout_sec,
        pending_file=args.pending_file or config.pending_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
