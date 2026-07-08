from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from device_transfer.Edge.edge.feature_extractor import FEATURE_COLUMNS
from tools.train_coarse_candidates import (
    CoarseTrainingError,
    TrainConfig,
    execute,
)


class TrainCoarseCandidatesTests(unittest.TestCase):
    def _fixture(self, root: Path, *, include_underfilled: bool = False) -> tuple[Path, Path, Path]:
        csv_path = root / "features.csv"
        npz_path = root / "sequences.npz"
        meta_path = root / "meta.json"
        labels = ("standing", "walking", "lying_rest") if include_underfilled else ("standing", "walking")
        rows: list[dict[str, str]] = []
        sample_ids: list[str] = []
        target_labels: list[int] = []
        sequences: list[np.ndarray] = []
        for label_index, label in enumerate(labels):
            sample_count = 1 if label == "lying_rest" else 4
            for index in range(sample_count):
                sample_id = f"{label}-video-{index}"
                row = {"sample_id": sample_id, "activity_label": label}
                row.update({name: str(float(label_index + index + column / 100)) for column, name in enumerate(FEATURE_COLUMNS)})
                rows.append(row)
                sample_ids.append(sample_id)
                target_labels.append(label_index)
                sequences.append(np.full((3, 4, 17, 1), label_index + index / 10, dtype=np.float32))
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("sample_id", "activity_label", *FEATURE_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
        np.savez_compressed(
            npz_path,
            sequences=np.stack(sequences),
            labels=np.asarray(target_labels, dtype=np.int64),
            sample_ids=np.asarray(sample_ids),
        )
        meta_path.write_text(json.dumps({
            "profile": "coarse-distill-v1", "canonical_21": False,
            "teacher_distillation": True, "label_names": list(labels),
            "label_counts": {label: (1 if label == "lying_rest" else 4) for label in labels},
            "samples": len(sample_ids),
        }), encoding="utf-8")
        return csv_path, npz_path, meta_path

    def test_dry_run_proves_group_safe_split_without_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path, npz_path, meta_path = self._fixture(root)
            report_path = root / "dry.json"
            candidate_dir = root / "candidates"
            report = execute(TrainConfig(
                csv_path, npz_path, meta_path, candidate_dir, report_path,
                dry_run=True, seed=42, validation_fraction=0.25,
                min_samples_per_class=2, num_round=2, epochs=1,
            ))
            self.assertTrue(report["ready_for_training"])
            self.assertEqual(report["group_overlap"], [])
            self.assertFalse(report["training_started"])
            self.assertFalse(candidate_dir.exists())

    def test_training_writes_reloadable_candidate_bundle_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path, npz_path, meta_path = self._fixture(root)
            candidate_dir = root / "candidates"
            report = execute(TrainConfig(
                csv_path, npz_path, meta_path, candidate_dir, root / "train.json",
                dry_run=False, seed=42, validation_fraction=0.25,
                min_samples_per_class=2, num_round=2, epochs=1,
            ))
            self.assertTrue((candidate_dir / "xgboost_coarse_action.json").is_file())
            self.assertTrue((candidate_dir / "stgcn_coarse_activity.pth").is_file())
            self.assertTrue(report["reload_verified"])
            self.assertEqual(set(report["candidate_sha256"]), {
                "xgboost_coarse_action.json", "stgcn_coarse_activity.pth",
            })
            with self.assertRaises(CoarseTrainingError):
                execute(TrainConfig(
                    csv_path, npz_path, meta_path, candidate_dir, root / "rerun.json",
                    dry_run=False, seed=42, validation_fraction=0.25,
                    min_samples_per_class=2, num_round=2, epochs=1,
                ))

    def test_dry_run_automatically_excludes_underfilled_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path, npz_path, meta_path = self._fixture(root, include_underfilled=True)
            report = execute(TrainConfig(
                csv_path, npz_path, meta_path, root / "candidates", root / "dry.json",
                dry_run=True, seed=42, validation_fraction=0.25,
                min_samples_per_class=2, num_round=2, epochs=1,
            ))
            self.assertEqual(report["label_names"], ["standing", "walking"])
            self.assertEqual(report["excluded_underfilled_labels"], ["lying_rest"])


if __name__ == "__main__":
    unittest.main()
