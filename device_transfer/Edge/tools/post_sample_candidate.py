from __future__ import annotations

import argparse
import json
import time

import requests


def build_sample_candidate_batch(
    camera_id: str,
    device_id: str,
    end_ts_ms: int | None = None,
) -> dict[str, object]:
    frame_count = 24
    frame_interval_ms = 66
    if end_ts_ms is None:
        start_ts_ms = 1000
        window_end_ts_ms = 2600
    else:
        window_end_ts_ms = int(end_ts_ms)
        start_ts_ms = window_end_ts_ms - (frame_count - 1) * frame_interval_ms

    sequence = []
    for frame_idx in range(frame_count):
        ts_ms = start_ts_ms + frame_idx * frame_interval_ms
        sequence.append(
            {
                "frame_idx": frame_idx,
                "ts_ms": ts_ms,
                "pose_conf_mean": 0.9,
                "bbox_xyxy": [100, 100, 200, 300],
                "keypoints_17": [[0.1 + frame_idx * 0.001, 0.2, 0.9] for _ in range(17)],
                "features": {
                    "torso_angle_deg": 30.0 + frame_idx,
                    "center_velocity_px_s": 15.0,
                    "visible_joint_ratio": 0.95,
                },
            }
        )

    return {
        "windows": [
            {
                "schema_version": "v0.3",
                "device_id": device_id,
                "camera_id": camera_id,
                "track_id": 1,
                "candidate_type": "FORWARD_FALL",
                "candidate_category": "DANGER",
                "composite_condition": "torso_angle_spike+velocity_spike",
                "coarse_action": "FALL",
                "coarse_confidence": 0.92,
                "window": {
                    "start_ts_ms": start_ts_ms,
                    "end_ts_ms": window_end_ts_ms,
                    "fps": 12,
                    "frame_count": frame_count,
                },
                "trigger_flags": ["torso_angle_spike", "velocity_spike"],
                "preprocess": {"enabled": True, "low_light": False},
                "sequence": sequence,
                "video_ref": {
                    "segment_ids": ["segment_0001.mp4"],
                    "start_offset_ms": 0,
                    "end_offset_ms": 3000,
                },
                "candidate_clip_ref": {"enabled": True, "clip_id": "danger_clip_0001"},
            }
        ]
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Post one sample candidate window to the local server")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--camera-id", default="cam_livingroom_01")
    parser.add_argument("--device-id", default="pc_local_01")
    parser.add_argument("--room-id", default="living_room")
    parser.add_argument("--end-ts-ms", type=int, default=None)
    parser.add_argument("--live-timestamps", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    requests.post(
        f"{args.base_url}/api/cameras/register",
        json={
            "camera_id": args.camera_id,
            "room_id": args.room_id,
            "display_name": "Local Sample Camera",
            "stream_url": None,
            "metadata": {"source": "sample-candidate"},
        },
        timeout=10,
    ).raise_for_status()

    response = requests.post(
        f"{args.base_url}/api/candidates/submit",
        json=build_sample_candidate_batch(
            camera_id=args.camera_id,
            device_id=args.device_id,
            end_ts_ms=int(time.time() * 1000) if args.live_timestamps else args.end_ts_ms,
        ),
        timeout=30,
    )
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
