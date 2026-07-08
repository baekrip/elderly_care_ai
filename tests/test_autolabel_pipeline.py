from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from device_transfer.Edge.shared.training_dataset import LabelObject, ManifestRecord
from tools.run_autolabel_pipeline import (
    PipelineContractError,
    assert_candidate_path,
    select_balanced_records,
)


class AutoLabelPipelineTests(unittest.TestCase):
    @staticmethod
    def _record(sample_id: str, action_type: str) -> ManifestRecord:
        return ManifestRecord(
            sample_id=sample_id, video_path=f"{sample_id}.mp4", label_path=f"{sample_id}.json",
            video_stem=sample_id, resource_name=f"{sample_id}.mp4", dataset_family="fixture",
            scenario_group=None, event_tier="NORMAL", fps=30.0,
            objects=[LabelObject(0.0, 30.0, action_type, "")],
        )

    def test_limited_smoke_selection_balances_source_action_types(self) -> None:
        records = [
            self._record(f"normal-{index}", "M_I_001") for index in range(5)
        ] + [
            self._record(f"danger-{index}", "ABNOR_H") for index in range(5)
        ]
        selected = select_balanced_records(records, 4)
        selected_types = [record.objects[0].action_type for record in selected]
        self.assertEqual(selected_types.count("M_I_001"), 2)
        self.assertEqual(selected_types.count("ABNOR_H"), 2)

    def test_candidate_guard_accepts_only_run_candidate_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir).resolve()
            accepted = run_dir / "candidates" / "model.json"
            assert_candidate_path(accepted, run_dir)
            for rejected in (
                run_dir / "model.json",
                Path("edge/models/model.json").resolve(),
                Path("device_transfer/Edge/server/models/model.pth").resolve(),
            ):
                with self.subTest(path=rejected):
                    with self.assertRaises(PipelineContractError):
                        assert_candidate_path(rejected, run_dir)

    def test_cli_help_and_plan_are_runnable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        help_result = subprocess.run(
            [sys.executable, "-m", "tools.run_autolabel_pipeline", "--help"],
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(help_result.returncode, 0, msg=help_result.stderr)
        self.assertIn("--train-candidates", help_result.stdout)
        self.assertIn("--plan", help_result.stdout)
        self.assertIn("--profile", help_result.stdout)

    def test_pipeline_uses_coarse_distill_contract_not_impossible_6_or_21_class_trainers(self) -> None:
        source = Path("tools/run_autolabel_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("tools.export_coarse_distill_dataset", source)
        self.assertIn("tools.train_coarse_candidates", source)
        self.assertNotIn("tools.train_xgboost_static_posture", source)
        self.assertNotIn("tools.train_stgcn_activity", source)


if __name__ == "__main__":
    unittest.main()
