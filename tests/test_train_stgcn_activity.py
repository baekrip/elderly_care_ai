from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from device_transfer.Edge.shared.labels import TARGET_ACTION_LABELS
from tools.stgcn_activity_training import TrainingContractError, build_dry_run_report
from tools.stgcn_activity_model import MultiTaskSTGCN, train_one_run
from tools.train_stgcn_activity import detach_run_result
from tools.stgcn_sequence_input import RISK_LABELS


class StgcnActivityTrainerTests(unittest.TestCase):
    def test_saved_state_is_the_same_state_that_produced_reported_best_score(self) -> None:
        state = {"weight": torch.tensor([1.0])}
        result = {
            "best_val_combined_acc": 0.75,
            "history": [{"epoch": 1, "val_combined_acc": 0.75}],
            "best_state": state,
        }

        report, selected_state = detach_run_result(result, run_number=2, elapsed_seconds=1.25)

        self.assertIs(selected_state, state)
        self.assertEqual(report["best_val_combined_acc"], 0.75)
        self.assertNotIn("best_state", report)
        self.assertEqual(report["run"], 2)

    def test_exporter_shape_runs_through_model_and_keeps_checkpoint_state(self) -> None:
        features = torch.zeros((2, 4, 17, 3), dtype=torch.float32)
        activity = torch.tensor([0, 1], dtype=torch.long)
        risk = torch.tensor([0, 1], dtype=torch.long)
        loader = DataLoader(TensorDataset(features, activity, risk), batch_size=2)
        model = MultiTaskSTGCN(len(TARGET_ACTION_LABELS), len(RISK_LABELS))

        activity_logits, risk_logits = model(features)
        result = train_one_run(model, loader, loader, epochs=1, lr=0.001, device="cpu")

        self.assertEqual(tuple(activity_logits.shape), (2, len(TARGET_ACTION_LABELS)))
        self.assertEqual(tuple(risk_logits.shape), (2, len(RISK_LABELS)))
        self.assertIsNotNone(result["best_state"])

    def _write_dataset(self, root: Path) -> tuple[Path, Path]:
        sequences: list[np.ndarray] = []
        activity_labels: list[int] = []
        risk_labels: list[int] = []
        sample_ids: list[str] = []
        for label_index, _label in enumerate(TARGET_ACTION_LABELS):
            for group_index in range(2):
                sequences.append(np.full((4, 17, 3), label_index + group_index, dtype=np.float32))
                activity_labels.append(label_index)
                risk_labels.append(label_index % len(RISK_LABELS))
                sample_ids.append(f"source-{label_index}-{group_index}:0")
        npz_path = root / "sequences.npz"
        meta_path = root / "meta.json"
        np.savez_compressed(
            npz_path,
            sequences=np.asarray(sequences),
            activity_labels=np.asarray(activity_labels),
            risk_labels=np.asarray(risk_labels),
            sample_ids=np.asarray(sample_ids),
        )
        meta_path.write_text(
            json.dumps(
                {
                    "activity_label_names": list(TARGET_ACTION_LABELS),
                    "risk_label_names": list(RISK_LABELS),
                },
            ),
            encoding="utf-8",
        )
        return npz_path, meta_path

    def test_dry_run_enforces_canonical_labels_and_group_safe_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            npz_path, meta_path = self._write_dataset(Path(temporary))
            report = build_dry_run_report(npz_path, meta_path, seed=42, validation_fraction=0.2, minimum=2)

        self.assertEqual(report["activity_label_names"], list(TARGET_ACTION_LABELS))
        self.assertEqual(report["risk_label_names"], list(RISK_LABELS))
        self.assertEqual(report["group_overlap"], [])
        self.assertEqual(report["training_started"], False)
        self.assertEqual(report["ready_for_training"], True)

    def test_rejects_label_contract_shape_and_underfilled_classes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            npz_path, meta_path = self._write_dataset(root)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["risk_label_names"] = ["NORMAL", "SUSPECT", "DANGER"]
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            with self.assertRaisesRegex(TrainingContractError, "risk label contract"):
                build_dry_run_report(npz_path, meta_path, seed=42, validation_fraction=0.2, minimum=2)

            meta["risk_label_names"] = list(RISK_LABELS)
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
            with np.load(npz_path) as data:
                np.savez_compressed(
                    root / "bad.npz",
                    sequences=data["sequences"][:, :, :16, :],
                    activity_labels=data["activity_labels"],
                    risk_labels=data["risk_labels"],
                    sample_ids=data["sample_ids"],
                )
            with self.assertRaisesRegex(TrainingContractError, r"\[N,T,17,3\]"):
                build_dry_run_report(root / "bad.npz", meta_path, seed=42, validation_fraction=0.2, minimum=2)

    def test_cli_dry_run_writes_report_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            npz_path, meta_path = self._write_dataset(root)
            report_path = root / "report.json"
            model_path = root / "must-not-exist.pth"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.train_stgcn_activity",
                    "--input",
                    str(npz_path),
                    "--meta-in",
                    str(meta_path),
                    "--report-out",
                    str(report_path),
                    "--model-out",
                    str(model_path),
                    "--dry-run",
                    "--min-samples-per-class",
                    "2",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(report_path.is_file())
            self.assertFalse(model_path.exists())
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["training_started"], False)


if __name__ == "__main__":
    unittest.main()
