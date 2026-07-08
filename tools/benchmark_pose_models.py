from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.benchmark_pose_resolutions import build_onnx_candidate_options, build_replay_decision_report


def _parse_candidates(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_blocked_candidate_report(*, candidates: list[str], reason: str) -> dict[str, object]:
    rows = build_onnx_candidate_options(candidates)
    candidate_rows: list[dict[str, object]] = []
    for row in rows:
        candidate_rows.append(
            {
                **row,
                "pose_latency_p95_ms": None,
                "pose_fps": None,
                "DANGER_recall": None,
                "FP_per_hour": None,
                "status": "blocked",
                "blocked_reason": reason,
            }
        )
    report = build_replay_decision_report(candidates=candidate_rows, stage_metrics_present=False)
    report["status"] = "blocked"
    report["reason"] = reason
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark pose inference candidates with safe blocked reports."
    )
    parser.add_argument("--config", default="edge/config.yaml")
    parser.add_argument("--video", default=r"C:\Users\jju03\Downloads\test.mp4")
    parser.add_argument("--candidates", default="default,threads1,threads2,threads4,threads4_nospin")
    parser.add_argument("--warmup-frames", type=int, default=10)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--report-out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    candidates = _parse_candidates(args.candidates)
    video_path = Path(args.video)
    if not video_path.exists():
        report = build_blocked_candidate_report(
            candidates=candidates,
            reason=f"benchmark video not found: {video_path}",
        )
    else:
        report = build_blocked_candidate_report(
            candidates=candidates,
            reason="candidate execution requires replay accuracy labels and stage metrics before promotion",
        )
    report["config"] = str(args.config)
    report["video"] = str(video_path)
    report["warmup_frames"] = int(args.warmup_frames)
    report["frames"] = int(args.frames)
    report_path = Path(args.report_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report_out": str(report_path), "status": report["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
