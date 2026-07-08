from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shared.training_dataset import (
    build_label_gap_report,
    build_manifest,
    build_stgcn_job_rows,
    build_xgboost_job_rows,
)
from tools.export_stgcn_sequences import resolve_label_name


class TrainingDatasetTests(unittest.TestCase):
    def test_manifest_matches_label_resource_to_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            video_dir = root / "abnormal_drop" / "H12H22H31"
            video_dir.mkdir(parents=True)
            label_dir = root / "abnormal_drop"
            video_path = video_dir / "FD_In_H12H22H31_0025_20201016_20.mp4"
            label_path = label_dir / "FD_In_H12H22H31_0025_20201016_20.json"
            video_path.write_bytes(b"")
            label_path.write_text(
                json.dumps(
                    {
                        "annotations": {
                            "resource": video_path.name,
                            "fps": 29.97,
                            "object": [
                                {
                                    "startFrame": 8053.7,
                                    "endFrame": 8100.2,
                                    "actionType": "ABNOR_H",
                                    "actionName": "H12H22H31",
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest = build_manifest(root)

            self.assertEqual(len(manifest), 1)
            self.assertEqual(Path(manifest[0].video_path), video_path)
            self.assertEqual(manifest[0].dataset_family, "abnormal_drop")
            self.assertEqual(manifest[0].event_tier, "DANGER")

    def test_gap_report_identifies_missing_basic_behavior_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            video_dir = root / "Abnormal_Behavior_Wander" / "W11W22"
            video_dir.mkdir(parents=True)
            label_dir = root / "Abnormal_Behavior_Wander"
            video_path = video_dir / "WD_in_W11W22_0003_20201023_14.mp4"
            label_path = label_dir / "WD_in_W11W22_0003_20201023_14.json"
            video_path.write_bytes(b"")
            label_path.write_text(
                json.dumps(
                    {
                        "annotations": {
                            "resource": video_path.name,
                            "fps": 29.97,
                            "object": [
                                {
                                    "startFrame": 1317.6,
                                    "endFrame": 6928.2,
                                    "actionType": "ABNOR_W",
                                    "actionName": "W11W22",
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest = build_manifest(root)
            gap_report = build_label_gap_report(manifest)
            xgboost_jobs = build_xgboost_job_rows(manifest, normal_margin_frames=90)
            stgcn_jobs = build_stgcn_job_rows(manifest)

            self.assertFalse(gap_report["supports_basic_action_supervision"])
            self.assertIn("SITTING", gap_report["missing_basic_behavior_labels"])
            self.assertTrue(any(item["event_tier"] == "NORMAL" for item in xgboost_jobs))
            labels = {item["sequence_label"] for item in stgcn_jobs}
            self.assertIn("W11W22", labels)
            self.assertIn("NORMAL", labels)

    def test_manifest_matches_daily_activity_label_to_video_as_normal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            video_dir = root / "Dementia_Daily_Activity" / "M_I_001"
            video_dir.mkdir(parents=True)
            label_dir = root / "Dementia_Daily_Activity"
            video_path = video_dir / "DDA_In_MI001_00001.mp4"
            label_path = label_dir / "DDA_In_MI001_00001.json"
            video_path.write_bytes(b"")
            label_path.write_text(
                json.dumps(
                    {
                        "annotations": {
                            "resource": video_path.name,
                            "fps": 29.97,
                            "object": [
                                {
                                    "startFrame": 120.0,
                                    "endFrame": 240.0,
                                    "actionType": "M_I_001",
                                    "actionName": None,
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest = build_manifest(root)

            self.assertEqual(len(manifest), 1)
            self.assertEqual(Path(manifest[0].video_path), video_path)
            self.assertEqual(manifest[0].dataset_family, "daily_activity")
            self.assertEqual(manifest[0].event_tier, "NORMAL")

    def test_fall_binary_label_mode_maps_wander_to_normal(self) -> None:
        wander_job = {
            "dataset_family": "abnormal_wander",
            "event_tier": "SUSPECT",
            "action_type": "ABNOR_W",
        }
        drop_job = {
            "dataset_family": "abnormal_drop",
            "event_tier": "DANGER",
            "action_type": "ABNOR_H",
        }
        daily_job = {
            "dataset_family": "daily_activity",
            "event_tier": "NORMAL",
            "action_type": "M_I_001",
        }

        self.assertEqual(resolve_label_name(wander_job, "fall_binary"), "NORMAL")
        self.assertEqual(resolve_label_name(drop_job, "fall_binary"), "DROP")
        self.assertEqual(resolve_label_name(daily_job, "fall_binary"), "NORMAL")


if __name__ == "__main__":
    unittest.main()
