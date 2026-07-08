from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class LabelObject:
    start_frame: float
    end_frame: float
    action_type: str
    action_name: str


@dataclass
class ManifestRecord:
    sample_id: str
    video_path: str
    label_path: str
    video_stem: str
    resource_name: str
    dataset_family: str
    scenario_group: str | None
    event_tier: str
    fps: float
    objects: list[LabelObject]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["objects"] = [asdict(item) for item in self.objects]
        return payload


def _normalize_family(path: Path, dataset_root: Path) -> str:
    relative = path.relative_to(dataset_root)
    if not relative.parts:
        return "unknown"
    family = relative.parts[0].lower()
    if "drop" in family or "fall" in family:
        return "abnormal_drop"
    if "wander" in family:
        return "abnormal_wander"
    if "dementia_daily_activity" in family or family == "daily_activity":
        return "daily_activity"
    return family


def _derive_tier(action_type: str, dataset_family: str) -> str:
    action_type_upper = action_type.upper()
    if action_type_upper == "ABNOR_H" or dataset_family == "abnormal_drop":
        return "DANGER"
    if action_type_upper == "ABNOR_W" or dataset_family == "abnormal_wander":
        return "SUSPECT"
    return "NORMAL"


def _parse_label_file(label_path: Path) -> tuple[str, float, list[LabelObject]]:
    raw = json.loads(label_path.read_text(encoding="utf-8"))
    annotations = raw.get("annotations", {})
    resource_name = str(annotations.get("resource", "")).strip()
    fps = float(annotations.get("fps", 0.0) or 0.0)
    objects: list[LabelObject] = []
    for item in annotations.get("object", []) or []:
        objects.append(
            LabelObject(
                start_frame=float(item.get("startFrame", 0.0) or 0.0),
                end_frame=float(item.get("endFrame", 0.0) or 0.0),
                action_type=str(item.get("actionType", "")).strip(),
                action_name=str(item.get("actionName", "")).strip(),
            )
        )
    return resource_name, fps, objects


def build_manifest(dataset_root: str | Path) -> list[ManifestRecord]:
    root = Path(dataset_root)
    video_paths = list(root.rglob("*.mp4"))
    label_paths = list(root.rglob("*.json"))
    videos_by_name = {path.name.lower(): path for path in video_paths}
    videos_by_stem = {path.stem.lower(): path for path in video_paths}

    manifest: list[ManifestRecord] = []
    for label_path in sorted(label_paths):
        resource_name, fps, objects = _parse_label_file(label_path)
        matched_video = None
        if resource_name:
            matched_video = videos_by_name.get(resource_name.lower())
        if matched_video is None:
            matched_video = videos_by_stem.get(label_path.stem.lower())
        if matched_video is None:
            continue

        dataset_family = _normalize_family(label_path, root)
        action_type = objects[0].action_type if objects else ""
        event_tier = _derive_tier(action_type, dataset_family)
        scenario_group = None
        try:
            relative_video = matched_video.relative_to(root)
            if len(relative_video.parts) >= 2:
                scenario_group = relative_video.parts[1] if dataset_family == "abnormal_drop" else relative_video.parts[1]
        except ValueError:
            scenario_group = matched_video.parent.name

        manifest.append(
            ManifestRecord(
                sample_id=label_path.stem,
                video_path=str(matched_video),
                label_path=str(label_path),
                video_stem=matched_video.stem,
                resource_name=resource_name or matched_video.name,
                dataset_family=dataset_family,
                scenario_group=scenario_group,
                event_tier=event_tier,
                fps=fps,
                objects=objects,
            )
        )
    return manifest


