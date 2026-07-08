"""DB Random Test Data Seeder — Phase 8.

Generates random elderly care action analysis data and inserts
into PostgreSQL directly or via Flask API.

Usage:
    python tools/seed_test_data.py --mode direct --host 192.168.0.100 --count 100
    python tools/seed_test_data.py --mode api --url http://localhost:5000 --count 50
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

# direct mode dependencies (optional)
try:
    import psycopg2
except ImportError:
    psycopg2 = None  # type: ignore

try:
    import requests
except ImportError:
    requests = None  # type: ignore


# --- data generation ---

DEVICE_KEYS = ["cam-01", "cam-02", "cam-kitchen", "cam-bedroom"]
PATIENT_CODES = ["P001", "P002", "P003"]
EVENT_TYPES = [
    "FORWARD_COLLAPSE_FROM_STANDING",
    "FORWARD_COLLAPSE_FROM_SITTING",
    "SIDEWAYS_COLLAPSE",
    "GRADUAL_COLLAPSE",
    "PROLONGED_FLOOR_LYING",
    "PROLONGED_INACTIVITY",
    "WANDERING_PATTERN",
    "NIGHT_UNUSUAL_ACTIVITY",
    "LOW_SKELETON_QUALITY",
]
ACTION_LABELS = ["STANDING", "SITTING", "LYING", "WALKING", "TRANSITION", "EATING", "RESTING_IN_CHAIR"]
SEVERITY_MAP = {"DANGER": 90, "ABNORMAL": 50, "QUALITY": 10}


def generate_event(base_time: datetime, index: int) -> dict:
    """Generate one random event record."""
    occurred = base_time + timedelta(minutes=random.randint(0, 1440))
    category = random.choice(["DANGER", "ABNORMAL", "QUALITY"])
    event_type = random.choice(EVENT_TYPES)
    confidence = round(random.uniform(0.45, 0.98), 3)

    return {
        "device_key": random.choice(DEVICE_KEYS),
        "patient_id": random.choice(PATIENT_CODES),
        "event_type": event_type,
        "confidence": confidence,
        "severity": SEVERITY_MAP[category],
        "event_status": "detected",
        "occurred_at": occurred.isoformat(),
        "clip_url": f"s3://elderly-clips/{occurred.strftime('%Y%m%d')}/{uuid.uuid4().hex[:8]}.mp4"
                    if category == "DANGER" else None,
        "payload_json": json.dumps({
            "candidate_category": category,
            "coarse_action": random.choice(ACTION_LABELS),
            "action_confidence": confidence,
            "trigger_flags": random.sample(
                ["torso_angle_spike", "vertical_velocity_spike", "head_below_hip",
                 "knee_angle_collapse", "inactivity_duration", "pose_confidence_drop"],
                k=random.randint(1, 3)
            ),
        }),
    }


# --- direct mode (psycopg2) ---

INSERT_SQL = """
INSERT INTO public.events
    (device_id, patient_id, event_type, confidence, severity,
     event_status, occurred_at, clip_url, payload_json)
VALUES (
    (SELECT id FROM public.devices WHERE device_key = %(device_key)s),
    (SELECT id FROM public.patients WHERE patient_code = %(patient_id)s),
    %(event_type)s, %(confidence)s, %(severity)s,
    %(event_status)s, %(occurred_at)s, %(clip_url)s, %(payload_json)s::jsonb
)
"""


def seed_direct(host: str, port: int, dbname: str, user: str, password: str, count: int) -> None:
    """Insert random data directly via psycopg2."""
    if psycopg2 is None:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
    cur = conn.cursor()

    base_time = datetime.now(timezone.utc) - timedelta(days=1)
    inserted = 0

    for i in range(count):
        event = generate_event(base_time, i)
        try:
            cur.execute(INSERT_SQL, event)
            inserted += 1
        except Exception as exc:
            print(f"  Row {i}: {exc}")
            conn.rollback()
            continue

    conn.commit()
    cur.close()
    conn.close()
    print(f"Direct mode: {inserted}/{count} rows inserted into {dbname}@{host}:{port}")

    # verification query
    conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM public.events")
    total = cur.fetchone()[0]
    cur.execute("SELECT event_type, count(*) FROM public.events GROUP BY event_type ORDER BY count(*) DESC LIMIT 5")
    top = cur.fetchall()
    cur.close()
    conn.close()
    print(f"Verification: {total} total events")
    print(f"Top event types: {top}")


# --- API mode (requests) ---

def seed_api(url: str, count: int, token: str) -> None:
    """Insert random data via Flask API."""
    if requests is None:
        print("ERROR: requests not installed. Run: pip install requests")
        sys.exit(1)

    base_url = url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    base_time = datetime.now(timezone.utc) - timedelta(days=1)
    success = 0
    failed = 0

    for i in range(count):
        event = generate_event(base_time, i)
        payload = {
            "device_key": event["device_key"],
            "patient_id": event["patient_id"],
            "event_type": event["event_type"],
            "confidence": event["confidence"],
            "severity": event["severity"],
            "ts": event["occurred_at"],
            "payload": json.loads(event["payload_json"]),
        }
        try:
            resp = requests.post(f"{base_url}/api/v1/events", json=payload, headers=headers, timeout=10)
            if resp.status_code < 300:
                success += 1
            else:
                print(f"  Row {i}: HTTP {resp.status_code} — {resp.text[:100]}")
                failed += 1
        except Exception as exc:
            print(f"  Row {i}: {exc}")
            failed += 1

    print(f"API mode: {success} success, {failed} failed out of {count}")


# --- CLI ---

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed test data for elderly care AI")
    parser.add_argument("--mode", choices=["direct", "api"], default="direct")
    parser.add_argument("--count", type=int, default=100)

    # direct mode args
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--dbname", default="elderly_care")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")

    # api mode args
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--token", default="dev-secret-token")

    args = parser.parse_args()

    print(f"Generating {args.count} random events...")

    if args.mode == "direct":
        seed_direct(args.host, args.port, args.dbname, args.user, args.password, args.count)
    else:
        seed_api(args.url, args.count, args.token)


if __name__ == "__main__":
    main()
