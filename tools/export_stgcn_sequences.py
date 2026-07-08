from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edge.config import load_config
from edge.pose_estimator import YoloPoseEstimator
from tools.static_feature_export_io import JsonObject
from tools.training_runtime_paths import resolve_bundle_model_path


MULTICLASS_LABEL_MAP = {
    "NORMAL": "NORMAL",
    "ABNOR_H": "DROP",
    "ABNOR_W": "WANDER",
}


def resolve_label_name(job: JsonObject, label_mode: str) -> str | None:
    dataset_family = str(job.get("dataset_family", "")).lower()
    event_tier = str(job.get("event_tier", "")).upper()
    action_type = str(job.get("action_type", "")).upper()

    if label_mode == "fall_binary":
        if action_type == "ABNOR_H" or event_tier == "DANGER" or dataset_family == "abnormal_drop":
            return "DROP"
        return "NORMAL"

    raw_label = action_type if event_tier != "NORMAL" else "NORMAL"
    return MULTICLASS_LABEL_MAP.get(raw_label, "NORMAL" if event_tier == "NORMAL" else None)


def sample_frames(start_frame: int, end_frame: int, target_frames: int) -> list[int]:
    if end_frame <= start_frame:
        return [start_frame] * target_frames
    values = np.linspace(start_frame, end_frame, num=target_frames, dtype=int)
    return [int(value) for value in values]


def extract_sequence(video_path: Path, start_frame: int, end_frame: int, pose_estimator: YoloPoseEstimator, target_frames: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(video_path))
    frames = sample_frames(start_frame, end_frame, target_frames)
    sequence = np.zeros((3, target_frames, 17, 1), dtype=np.float32)

    for index, frame_number in enumerate(frames):
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        detections = pose_estimator.predict(frame)
        if not detections:
            continue
        keypoints = detections[0].keypoints[:17]
        hip_center_x = (keypoints[11][0] + keypoints[12][0]) / 2
        hip_center_y = (keypoints[11][1] + keypoints[12][1]) / 2
        for joint_index, kp in enumerate(keypoints):
            sequence[0, index, joint_index, 0] = float(kp[0] - hip_center_x)
            sequence[1, index, joint_index, 0] = float(kp[1] - hip_center_y)
            sequence[2, index, joint_index, 0] = float(kp[2])

    capture.release()
    return sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export ST-GCN sequence tensors")
    parser.add_argument("--jobs", default="experiments/behavior_training/jobs/stgcn_jobs.jsonl")
    parser.add_argument("--output", default="experiments/behavior_training/sequences/stgcn_sequences.npz")
    parser.add_argument("--meta-out", default="experiments/behavior_training/reports/stgcn_sequence_meta.json")
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--target-frames", type=int, default=24)
    parser.add_argument("--max-jobs", type=int, default=0)
    parser.add_argument("--include-labels", default="NORMAL,DROP,WANDER")
    parser.add_argument("--label-mode", choices=["multiclass", "fall_binary"], default="multiclass")
    parser.add_argument("--max-per-label", type=int, default=0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    config["model"]["model_path"] = str(
        resolve_bundle_model_path(args.config, config["model"]["model_path"]),
    )
    pose_estimator = YoloPoseEstimator(config)
    include_labels = {label.strip().upper() for label in str(args.include_labels).split(",") if label.strip()}
    jobs_path = Path(args.jobs)
    output_path = Path(args.output)
    meta_path = Path(args.meta_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)

    sequences: list[np.ndarray] = []
    labels: list[int] = []
    label_names: list[str] = []
    samples: list[JsonObject] = []
    emitted_counts: Counter[str] = Counter()

    with jobs_path.open("r", encoding="utf-8") as file:
        for index, line in enumerate(file):
            if args.max_jobs and index >= args.max_jobs:
                break
            job = json.loads(line)
            label_name = resolve_label_name(job, args.label_mode)
            if label_name is None:
                continue
            if include_labels and label_name.upper() not in include_labels:
                continue
            if args.max_per_label and emitted_counts[label_name] >= args.max_per_label:
                continue
            sequence = extract_sequence(
                video_path=Path(job["video_path"]),
                start_frame=int(job["start_frame"]),
                end_frame=int(job["end_frame"]),
                pose_estimator=pose_estimator,
                target_frames=args.target_frames,
            )
            if not np.any(sequence):
                continue
            if label_name not in label_names:
                label_names.append(label_name)
            label_index = label_names.index(label_name)
            sequences.append(sequence)
            labels.append(label_index)
            emitted_counts[label_name] += 1
            samples.append(
                {
                    "sample_id": job["sample_id"],
                    "video_path": job["video_path"],
                    "label_name": label_name,
                    "start_frame": int(job["start_frame"]),
                    "end_frame": int(job["end_frame"]),
                }
            )

    if not sequences:
        print("no sequences exported")
        return 1

    tensor = np.stack(sequences, axis=0)
    np.savez_compressed(output_path, sequences=tensor, labels=np.asarray(labels, dtype=np.int64))
    meta = {
        "rows": len(sequences),
        "label_names": label_names,
        "label_counts": dict(Counter(sample["label_name"] for sample in samples)),
        "samples": samples[:50],
        "target_frames": args.target_frames,
        "include_labels": sorted(include_labels),
        "label_mode": args.label_mode,
        "max_per_label": args.max_per_label,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported_sequences={len(sequences)} labels={meta['label_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
