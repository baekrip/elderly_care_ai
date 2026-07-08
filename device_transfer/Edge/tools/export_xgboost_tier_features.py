from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edge.config import load_config
from edge.feature_extractor import FeatureExtractor
from edge.pose_estimator import YoloPoseEstimator


SUMMARY_SUFFIXES = ["mean", "std", "min", "max", "last"]


def summarize_feature_rows(feature_rows: list[dict[str, float]]) -> dict[str, float]:
    summary: dict[str, float] = {}
    if not feature_rows:
        return summary
    keys = sorted(feature_rows[0].keys())
    for key in keys:
        values = [float(row.get(key, 0.0)) for row in feature_rows]
        summary[f"{key}_mean"] = float(statistics.fmean(values))
        summary[f"{key}_std"] = float(statistics.pstdev(values)) if len(values) > 1 else 0.0
        summary[f"{key}_min"] = float(min(values))
        summary[f"{key}_max"] = float(max(values))
        summary[f"{key}_last"] = float(values[-1])
    return summary


def iter_sample_frames(start_frame: int, end_frame: int, max_frames: int) -> list[int]:
    if end_frame <= start_frame:
        return [start_frame]
    count = min(max_frames, max(1, end_frame - start_frame + 1))
    indices = np.linspace(start_frame, end_frame, num=count, dtype=int)
    return sorted({int(index) for index in indices})


def extract_window_features(
    video_path: Path,
    start_frame: int,
    end_frame: int,
    pose_estimator: YoloPoseEstimator,
    max_frames: int,
) -> tuple[dict[str, float], int]:
    capture = cv2.VideoCapture(str(video_path))
    extractor = FeatureExtractor()
    feature_rows: list[dict[str, float]] = []
    sample_frames = iter_sample_frames(start_frame, end_frame, max_frames=max_frames)

    for frame_number in sample_frames:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        detections = pose_estimator.predict(frame)
        if not detections:
            continue
        detection = detections[0]
        feature_map, _ = extractor.extract(
            track_id=1,
            bbox=detection.bbox,
            keypoints=detection.keypoints,
            frame_shape=frame.shape,
            timestamp_ms=int(frame_number * 1000 / max(1, int(capture.get(cv2.CAP_PROP_FPS) or 30))),
        )
        feature_rows.append({key: float(value) for key, value in feature_map.items() if isinstance(value, (int, float))})

    capture.release()
    return summarize_feature_rows(feature_rows), len(feature_rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export XGBoost tier feature dataset")
    parser.add_argument("--jobs", default="experiments/behavior_training/jobs/xgboost_jobs.jsonl")
    parser.add_argument("--output-csv", default="experiments/behavior_training/features/xgboost_tier_features.csv")
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--max-jobs", type=int, default=0)
    parser.add_argument("--max-frames-per-job", type=int, default=24)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--label-mode", choices=["tier", "fall_binary"], default="tier")
    return parser


def resolve_xgboost_label(job: dict[str, Any], label_mode: str) -> str:
    tier_label = str(job.get("event_tier", "NORMAL")).upper()
    dataset_family = str(job.get("dataset_family", "")).lower()
    action_type = str(job.get("action_type", "")).upper()

    if label_mode == "fall_binary":
        if tier_label == "DANGER" or action_type == "ABNOR_H" or dataset_family == "abnormal_drop":
            return "DROP"
        return "NORMAL"
    return tier_label


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    pose_estimator = YoloPoseEstimator(config)
    jobs_path = Path(args.jobs)
    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []

    if args.skip_existing and output_path.exists():
        print(f"skip-existing: {output_path}")
        return 0

    # jobs 전체 개수 미리 계산
    all_lines = jobs_path.read_text(encoding="utf-8").splitlines()
    total_jobs = len(all_lines)
    if args.max_jobs and args.max_jobs < total_jobs:
        total_jobs = args.max_jobs
    print(f"[export] 총 {total_jobs}개 job 처리 시작 | 출력: {output_path}")
    print("-" * 60)

    skipped = 0
    t_start = time.time()

    with jobs_path.open("r", encoding="utf-8") as file:
        for index, line in enumerate(file):
            if args.max_jobs and index >= args.max_jobs:
                break
            job = json.loads(line)
            t_job = time.time()
            features, used_frames = extract_window_features(
                video_path=Path(job["video_path"]),
                start_frame=int(job["start_frame"]),
                end_frame=int(job["end_frame"]),
                pose_estimator=pose_estimator,
                max_frames=args.max_frames_per_job,
            )
            elapsed = time.time() - t_job
            label = resolve_xgboost_label(job, args.label_mode)

            if not features:
                skipped += 1
                print(f"  [{index+1:>4}/{total_jobs}] SKIP  {job.get('sample_id','?')} ({elapsed:.1f}s) — feature 없음")
                continue

            row = {
                "sample_id": job["sample_id"],
                "video_path": job["video_path"],
                "window_role": job["window_role"],
                "tier_label": job["event_tier"],
                "model_label": label,
                "action_type": job["action_type"],
                "action_name": job["action_name"],
                "used_frames": used_frames,
            }
            row.update(features)
            rows.append(row)

            # 10개마다 또는 마지막에 진행률 출력
            done = index + 1
            if done % 10 == 0 or done == total_jobs:
                elapsed_total = time.time() - t_start
                rate = done / elapsed_total if elapsed_total > 0 else 0
                eta = (total_jobs - done) / rate if rate > 0 else 0
                print(f"  [{done:>4}/{total_jobs}] OK    {job.get('sample_id','?')} | 라벨={label} | 프레임={used_frames} | 경과={elapsed_total:.0f}s | 남은={eta:.0f}s")
            else:
                print(f"  [{done:>4}/{total_jobs}] OK    {job.get('sample_id','?')} | 라벨={label} | 프레임={used_frames} | {elapsed:.1f}s")

    print("-" * 60)
    if not rows:
        print(f"[export] 완료 — 내보낸 행 없음 (스킵={skipped})")
        return 1

    fieldnames = list(rows[0].keys())
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    total_elapsed = time.time() - t_start
    print(f"[export] 완료 — 내보낸 행={len(rows)} | 스킵={skipped} | 총 소요={total_elapsed:.1f}s | 출력={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
