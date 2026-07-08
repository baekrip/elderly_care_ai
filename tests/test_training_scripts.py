from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TrainingScriptTests(unittest.TestCase):
    def test_train_stgcn_help_runs_from_repo_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "tools/train_stgcn.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Train Mini ST-GCN", result.stdout)

    def test_benchmark_pose_resolutions_help_runs_from_repo_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "tools/benchmark_pose_resolutions.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Benchmark pose inference", result.stdout)

    def test_pose_benchmark_wrapper_help_runs_from_repo_root(self) -> None:
        for script in ["tools/benchmark_pose_baseline.py", "tools/benchmark_pose_models.py"]:
            result = subprocess.run(
                [sys.executable, script, "--help"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Benchmark pose inference", result.stdout)

    def test_validate_label_quality_help_runs_from_repo_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "tools/validate_label_quality.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Validate behavior label quality", result.stdout)

    def test_run_training_cycle_help_runs_from_repo_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "tools/run_training_cycle.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Run repeatable training cycle", result.stdout)

    def test_training_batch_tool_help_runs_from_repo_root(self) -> None:
        expected = {
            "tools/register_training_batch.py": "Register a repeat-training data batch",
            "tools/merge_xgboost_feature_batches.py": "Merge XGBoost feature CSV batches",
            "tools/merge_stgcn_sequence_batches.py": "Merge ST-GCN NPZ sequence batches",
            "tools/extract_yolo_pose_pseudo_labels.py": "Extract YOLO pose pseudo-label JSONL",
            "tools/validate_yolo_pose_pseudo_labels.py": "Validate YOLO pose pseudo-label JSONL",
            "tools/build_yolo_pose_dataset.py": "Build a YOLO pose dataset",
            "tools/check_yolo_pose_dataset_gate.py": "Check YOLO pose dataset training gate",
            "tools/build_risk_event_label_sheet.py": "Build a risk event label review sheet",
        }
        for script, marker in expected.items():
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, script, "--help"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertIn(marker, result.stdout)


if __name__ == "__main__":
    unittest.main()
