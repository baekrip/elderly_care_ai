from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.grid_search_fusion_weights import evaluate_combination


ROOT = Path(__file__).resolve().parents[1]


class FusionWeightGridSearchTests(unittest.TestCase):
    def test_evaluate_combination_reports_confusion_metrics(self) -> None:
        events = [
            {
                "ground_truth": "DANGER",
                "xgboost_label": "FALL",
                "xgboost_probability": 0.6,
                "stgcn_label": "FALL",
                "stgcn_probability": 0.8,
            },
            {
                "ground_truth": "NORMAL",
                "xgboost_label": "NORMAL",
                "xgboost_probability": 0.2,
                "stgcn_label": "NORMAL",
                "stgcn_probability": 0.1,
            },
        ]

        result = evaluate_combination(events, stgcn_weight=0.6, decision_threshold=0.7)

        self.assertEqual(result["tp"], 1)
        self.assertEqual(result["tn"], 1)
        self.assertEqual(result["fp"], 0)
        self.assertEqual(result["fn"], 0)
        self.assertEqual(result["recall"], 1.0)
        self.assertEqual(result["precision"], 1.0)
        self.assertEqual(result["fpr"], 0.0)

    def test_cli_writes_report_with_best_filtered_result(self) -> None:
        rows = [
            {
                "ground_truth": "DANGER",
                "xgboost_label": "FALL",
                "xgboost_probability": 0.0,
                "stgcn_label": "FALL",
                "stgcn_probability": 0.9,
            },
            {
                "ground_truth": "NORMAL",
                "xgboost_label": "NORMAL",
                "xgboost_probability": 0.0,
                "stgcn_label": "NORMAL",
                "stgcn_probability": 0.0,
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            eval_path = Path(tmpdir) / "eval.jsonl"
            report_path = Path(tmpdir) / "report.json"
            eval_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "grid_search_fusion_weights.py"),
                    "--eval-data",
                    str(eval_path),
                    "--stgcn-weights",
                    "0.2",
                    "0.8",
                    "--decision-thresholds",
                    "0.5",
                    "0.8",
                    "--max-fpr",
                    "0.0",
                    "--report",
                    str(report_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["total_events"], 2)
        self.assertEqual(report["danger_ground_truth"], 1)
        self.assertEqual(report["normal_ground_truth"], 1)
        self.assertEqual(report["best"]["stgcn_weight"], 0.8)
        self.assertEqual(report["best"]["decision_threshold"], 0.5)
        self.assertEqual(report["best"]["recall"], 1.0)
        self.assertEqual(report["best"]["fpr"], 0.0)


if __name__ == "__main__":
    unittest.main()
