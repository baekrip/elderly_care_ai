from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.training_dataset import (
    build_label_gap_report,
    build_manifest,
    build_stgcn_job_rows,
    build_xgboost_job_rows,
    summarize_manifest,
    write_json,
    write_jsonl,
)


def _default_output_root() -> Path:
    return Path("experiments/behavior_training")


def _build_label_gap_markdown(report: dict) -> str:
    observed = ", ".join(report.get("observed_action_names", [])) or "(없음)"
    missing = ", ".join(report.get("missing_basic_behavior_labels", [])) or "(없음)"
    summary = report.get("summary", {})
    family_counts = summary.get("dataset_family_counts", {})
    tier_counts = summary.get("tier_counts", {})

    family_lines = "\n".join(f"- `{key}`: {value}" for key, value in family_counts.items()) or "- (없음)"
    tier_lines = "\n".join(f"- `{key}`: {value}" for key, value in tier_counts.items()) or "- (없음)"

    return "\n".join(
        [
            "# 라벨 갭 리포트",
            "",
            f"- 매칭된 쌍: `{report.get('matched_pairs', 0)}`",
            f"- tier 학습 가능 여부: `{report.get('supports_tier_training', False)}`",
            f"- 기본 행동 지도학습 가능 여부: `{report.get('supports_basic_action_supervision', False)}`",
            "",
            "## 현재 데이터셋 분포",
            family_lines,
            "",
            "## tier 분포",
            tier_lines,
            "",
            "## 직접 관측된 action_name",
            f"- {observed}",
            "",
            "## 현재 없는 기본 행동 라벨",
            f"- {missing}",
            "",
            "## 권장 해석",
            "- 현재 `video\\run` 데이터는 `낙상(drop)`과 `배회(wander)` 중심이라 `NORMAL/SUSPECT/DANGER` tier 학습과 `ST-GCN event sequence` 학습에는 바로 쓸 수 있다.",
            "- 현재 데이터만으로 `SLEEPING / SITTING / STANDING / WALKING / UNKNOWN`를 직접 지도학습하는 것은 어렵다.",
            "- 기본 행동 분류는 별도 ADL 라벨셋 추가 또는 pseudo-label bootstrap이 필요하다.",
        ]
    )


def command_prepare(args: argparse.Namespace) -> int:
    dataset_root = Path(args.dataset_root)
    output_root = Path(args.output_root)
    manifest = build_manifest(dataset_root)
    summary = summarize_manifest(manifest)
    gap_report = build_label_gap_report(manifest)
    xgboost_jobs = build_xgboost_job_rows(manifest, normal_margin_frames=args.normal_margin_frames)
    stgcn_jobs = build_stgcn_job_rows(manifest)

    write_json(output_root / "manifest" / "manifest.json", [record.to_dict() for record in manifest])
    write_json(output_root / "reports" / "dataset_summary.json", summary)
    write_json(output_root / "reports" / "label_gap_report.json", gap_report)
    write_jsonl(output_root / "jobs" / "xgboost_jobs.jsonl", xgboost_jobs)
    write_jsonl(output_root / "jobs" / "stgcn_jobs.jsonl", stgcn_jobs)
    (output_root / "reports" / "label_gap_report.md").write_text(
        _build_label_gap_markdown(gap_report),
        encoding="utf-8",
    )
    print(f"prepared manifest={len(manifest)} xgboost_jobs={len(xgboost_jobs)} stgcn_jobs={len(stgcn_jobs)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Behavior training preparation runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Build manifest and training job files")
    prepare.add_argument(
        "--dataset-root",
        default=r"..\video\run",
        help="Dataset root containing mp4/json files",
    )
    prepare.add_argument(
        "--output-root",
        default=str(_default_output_root()),
        help="Output directory for manifest, reports, and jobs",
    )
    prepare.add_argument(
        "--normal-margin-frames",
        type=int,
        default=90,
        help="Margin before event window treated as normal candidate",
    )
    prepare.set_defaults(func=command_prepare)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
