from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repeatable training cycle with log capture")
    parser.add_argument("--mode", choices=["fall", "all"], default="fall")
    parser.add_argument("--log-dir", default="experiments/behavior_training/logs")
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--max-frames-per-job", type=int, default=8)
    parser.add_argument("--epochs-stgcn", type=int, default=12)
    return parser


def _python_executable() -> Path:
    return ROOT / ".venv_edge_local" / "Scripts" / "python.exe"


def _commands(mode: str, max_frames_per_job: int, epochs_stgcn: int) -> list[list[str]]:
    py = str(_python_executable())
    commands: list[list[str]] = []
    if mode == "fall":
        commands.extend(
            [
                [py, "tools/export_xgboost_tier_features.py", "--label-mode", "fall_binary", "--output-csv", "experiments/behavior_training/features/xgboost_fall_features.csv", "--max-frames-per-job", str(max_frames_per_job)],
                [py, "tools/train_xgboost_tier.py", "--features-csv", "experiments/behavior_training/features/xgboost_fall_features.csv", "--model-out", "edge/models/xgboost_fall_binary.json", "--meta-out", "experiments/behavior_training/reports/xgboost_fall_meta.json", "--label-column", "model_label"],
                [py, "tools/export_stgcn_sequences.py", "--label-mode", "fall_binary", "--include-labels", "NORMAL,DROP", "--output", "experiments/behavior_training/sequences/stgcn_sequences_fall.npz", "--meta-out", "experiments/behavior_training/reports/stgcn_sequence_meta_fall.json"],
                [py, "tools/train_stgcn.py", "--input", "experiments/behavior_training/sequences/stgcn_sequences_fall.npz", "--meta-in", "experiments/behavior_training/reports/stgcn_sequence_meta_fall.json", "--model-out", "server/models/stgcn_fall_binary.pth", "--report-out", "experiments/behavior_training/reports/stgcn_train_report_fall.json", "--epochs", str(epochs_stgcn), "--class-weight-mode", "inverse"],
            ]
        )
    else:
        commands.extend(
            [
                [py, "tools/export_xgboost_tier_features.py", "--output-csv", "experiments/behavior_training/features/xgboost_tier_features.csv", "--max-frames-per-job", str(max_frames_per_job)],
                [py, "tools/train_xgboost_tier.py", "--features-csv", "experiments/behavior_training/features/xgboost_tier_features.csv", "--model-out", "edge/models/xgboost_tier.json", "--meta-out", "experiments/behavior_training/reports/xgboost_tier_meta.json", "--label-column", "model_label"],
                [py, "tools/export_stgcn_sequences.py", "--label-mode", "multiclass", "--include-labels", "NORMAL,DROP,WANDER", "--output", "experiments/behavior_training/sequences/stgcn_sequences.npz", "--meta-out", "experiments/behavior_training/reports/stgcn_sequence_meta.json"],
                [py, "tools/train_stgcn.py", "--input", "experiments/behavior_training/sequences/stgcn_sequences.npz", "--meta-in", "experiments/behavior_training/reports/stgcn_sequence_meta.json", "--model-out", "server/models/stgcn_fall_local.pth", "--report-out", "experiments/behavior_training/reports/stgcn_train_report.json", "--epochs", str(epochs_stgcn), "--class-weight-mode", "inverse"],
            ]
        )
    return commands


def _run_command(command: list[str], log_file: Path) -> int:
    with log_file.open("a", encoding="utf-8") as file:
        file.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] RUN {' '.join(command)}\n")
        file.flush()
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            file.write(line)
        return process.wait()


def main() -> int:
    args = build_parser().parse_args()
    python_exe = _python_executable()
    if not python_exe.exists():
        print("missing .venv_edge_local\\Scripts\\python.exe")
        return 1

    log_dir = ROOT / args.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"train_cycle_{args.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    commands: list[list[str]] = []
    if not args.skip_prepare:
        commands.append([str(python_exe), "tools/run_behavior_training.py", "prepare"])
    commands.extend(_commands(args.mode, args.max_frames_per_job, args.epochs_stgcn))

    for command in commands:
        return_code = _run_command(command, log_file)
        if return_code != 0:
            print(f"training-cycle-failed log={log_file}")
            return return_code

    print(f"training-cycle-ok log={log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