def summarize_manifest(records: list[ManifestRecord]) -> dict[str, Any]:
    family_counter = Counter(record.dataset_family for record in records)
    tier_counter = Counter(record.event_tier for record in records)
    action_type_counter = Counter(
        item.action_type
        for record in records
        for item in record.objects
        if item.action_type
    )
    action_name_counter = Counter(
        item.action_name
        for record in records
        for item in record.objects
        if item.action_name
    )
    return {
        "matched_pairs": len(records),
        "dataset_family_counts": dict(sorted(family_counter.items())),
        "tier_counts": dict(sorted(tier_counter.items())),
        "action_type_counts": dict(sorted(action_type_counter.items())),
        "action_name_counts": dict(sorted(action_name_counter.items())),
    }


def build_label_gap_report(records: list[ManifestRecord]) -> dict[str, Any]:
    summary = summarize_manifest(records)
    basic_behavior_labels = ["SLEEPING", "SITTING", "STANDING", "WALKING", "UNKNOWN"]
    direct_action_names = {item.action_name for record in records for item in record.objects if item.action_name}
    supports_basic_supervision = any(label in direct_action_names for label in basic_behavior_labels)

    return {
        "matched_pairs": len(records),
        "supports_tier_training": bool(records),
        "supports_basic_action_supervision": supports_basic_supervision,
        "observed_action_names": sorted(direct_action_names),
        "missing_basic_behavior_labels": [label for label in basic_behavior_labels if label not in direct_action_names],
        "recommendation": {
            "xgboost_now": "tier_classifier_normal_suspect_danger",
            "stgcn_now": "event_sequence_classifier_drop_wander_or_fall_binary",
            "basic_action_model": "daily_activity_present_but_needs_action_name_mapping_or_pseudo_labels",
        },
        "summary": summary,
    }


def build_xgboost_job_rows(
    records: list[ManifestRecord],
    normal_margin_frames: int = 90,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        for index, item in enumerate(record.objects):
            rows.append(
                {
                    "job_type": "event_window",
                    "sample_id": record.sample_id,
                    "video_path": record.video_path,
                    "label_path": record.label_path,
                    "event_tier": record.event_tier,
                    "dataset_family": record.dataset_family,
                    "action_type": item.action_type,
                    "action_name": item.action_name,
                    "start_frame": int(item.start_frame),
                    "end_frame": int(item.end_frame),
                    "window_role": "positive",
                    "job_index": index,
                }
            )
            if item.start_frame > normal_margin_frames:
                rows.append(
                    {
                        "job_type": "normal_window",
                        "sample_id": record.sample_id,
                        "video_path": record.video_path,
                        "label_path": record.label_path,
                        "event_tier": "NORMAL",
                        "dataset_family": record.dataset_family,
                        "action_type": "NORMAL_BASELINE",
                        "action_name": "NORMAL_WINDOW",
                        "start_frame": 0,
                        "end_frame": int(max(item.start_frame - normal_margin_frames, 0)),
                        "window_role": "negative_pre",
                        "job_index": index,
                    }
                )
    return rows


def build_stgcn_job_rows(records: list[ManifestRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        for index, item in enumerate(record.objects):
            rows.append(
                {
                    "sample_id": record.sample_id,
                    "video_path": record.video_path,
                    "label_path": record.label_path,
                    "dataset_family": record.dataset_family,
                    "event_tier": record.event_tier,
                    "action_type": item.action_type,
                    "action_name": item.action_name,
                    "start_frame": int(item.start_frame),
                    "end_frame": int(item.end_frame),
                    "fps": record.fps,
                    "sequence_label": item.action_name or item.action_type or record.event_tier,
                    "job_index": index,
                }
            )
            if item.start_frame > 120:
                rows.append(
                    {
                        "sample_id": record.sample_id,
                        "video_path": record.video_path,
                        "label_path": record.label_path,
                        "dataset_family": record.dataset_family,
                        "event_tier": "NORMAL",
                        "action_type": "NORMAL_BASELINE",
                        "action_name": "NORMAL_WINDOW",
                        "start_frame": 0,
                        "end_frame": int(max(item.start_frame - 120, 0)),
                        "fps": record.fps,
                        "sequence_label": "NORMAL",
                        "job_index": index,
                    }
                )
    return rows


def write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
