from __future__ import annotations

import unittest

from tools.benchmark_pose_resolutions import (
    build_onnx_candidate_options,
    build_pose_benchmark_row,
    build_replay_decision_report,
    evaluate_pose_benchmark_gate,
)


class PoseBenchmarkReportTests(unittest.TestCase):
    def test_row_uses_plan_metric_names_and_p95_latency(self) -> None:
        row = build_pose_benchmark_row(
            candidate="baseline",
            resolution="640x360",
            frames=3,
            detected_frames=2,
            latencies_ms=[20.0, 30.0, 40.0],
            pose_confidences=[0.7, 0.9],
            visible_ratios=[0.5, 0.7],
            danger_recall=0.91,
            fp_per_hour=1.5,
        )

        self.assertEqual(row["candidate"], "baseline")
        self.assertEqual(row["pose_latency_p95_ms"], 40.0)
        self.assertEqual(row["pose_fps"], 33.3333)
        self.assertEqual(row["skeleton_confidence_mean"], 0.8)
        self.assertEqual(row["visible_joint_ratio"], 0.6)
        self.assertTrue(row["operational_gate"]["allowed"])

    def test_gate_blocks_low_recall_or_high_false_positive_rate(self) -> None:
        low_recall = evaluate_pose_benchmark_gate(danger_recall=0.89, fp_per_hour=1.0)
        high_fp = evaluate_pose_benchmark_gate(danger_recall=0.95, fp_per_hour=2.1)

        self.assertFalse(low_recall["allowed"])
        self.assertIn("DANGER recall", low_recall["reasons"][0])
        self.assertFalse(high_fp["allowed"])
        self.assertIn("FP/hour", high_fp["reasons"][0])

    def test_replay_decision_blocks_when_accuracy_or_stage_metrics_are_missing(self) -> None:
        report = build_replay_decision_report(
            candidates=[
                {
                    "candidate": "default",
                    "pose_latency_p95_ms": 40.0,
                    "pose_fps": 25.0,
                }
            ],
            stage_metrics_present=False,
        )

        self.assertFalse(report["required_metrics_present"])
        self.assertFalse(report["operational_gate"]["allowed"])
        self.assertIn("missing_stage_metrics", report["operational_gate"]["reasons"])
        self.assertIn("missing_DANGER_recall", report["operational_gate"]["reasons"])
        self.assertIn("missing_FP_per_hour", report["operational_gate"]["reasons"])

    def test_onnx_candidate_options_include_safe_thread_values_and_rollback_metadata(self) -> None:
        rows = build_onnx_candidate_options(["default", "threads1", "threads4_nospin"])

        self.assertEqual([row["candidate"] for row in rows], ["default", "threads1", "threads4_nospin"])
        self.assertEqual(rows[1]["options"]["intra_op_num_threads"], 1)
        self.assertEqual(rows[2]["options"]["session.intra_op.allow_spinning"], "0")
        for row in rows:
            self.assertIn("rollback", row)
            self.assertEqual(row["rollback"]["default_candidate"], "default")


if __name__ == "__main__":
    unittest.main()
