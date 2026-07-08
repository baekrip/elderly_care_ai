from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a YOLO pose dataset from reviewed pseudo-label JSONL")
    parser.add_argument("--pseudo-labels-jsonl", required=True)
    parser.add_argument("--dataset-dir", default="experiments/yolo_pose_dataset")
    parser.add_argument("--report-out", default="experiments/behavior_training/reports/yolo_pose_dataset_quality.json")
    parser.add_argument("--min-pose-confidence", type=float, default=0.6)
    parser.add_argument("--min-visible-joint-ratio", type=float, default=0.6)
    parser.add_argument("--copy-images", action="store_true")
    return parser


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_bbox(bbox_xyxy: list[float], width: float, height: float) -> list[float]:
    x1, y1, x2, y2 = [float(item) for item in bbox_xyxy[:4]]
    cx = ((x1 + x2) / 2.0) / width
    cy = ((y1 + y2) / 2.0) / height
    bw = abs(x2 - x1) / width
    bh = abs(y2 - y1) / height
    return [cx, cy, bw, bh]


def normalize_keypoints(keypoints: list[Any], width: float, height: float) -> list[float]:
    values: list[float] = []
    for item in keypoints[:17]:
        if isinstance(item, dict):
            x = float(item["x"])
            y = float(item["y"])
            confidence = float(item.get("confidence", item.get("conf", 0.0)))
        else:
            x = float(item[0])
            y = float(item[1])
            confidence = float(item[2]) if len(item) > 2 else 0.0
        visibility = 2 if confidence > 0 else 0
        values.extend([x / width, y / height, float(visibility)])
    while len(values) < 17 * 3:
        values.extend([0.0, 0.0, 0.0])
    return values


def yolo_pose_line(record: dict[str, Any]) -> str:
    width = float(record["width"])
    height = float(record["height"])
    bbox = normalize_bbox(record["bbox_xyxy"], width, height)
    keypoints = normalize_keypoints(record["keypoints"], width, height)
    values = [0.0, *bbox, *keypoints]
    return " ".join(f"{value:.6f}" for value in values)


def visible_joint_ratio(record: dict[str, Any]) -> float:
    keypoints = record.get("keypoints") or []
    if not keypoints:
        return 0.0
    visible = 0
    for item in keypoints[:17]:
        confidence = item.get("confidence", item.get("conf", 0.0)) if isinstance(item, dict) else (item[2] if len(item) > 2 else 0.0)
        if float(confidence) > 0:
            visible += 1
    return visible / 17.0


def write_dataset_yaml(dataset_dir: Path) -> None:
    content = "\n".join(
        [
            f"path: {dataset_dir.as_posix()}",
            "train: images/train",
            "val: images/val",
            "kpt_shape: [17, 3]",
            "names:",
            "  0: person",
            "",
        ]
    )
    (dataset_dir / "dataset.yaml").write_text(content, encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    source = Path(args.pseudo_labels_jsonl)
    dataset_dir = Path(args.dataset_dir)
    report_out = Path(args.report_out)
    if not source.exists():
        write_json(report_out, {"status": "failed", "reason": "pseudo_labels_missing", "training_started": False})
        return 2

    accepted = 0
    manual_review = 0
    rejected = 0
    rows = 0
    review_items: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows += 1
        record = json.loads(line)
        split = str(record.get("split", "train")).lower()
        if split not in {"train", "val"}:
            split = "train"
        pose_conf = float(record.get("pose_confidence_mean", 0.0))
        ratio = visible_joint_ratio(record)
        image_path = Path(record["image_path"])
        if not image_path.exists():
            rejected += 1
            review_items.append({"image_path": str(image_path), "reason": "image_missing"})
            continue

        needs_review = pose_conf < args.min_pose_confidence or ratio < args.min_visible_joint_ratio
        if needs_review:
            manual_review += 1
            review_items.append({"image_path": str(image_path), "pose_confidence_mean": pose_conf, "visible_joint_ratio": ratio})
        else:
            accepted += 1

        image_target = dataset_dir / "images" / split / image_path.name
        label_target = dataset_dir / "labels" / split / f"{image_path.stem}.txt"
        image_target.parent.mkdir(parents=True, exist_ok=True)
        label_target.parent.mkdir(parents=True, exist_ok=True)
        if args.copy_images:
            shutil.copy2(image_path, image_target)
        label_target.write_text(yolo_pose_line(record) + "\n", encoding="utf-8")

    write_dataset_yaml(dataset_dir)
    write_json(
        report_out,
        {
            "status": "built",
            "rows": rows,
            "accepted_frames": accepted,
            "manual_review_frames": manual_review,
            "rejected_frames": rejected,
            "manual_review_items": review_items[:100],
            "dataset_yaml": str(dataset_dir / "dataset.yaml"),
            "training_started": False,
        },
    )
    print(f"yolo-pose-dataset rows={rows} accepted={accepted} manual_review={manual_review} training_started=false")
    return 0 if rejected == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
