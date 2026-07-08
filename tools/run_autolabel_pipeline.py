from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from device_transfer.Edge.shared.training_dataset import ManifestRecord, build_manifest

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_DATASET: Final = ROOT.parent / "video" / "run"
DEFAULT_CONFIG: Final = ROOT / "device_transfer" / "camera1" / "edge" / "config.raspi_cam01.yaml"
DEFAULT_POLICY: Final = ROOT / "experiments" / "behavior_training" / "config" / "autolabel_policy.json"


class PipelineContractError(Exception):
    pass


def assert_candidate_path(path: Path, run_dir: Path) -> None:
    resolved = path.resolve()
    candidate_root = (run_dir / "candidates").resolve()
    try:
        resolved.relative_to(candidate_root)
    except ValueError as exc:
        raise PipelineContractError(f"model output must stay under run candidates: {resolved}") from exc


def _run(command: list[str]) -> None:
    print("[stage] " + subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def _paths(run_dir: Path) -> dict[str, Path]:
    return {
        "registry": run_dir / "manifest" / "batch_registry.jsonl",
        "frames": run_dir / "poses" / "frames",
        "raw_poses": run_dir / "poses" / "raw_pose_rows.jsonl",
        "accepted_poses": run_dir / "labels" / "accepted_poses.jsonl",
        "activity_frames": run_dir / "labels" / "activity_frames.jsonl",
        "static_registry": run_dir / "labels" / "activity_static_registry.jsonl",
        "rejected": run_dir / "labels" / "rejected_samples.jsonl",
        "pose_report": run_dir / "reports" / "pose_extraction.json",
        "label_report": run_dir / "reports" / "autolabel.json",
        "coarse_csv": run_dir / "features" / "coarse_action_features.csv",
        "coarse_npz": run_dir / "sequences" / "coarse_activity_sequences.npz",
        "coarse_meta": run_dir / "reports" / "coarse_distill_export.json",
        "coarse_dry": run_dir / "reports" / "coarse_distill_dry_run.json",
        "coarse_train": run_dir / "reports" / "coarse_distill_training.json",
        "candidate_dir": run_dir / "candidates",
        "pipeline_report": run_dir / "reports" / "pipeline_status.json",
    }


def _preflight(args: argparse.Namespace, run_dir: Path) -> None:
    required = (Path(args.dataset_root), Path(args.config), Path(args.policy))
    missing = [str(path.resolve()) for path in required if not path.exists()]
    if missing:
        raise PipelineContractError("missing required path(s): " + ", ".join(missing))
    if run_dir.exists() and any(run_dir.iterdir()):
        raise PipelineContractError(f"run directory is not empty: {run_dir}")
    assert_candidate_path(_paths(run_dir)["candidate_dir"] / "candidate.file", run_dir)


def select_balanced_records(records: list[ManifestRecord], limit: int) -> list[ManifestRecord]:
    if limit <= 0:
        raise PipelineContractError("max-samples must be positive")
    groups: dict[str, list[ManifestRecord]] = {}
    for record in records:
        action_type = record.objects[0].action_type if record.objects else "NO_ACTION"
        groups.setdefault(action_type, []).append(record)
    selected: list[ManifestRecord] = []
    offsets = {name: 0 for name in groups}
    while len(selected) < min(limit, len(records)):
        added = False
        for name in sorted(groups):
            offset = offsets[name]
            if offset < len(groups[name]) and len(selected) < limit:
                selected.append(groups[name][offset])
                offsets[name] += 1
                added = True
        if not added:
            break
    return selected


def _write_registry(dataset_root: Path, path: Path, max_samples: int | None) -> int:
    records = build_manifest(dataset_root)
    if max_samples is not None:
        records = select_balanced_records(records, max_samples)
    if not records:
        raise PipelineContractError("no matched MP4/JSON pairs found")
    samples = [
        {
            "sample_id": item.sample_id,
            "video_path": str(Path(item.video_path).resolve()),
            "label_path": str(Path(item.label_path).resolve()),
        }
        for item in records
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"batch_id": "autolabel", "samples": samples}, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(samples)


def _commands(args: argparse.Namespace, run_dir: Path) -> list[list[str]]:
    p = _paths(run_dir)
    python = sys.executable
    commands = [
        [python, "-m", "tools.extract_yolo_pose_pseudo_labels", "--registry", str(p["registry"]), "--frames-dir", str(p["frames"]), "--output-jsonl", str(p["raw_poses"]), "--report-out", str(p["pose_report"]), "--config", str(Path(args.config).resolve()), "--max-frames-per-sample", str(args.max_frames_per_sample), "--contiguous-frames"],
        [python, "-m", "tools.auto_label_generator", "--pose-jsonl", str(p["raw_poses"]), "--policy", str(Path(args.policy).resolve()), "--config", str(Path(args.config).resolve()), "--activity-frames-out", str(p["activity_frames"]), "--accepted-poses-out", str(p["accepted_poses"]), "--static-registry-out", str(p["static_registry"]), "--rejected-out", str(p["rejected"]), "--report-out", str(p["label_report"])],
        [python, "-m", "tools.export_coarse_distill_dataset", "--activity-frames", str(p["activity_frames"]), "--accepted-poses", str(p["accepted_poses"]), "--output-csv", str(p["coarse_csv"]), "--output-npz", str(p["coarse_npz"]), "--meta-out", str(p["coarse_meta"]), "--sequence-length", str(args.sequence_length)],
        [python, "-m", "tools.train_coarse_candidates", "--features-csv", str(p["coarse_csv"]), "--sequence-npz", str(p["coarse_npz"]), "--meta-json", str(p["coarse_meta"]), "--candidate-dir", str(p["candidate_dir"]), "--report-out", str(p["coarse_dry"]), "--min-samples-per-class", str(args.min_samples_per_class), "--dry-run"],
    ]
    if args.train_candidates:
        commands.append(
            [python, "-m", "tools.train_coarse_candidates", "--features-csv", str(p["coarse_csv"]), "--sequence-npz", str(p["coarse_npz"]), "--meta-json", str(p["coarse_meta"]), "--candidate-dir", str(p["candidate_dir"]), "--report-out", str(p["coarse_train"]), "--min-samples-per-class", str(args.min_samples_per_class), "--num-round", str(args.num_round), "--epochs", str(args.epochs)],
        )
    return commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed pseudo-label and candidate-training pipeline")
    parser.add_argument("command", nargs="?", choices=("auto-train",), default="auto-train")
    parser.add_argument("--profile", choices=("coarse-distill-v1",), default="coarse-distill-v1")
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--run-dir")
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--max-frames-per-sample", type=int, default=48)
    parser.add_argument("--sequence-length", type=int, default=24)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--num-round", type=int, default=100)
    parser.add_argument("--min-samples-per-class", type=int, default=2)
    parser.add_argument("--train-candidates", action="store_true")
    parser.add_argument("--plan", action="store_true", help="validate paths and print stages without writing or training")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.run_dir).resolve() if args.run_dir else (ROOT / "experiments" / "behavior_training" / "runs" / run_id)
    try:
        _preflight(args, run_dir)
        commands = _commands(args, run_dir)
        if args.plan:
            print(json.dumps({"status": "plan_valid", "run_dir": str(run_dir), "stages": [item[2] for item in commands]}, indent=2))
            return 0
        count = _write_registry(Path(args.dataset_root).resolve(), _paths(run_dir)["registry"], args.max_samples)
        print(f"manifest_samples={count} run_dir={run_dir}")
        completed_stages: list[str] = []
        for stage_index, command in enumerate(commands, 1):
            try:
                _run(command)
                completed_stages.append(command[2])
            except subprocess.CalledProcessError as exc:
                report = {
                    "status": "blocked", "failed_stage": command[2],
                    "stage_index": stage_index, "returncode": exc.returncode,
                    "completed_stages": completed_stages,
                    "training_started": "tools.train_coarse_candidates" in completed_stages and args.train_candidates,
                }
                report_path = _paths(run_dir)["pipeline_report"]
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                raise
        success = {
            "status": "candidate_trained_unvalidated_for_production" if args.train_candidates else "dry_run_passed",
            "profile": args.profile, "canonical_21": False,
            "completed_stages": completed_stages,
            "training_started": bool(args.train_candidates),
            "production_models_changed": False,
        }
        report_path = _paths(run_dir)["pipeline_report"]
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(success, indent=2) + "\n", encoding="utf-8")
    except (PipelineContractError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 11
    print("status=candidate_trained_unvalidated_for_production" if args.train_candidates else "status=dry_run_passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
