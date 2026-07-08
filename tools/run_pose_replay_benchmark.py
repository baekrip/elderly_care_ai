from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pose_replay_benchmark import (
    SelectedVideo,
    VideoResolutionResult,
    select_replay_videos,
    summarize_by_resolution,
)
from tools.benchmark_pose_resolutions import build_replay_decision_report
from tools.pose_replay_processing import benchmark_selected_videos
from tools.pose_replay_processing import apply_pose_overrides


DEFAULT_VIDEO_ROOT = Path(r"C:\Users\jju03\Desktop\university\program development\video\run")
DEFAULT_REPORT = Path("experiments/behavior_training/reports/pose_replay_benchmark_20260602.json")
DEFAULT_SAMPLES = Path("experiments/behavior_training/reports/pose_replay_samples_20260602")
DEFAULT_PREFLIGHT = Path("reports/remaining_plan/preflight.json")


def parse_resolutions(raw: str) -> tuple[tuple[int, int], ...]:
    values: list[tuple[int, int]] = []
    for item in raw.split(","):
        normalized = item.strip().lower()
        if normalized:
            width, height = normalized.split("x", 1)
            values.append((int(width), int(height)))
    return tuple(values)


def write_report(
    *,
    selected: tuple[SelectedVideo, ...],
    results: tuple[VideoResolutionResult, ...],
    report_out: Path,
    seed: int,
) -> None:
    payload = {
        "schema_version": "pose-replay-benchmark-v1",
        "seed": seed,
        "selected_videos": [asdict(video) | {"path": str(video.path)} for video in selected],
        "results": [asdict(result) for result in results],
        "summary": [asdict(summary) for summary in summarize_by_resolution(results)],
    }
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_blocked_replay_report(*, reason: str) -> dict[str, object]:
    report = build_replay_decision_report(candidates=[], stage_metrics_present=False)
    report.update(
        {
            "status": "blocked",
            "blocked_reason": reason,
            "promotion_allowed": False,
            "live_action_attempted": False,
        }
    )
    return report


def preflight_blocked_reason(preflight_path: Path = DEFAULT_PREFLIGHT) -> str | None:
    if not preflight_path.exists():
        return None
    payload = json.loads(preflight_path.read_text(encoding="utf-8"))
    can_run = payload.get("can_run") if isinstance(payload, dict) else None
    if not isinstance(can_run, dict) or can_run.get("pose_replay_gate", True):
        return None
    blocked_reasons = payload.get("blocked_reasons", {})
    if isinstance(blocked_reasons, dict):
        reason = blocked_reasons.get("pose_replay_gate")
        if reason:
            return str(reason)
    return "pose replay gate is blocked by preflight"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full-video pose replay benchmark")
    parser.add_argument("--video-root", type=Path, default=DEFAULT_VIDEO_ROOT)
    parser.add_argument("--config", type=Path, default=Path("edge/config.yaml"))
    parser.add_argument("--seed", type=int, default=20250602)
    parser.add_argument("--per-label", type=int, default=2)
    parser.add_argument("--resolutions", default="640x360,320x180")
    parser.add_argument("--samples-dir", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--pose-model-path", type=Path, default=None)
    parser.add_argument("--pose-backend", default=None)
    parser.add_argument("--pose-imgsz", type=int, default=None)
    parser.add_argument("--pose-conf-threshold", type=float, default=None)
    parser.add_argument("--ignore-preflight", action="store_true")
    args = parser.parse_args()

    if not args.ignore_preflight and (reason := preflight_blocked_reason()):
        report = build_blocked_replay_report(reason=reason)
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report_out": str(args.report_out), "status": "blocked"}, ensure_ascii=False))
        return 0

    try:
        selected = select_replay_videos(root=args.video_root, seed=args.seed, per_label=args.per_label)
    except FileNotFoundError as exc:
        report = build_blocked_replay_report(reason=str(exc))
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report_out": str(args.report_out), "status": "blocked"}, ensure_ascii=False))
        return 0
    results = benchmark_selected_videos(
        selected=selected,
        config_path=args.config,
        resolutions=parse_resolutions(args.resolutions),
        samples_dir=args.samples_dir,
        pose_model_path=args.pose_model_path,
        pose_backend=args.pose_backend,
        pose_imgsz=args.pose_imgsz,
        pose_conf_threshold=args.pose_conf_threshold,
    )
    write_report(selected=selected, results=results, report_out=args.report_out, seed=args.seed)
    print(json.dumps({"report_out": str(args.report_out), "results": len(results)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
