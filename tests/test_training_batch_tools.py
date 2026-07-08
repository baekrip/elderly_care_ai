from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


class TrainingBatchToolTests(unittest.TestCase):
    def test_merge_xgboost_batches_rejects_duplicate_sample_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            first = tmp / "batch1.csv"
            second = tmp / "batch2.csv"
            for path in [first, second]:
                with path.open("w", encoding="utf-8", newline="") as file:
                    writer = csv.DictWriter(file, fieldnames=["sample_id", "model_label", "f_mean"])
                    writer.writeheader()
                    writer.writerow({"sample_id": "same", "model_label": "DROP", "f_mean": "1.0"})

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.merge_xgboost_feature_batches",
                    "--inputs",
                    str(first),
                    str(second),
                    "--output-csv",
                    str(tmp / "merged.csv"),
                    "--report-out",
                    str(tmp / "report.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            report = json.loads((tmp / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "failed")
            self.assertIn("same", report["duplicate_sample_ids"])

    def test_merge_stgcn_sequence_batches_merges_npz_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            first = tmp / "batch1.npz"
            second = tmp / "batch2.npz"
            first_meta = tmp / "batch1_meta.json"
            second_meta = tmp / "batch2_meta.json"
            np.savez_compressed(first, sequences=np.ones((1, 3, 24, 17, 1), dtype=np.float32), labels=np.array([0]))
            np.savez_compressed(second, sequences=np.zeros((1, 3, 24, 17, 1), dtype=np.float32), labels=np.array([1]))
            first_meta.write_text(json.dumps({"label_names": ["NORMAL", "DROP"]}), encoding="utf-8")
            second_meta.write_text(json.dumps({"label_names": ["NORMAL", "DROP"]}), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.merge_stgcn_sequence_batches",
                    "--inputs",
                    str(first),
                    str(second),
                    "--meta-inputs",
                    str(first_meta),
                    str(second_meta),
                    "--output",
                    str(tmp / "merged.npz"),
                    "--meta-out",
                    str(tmp / "meta.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            with np.load(tmp / "merged.npz") as merged:
                self.assertEqual(tuple(merged["sequences"].shape), (2, 3, 24, 17, 1))
                self.assertEqual(merged["labels"].tolist(), [0, 1])
            meta = json.loads((tmp / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["rows"], 2)
            self.assertEqual(meta["source_batches"], [str(first), str(second)])
            self.assertEqual(meta["label_names"], ["NORMAL", "DROP"])

    def test_merge_stgcn_sequence_batches_remaps_per_batch_label_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            normal_batch = tmp / "normal_batch.npz"
            drop_batch = tmp / "drop_batch.npz"
            normal_meta = tmp / "normal_meta.json"
            drop_meta = tmp / "drop_meta.json"
            np.savez_compressed(
                normal_batch,
                sequences=np.ones((1, 3, 24, 17, 1), dtype=np.float32),
                labels=np.array([0], dtype=np.int64),
            )
            np.savez_compressed(
                drop_batch,
                sequences=np.zeros((1, 3, 24, 17, 1), dtype=np.float32),
                labels=np.array([0], dtype=np.int64),
            )
            normal_meta.write_text(json.dumps({"label_names": ["NORMAL"]}), encoding="utf-8")
            drop_meta.write_text(json.dumps({"label_names": ["DROP"]}), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.merge_stgcn_sequence_batches",
                    "--inputs",
                    str(normal_batch),
                    str(drop_batch),
                    "--meta-inputs",
                    str(normal_meta),
                    str(drop_meta),
                    "--output",
                    str(tmp / "merged.npz"),
                    "--meta-out",
                    str(tmp / "meta.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            with np.load(tmp / "merged.npz") as merged:
                self.assertEqual(merged["labels"].tolist(), [0, 1])
                self.assertEqual(merged["label_names"].tolist(), ["NORMAL", "DROP"])
            meta = json.loads((tmp / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["label_names"], ["NORMAL", "DROP"])
            self.assertEqual(meta["label_counts"], {"NORMAL": 1, "DROP": 1})

    def test_build_yolo_pose_dataset_writes_labels_yaml_and_quality_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            image = tmp / "frame001.jpg"
            image.write_bytes(b"fake-image")
            pseudo = tmp / "pseudo.jsonl"
            pseudo.write_text(
                json.dumps(
                    {
                        "image_path": str(image),
                        "split": "train",
                        "width": 640,
                        "height": 360,
                        "bbox_xyxy": [100, 50, 300, 250],
                        "keypoints": [[10 + index, 20 + index, 0.9] for index in range(17)],
                        "pose_confidence_mean": 0.9,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.build_yolo_pose_dataset",
                    "--pseudo-labels-jsonl",
                    str(pseudo),
                    "--dataset-dir",
                    str(tmp / "dataset"),
                    "--report-out",
                    str(tmp / "quality.json"),
                    "--copy-images",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue((tmp / "dataset" / "dataset.yaml").exists())
            self.assertTrue((tmp / "dataset" / "labels" / "train" / "frame001.txt").exists())
            report = json.loads((tmp / "quality.json").read_text(encoding="utf-8"))
            self.assertEqual(report["accepted_frames"], 1)
            self.assertEqual(report["manual_review_frames"], 0)

    def test_extract_yolo_pose_pseudo_labels_from_registered_mp4_batch(self) -> None:
        import cv2

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            video = tmp / "sample_001.mp4"
            writer = cv2.VideoWriter(
                str(video),
                cv2.VideoWriter_fourcc(*"mp4v"),
                5.0,
                (64, 48),
            )
            for value in [20, 80, 140]:
                frame = np.full((48, 64, 3), value, dtype=np.uint8)
                writer.write(frame)
            writer.release()
            label = tmp / "sample_001.json"
            label.write_text(
                json.dumps(
                    {
                        "annotations": {
                            "resource": video.name,
                            "fps": 5,
                            "object": [
                                {
                                    "startFrame": 0,
                                    "endFrame": 2,
                                    "actionType": "ABNOR_H",
                                    "actionName": "H11H22",
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            registry = tmp / "registry.jsonl"
            registry.write_text(
                json.dumps(
                    {
                        "batch_id": "batch_test_001",
                        "samples": [
                            {
                                "sample_id": "sample_001",
                                "video_path": str(video),
                                "label_path": str(label),
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            detections = tmp / "pose_fixture.jsonl"
            rows = []
            for frame_index in [0, 2]:
                rows.append(
                    {
                        "sample_id": "sample_001",
                        "frame_index": frame_index,
                        "bbox_xyxy": [10, 5, 40, 35],
                        "keypoints": [[10 + index, 20 + index, 0.9] for index in range(17)],
                        "pose_confidence_mean": 0.9,
                    }
                )
            detections.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.extract_yolo_pose_pseudo_labels",
                    "--registry",
                    str(registry),
                    "--frames-dir",
                    str(tmp / "frames"),
                    "--output-jsonl",
                    str(tmp / "pseudo.jsonl"),
                    "--report-out",
                    str(tmp / "pseudo_report.json"),
                    "--pose-jsonl",
                    str(detections),
                    "--max-frames-per-sample",
                    "2",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            pseudo_rows = [
                json.loads(line)
                for line in (tmp / "pseudo.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(pseudo_rows), 2)
            self.assertTrue(Path(pseudo_rows[0]["image_path"]).exists())
            self.assertEqual(pseudo_rows[0]["width"], 64)
            self.assertEqual(pseudo_rows[0]["height"], 48)
            self.assertEqual(pseudo_rows[0]["bbox_xyxy"], [10, 5, 40, 35])
            report = json.loads((tmp / "pseudo_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "created")
            self.assertEqual(report["frames_written"], 2)
            self.assertEqual(report["pseudo_labels"], 2)
            self.assertFalse(report["training_started"])

    def test_validate_yolo_pose_pseudo_labels_accepts_reviewed_dataset_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            image = tmp / "frame001.jpg"
            image.write_bytes(b"fake-image")
            pseudo = tmp / "pseudo.jsonl"
            pseudo.write_text(
                json.dumps(
                    {
                        "image_path": str(image),
                        "width": 640,
                        "height": 360,
                        "bbox_xyxy": [100, 50, 300, 250],
                        "keypoints": [[10 + index, 20 + index, 0.9] for index in range(17)],
                        "pose_confidence_mean": 0.9,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            removed = tmp / "removed.jsonl"
            removed.write_text(
                json.dumps(
                    {
                        "image_path": str(tmp / "bad.jpg"),
                        "reason": "bbox_wrong",
                        "reviewed_at": "2026-06-06T17:53:00+09:00",
                        "reviewer_note": "bbox missed torso",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.validate_yolo_pose_pseudo_labels",
                    "--pseudo-labels-jsonl",
                    str(pseudo),
                    "--removed-jsonl",
                    str(removed),
                    "--report-out",
                    str(tmp / "validation.json"),
                    "--min-pose-confidence",
                    "0.6",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            report = json.loads((tmp / "validation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["dataset_build_allowed"])
            self.assertEqual(report["valid_rows"], 1)
            self.assertEqual(report["invalid_rows"], 0)

    def test_validate_yolo_pose_pseudo_labels_blocks_bad_rows_and_bad_removed_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pseudo = tmp / "pseudo.jsonl"
            pseudo.write_text(
                json.dumps(
                    {
                        "image_path": str(tmp / "missing.jpg"),
                        "width": 640,
                        "height": 360,
                        "bbox_xyxy": [500, 50, 700, 250],
                        "keypoints": [[10, 20, 0.9]],
                        "pose_confidence_mean": 0.2,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            removed = tmp / "removed.jsonl"
            removed.write_text(
                json.dumps(
                    {
                        "image_path": str(tmp / "bad.jpg"),
                        "reason": "not_allowed",
                        "reviewed_at": "2026-06-06T17:53:00+09:00",
                        "reviewer_note": "bad reason",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.validate_yolo_pose_pseudo_labels",
                    "--pseudo-labels-jsonl",
                    str(pseudo),
                    "--removed-jsonl",
                    str(removed),
                    "--report-out",
                    str(tmp / "validation.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            report = json.loads((tmp / "validation.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["dataset_build_allowed"])
            self.assertGreaterEqual(report["invalid_rows"], 1)
            self.assertIn("invalid_removed_reason", {item["reason"] for item in report["issues"]})

    def test_check_yolo_pose_dataset_gate_allows_ready_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dataset = tmp / "dataset"
            (dataset / "labels" / "train").mkdir(parents=True)
            (dataset / "labels" / "val").mkdir(parents=True)
            (dataset / "dataset.yaml").write_text("path: dataset\n", encoding="utf-8")
            for index in range(20):
                (dataset / "labels" / "train" / f"train_{index}.txt").write_text("0 0.5 0.5 0.5 0.5\n", encoding="utf-8")
            (dataset / "labels" / "val" / "val_0.txt").write_text("0 0.5 0.5 0.5 0.5\n", encoding="utf-8")
            quality = tmp / "quality.json"
            quality.write_text(
                json.dumps(
                    {
                        "status": "built",
                        "accepted_frames": 20,
                        "manual_review_frames": 0,
                        "rejected_frames": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.check_yolo_pose_dataset_gate",
                    "--quality-report",
                    str(quality),
                    "--dataset-dir",
                    str(dataset),
                    "--report-out",
                    str(tmp / "gate.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            report = json.loads((tmp / "gate.json").read_text(encoding="utf-8"))
            self.assertTrue(report["dataset_train_allowed"])
            self.assertEqual(report["status"], "passed")

    def test_check_yolo_pose_dataset_gate_blocks_bad_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dataset = tmp / "dataset"
            (dataset / "labels" / "train").mkdir(parents=True)
            (dataset / "labels" / "train" / "train_0.txt").write_text("0 0.5 0.5 0.5 0.5\n", encoding="utf-8")
            quality = tmp / "quality.json"
            quality.write_text(
                json.dumps(
                    {
                        "status": "built",
                        "accepted_frames": 19,
                        "manual_review_frames": 1,
                        "rejected_frames": 0,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.check_yolo_pose_dataset_gate",
                    "--quality-report",
                    str(quality),
                    "--dataset-dir",
                    str(dataset),
                    "--report-out",
                    str(tmp / "gate.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            report = json.loads((tmp / "gate.json").read_text(encoding="utf-8"))
            self.assertFalse(report["dataset_train_allowed"])
            self.assertEqual(report["status"], "failed")
            self.assertIn("manual_review_frames_not_zero", report["reasons"])
            self.assertIn("accepted_frames_below_minimum", report["reasons"])
            self.assertIn("dataset_yaml_missing", report["reasons"])
            self.assertIn("val_labels_missing", report["reasons"])

    def test_register_training_batch_writes_registry_without_starting_training(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            video = tmp / "sample.mp4"
            label = tmp / "sample.json"
            video.write_bytes(b"mp4")
            label.write_text("{}", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.register_training_batch",
                    "--source-dir",
                    str(tmp),
                    "--batch-id",
                    "batch_test_001",
                    "--registry",
                    str(tmp / "registry.jsonl"),
                    "--report-out",
                    str(tmp / "report.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            report = json.loads((tmp / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "registered")
            self.assertFalse(report["training_started"])
            self.assertEqual(report["matched_pairs"], 1)
            registry_line = json.loads((tmp / "registry.jsonl").read_text(encoding="utf-8").strip())
            self.assertEqual(registry_line["batch_id"], "batch_test_001")

    def test_build_risk_event_label_sheet_from_batch_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            registry = tmp / "registry.jsonl"
            registry.write_text(
                json.dumps(
                    {
                        "batch_id": "batch_test_001",
                        "samples": [
                            {
                                "sample_id": "sample_001",
                                "video_path": "videos/sample_001.mp4",
                                "label_path": "labels/sample_001.json",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.build_risk_event_label_sheet",
                    "--registry",
                    str(registry),
                    "--output-jsonl",
                    str(tmp / "risk_event_labels.jsonl"),
                    "--report-out",
                    str(tmp / "risk_event_label_report.json"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            row = json.loads((tmp / "risk_event_labels.jsonl").read_text(encoding="utf-8").strip())
            self.assertEqual(row["risk_label"], "needs_review")
            self.assertEqual(
                row["target_labels"],
                ["fall_detected", "running_over_speed", "collision_suspected", "faint_static"],
            )
            self.assertFalse(row["training_ready"])
            report = json.loads((tmp / "risk_event_label_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["rows"], 1)


if __name__ == "__main__":
    unittest.main()
