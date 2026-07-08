from __future__ import annotations

import unittest

from tools.remaining_plan_preflight import build_reconcile_report


class YoloBottleneckReconcileTests(unittest.TestCase):
    def test_duplicate_reference_modules_map_to_current_files(self) -> None:
        report = build_reconcile_report()
        mappings = {item["reference_file"]: item for item in report["mappings"]}

        split_worker = mappings["edge/split_pipeline_worker.py"]
        optimized_pose = mappings["edge/optimized_pose_estimator.py"]

        self.assertEqual("1.0", report["schema_version"])
        self.assertIn("edge/main.py", report["current_source_of_truth"])
        self.assertIn("edge/async_pose.py", report["current_source_of_truth"])
        self.assertIn("edge/pose_estimator.py", report["current_source_of_truth"])
        self.assertEqual("edge/async_pose.py", split_worker["current_file"])
        self.assertIn(split_worker["action"], {"not_required", "extend_existing"})
        self.assertNotEqual("new_required", split_worker["action"])
        self.assertEqual("edge/pose_estimator.py", optimized_pose["current_file"])
        self.assertEqual("extend_existing", optimized_pose["action"])
        self.assertNotEqual("new_required", optimized_pose["action"])


if __name__ == "__main__":
    unittest.main()
