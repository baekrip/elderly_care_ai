from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from device_transfer.Edge.shared.labels import TARGET_ACTION_LABELS
from tools import atomic_artifact_pair
from tools import export_stgcn_activity_sequences as exporter


EXPECTED_RISK_LABELS = ("normal", "abnormal", "danger")


class STGCNActivitySequenceExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.input_jsonl = self.root / "poses.jsonl"
        self.output_npz = self.root / "sequences.npz"
        self.meta_out = self.root / "sequences.meta.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _keypoints(confidence: float = 0.9, valid_count: int = 17) -> list[list[float]]:
        return [
            [float(index), float(index * 2), confidence if index < valid_count else 0.1]
            for index in range(17)
        ]

    def _frame(
        self,
        sample_id: str,
        frame_index: int,
        activity_label: str = "standing",
        tier_label: str = "NORMAL",
        keypoints: list[list[float]] | None = None,
    ) -> dict[str, str | int | list[list[float]]]:
        return {
            "sample_id": sample_id,
            "frame_index": frame_index,
            "activity_label": activity_label,
            "tier_label": tier_label,
            "keypoints": keypoints if keypoints is not None else self._keypoints(),
        }

    def _write_rows(self, rows: list[dict[str, str | int | list[list[float]]]]) -> None:
        self.input_jsonl.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _export(self, sequence_length: int = 4, stride: int = 2) -> exporter.ExportMetadata:
        return exporter.export_activity_sequences(
            input_jsonl=self.input_jsonl,
            output_npz=self.output_npz,
            meta_out=self.meta_out,
            sequence_length=sequence_length,
            stride=stride,
        )

    def test_exports_dual_head_arrays_with_exact_label_contracts(self) -> None:
        rows = [self._frame("daily", index) for index in range(6)]
        rows.extend(
            self._frame("fall", index, "fall_confirmed", "FALL") for index in range(6)
        )
        self._write_rows(rows)

        metadata = self._export()

        with np.load(self.output_npz, allow_pickle=False) as payload:
            self.assertEqual(payload["sequences"].shape, (4, 4, 17, 3))
            self.assertEqual(payload["sequences"].dtype, np.float32)
            self.assertEqual(payload["activity_labels"].tolist(), [0, 0, 18, 18])
            self.assertEqual(payload["risk_labels"].tolist(), [0, 0, 2, 2])
            self.assertEqual(payload["sample_ids"].tolist(), ["daily:0", "daily:2", "fall:0", "fall:2"])
        meta = json.loads(self.meta_out.read_text(encoding="utf-8"))
        self.assertEqual(meta["activity_label_names"], list(TARGET_ACTION_LABELS))
        self.assertEqual(meta["risk_label_names"], list(EXPECTED_RISK_LABELS))
        self.assertEqual(meta["sequence_shape"], [4, 4, 17, 3])
        self.assertEqual(meta["sequence_length"], 4)
        self.assertEqual(meta["stride"], 2)
        self.assertFalse(meta["training_started"])
        self.assertEqual(metadata.exported_sequences, 4)

    def test_filters_low_quality_frames_and_reports_partial_windows(self) -> None:
        rows = [self._frame("valid", index) for index in range(4)]
        rows.extend(
            [
                self._frame("low-mean", 0, keypoints=self._keypoints(confidence=0.3)),
                self._frame(
                    "few-valid",
                    0,
                    keypoints=self._keypoints(confidence=0.9, valid_count=9),
                ),
            ],
        )
        rows.extend(self._frame("partial", index) for index in range(3))
        self._write_rows(rows)

        metadata = self._export()

        self.assertEqual(metadata.exported_sequences, 1)
        self.assertEqual(
            metadata.skipped_reasons,
            {
                "low_mean_pose_confidence": 1,
                "partial_window": 1,
                "too_few_valid_keypoints": 1,
            },
        )

    def test_does_not_compress_a_low_quality_frame_gap_into_a_sequence(self) -> None:
        rows = [self._frame("valid", index) for index in range(4)]
        rows.extend(
            self._frame(
                "gap",
                index,
                keypoints=(
                    self._keypoints(confidence=0.3)
                    if index == 2
                    else self._keypoints()
                ),
            )
            for index in range(5)
        )
        self._write_rows(rows)

        metadata = self._export()

        self.assertEqual(metadata.exported_sequences, 1)
        self.assertEqual(
            metadata.skipped_reasons,
            {"low_mean_pose_confidence": 1, "non_contiguous_window": 1},
        )

    def test_invalid_contract_preserves_stale_outputs(self) -> None:
        invalid_cases = (
            self._frame("unknown", 0, activity_label="not_a_label"),
            self._frame("joints", 0, keypoints=self._keypoints()[:-1]),
            self._frame("array", 0, keypoints=[[0.0, 1.0]] * 17),
        )
        for row in invalid_cases:
            with self.subTest(sample_id=row["sample_id"]):
                self.output_npz.write_bytes(b"old npz")
                self.meta_out.write_text("old meta", encoding="utf-8")
                self._write_rows([row])

                with self.assertRaises(exporter.SequenceContractError):
                    self._export()

                self.assertEqual(self.output_npz.read_bytes(), b"old npz")
                self.assertEqual(self.meta_out.read_text(encoding="utf-8"), "old meta")

    def test_keyboard_interrupt_during_second_replace_restores_both_outputs(self) -> None:
        self.output_npz.write_bytes(b"old npz")
        self.meta_out.write_bytes(b"old meta")
        real_replace = atomic_artifact_pair.os.replace
        replace_calls = 0

        def interrupt_second_replace(source: str | Path, target: str | Path) -> None:
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 2:
                raise KeyboardInterrupt
            real_replace(source, target)

        with mock.patch.object(
            atomic_artifact_pair.os,
            "replace",
            side_effect=interrupt_second_replace,
        ):
            with self.assertRaises(KeyboardInterrupt):
                atomic_artifact_pair.write_atomic_pair(
                    self.output_npz,
                    b"new npz",
                    self.meta_out,
                    b"new meta",
                )

        self.assertEqual(self.output_npz.read_bytes(), b"old npz")
        self.assertEqual(self.meta_out.read_bytes(), b"old meta")

    def test_duplicate_frame_and_empty_result_are_rejected(self) -> None:
        duplicate = self._frame("same", 0)
        self._write_rows([duplicate, duplicate])
        with self.assertRaisesRegex(exporter.SequenceContractError, "duplicate frame"):
            self._export()
        self._write_rows([self._frame("partial", index) for index in range(3)])
        with self.assertRaisesRegex(exporter.SequenceContractError, "no complete sequences"):
            self._export()

    def test_cli_help_lists_fixture_contract_and_output_paths(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.export_stgcn_activity_sequences", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("--input-jsonl", result.stdout)
        self.assertIn("--output-npz", result.stdout)
        self.assertIn("--meta-out", result.stdout)
        self.assertIn("--sequence-length", result.stdout)
        self.assertIn("--stride", result.stdout)


if __name__ == "__main__":
    unittest.main()
