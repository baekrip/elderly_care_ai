from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from device_transfer.Edge.edge.feature_extractor import FEATURE_COLUMNS
from tools import export_xgboost_static_features as exporter
from tools import static_feature_export_io


EXPECTED_STATIC_LABELS = (
    "standing",
    "sitting",
    "lying_rest",
    "bending_candidate",
    "no_move_candidate",
    "unknown",
)


class StaticFeatureExporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.registry = self.root / "registry.jsonl"
        self.output_csv = self.root / "features.csv"
        self.meta_out = self.root / "features.meta.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _keypoints(confidence: float = 0.9, valid_count: int = 17) -> list[list[float]]:
        return [
            [float(index + 10), float(index * 2 + 20), confidence if index < valid_count else 0.1]
            for index in range(17)
        ]

    def _write_jsonl(self, path: Path, rows: list[dict[str, str | int | list[int] | list[list[float]]]]) -> None:
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _write_registry(self, rows: list[dict[str, str]]) -> None:
        self.registry.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _export(self) -> exporter.ExportMetadata:
        return exporter.export_static_features(
            registry_path=self.registry,
            output_csv=self.output_csv,
            meta_out=self.meta_out,
        )

    def test_exports_shared_static_labels_and_feature_schema(self) -> None:
        pose_path = self.root / "poses.jsonl"
        self._write_jsonl(
            pose_path,
            [
                {
                    "sample_id": f"sample-{index}",
                    "frame_index": index,
                    "timestamp_ms": index * 100,
                    "bbox_xyxy": [10, 10, 90, 190],
                    "frame_shape": [200, 100, 3],
                    "keypoints": self._keypoints(),
                }
                for index in range(len(EXPECTED_STATIC_LABELS))
            ],
        )
        self._write_registry(
            [
                {
                    "sample_id": f"sample-{index}",
                    "pose_jsonl": pose_path.name,
                    "activity_label": label,
                }
                for index, label in enumerate(EXPECTED_STATIC_LABELS)
            ],
        )

        metadata = self._export()

        with self.output_csv.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        self.assertEqual(reader.fieldnames, ["sample_id", "activity_label", *FEATURE_COLUMNS])
        self.assertEqual([row["activity_label"] for row in rows], list(EXPECTED_STATIC_LABELS))
        self.assertEqual(metadata.label_names, EXPECTED_STATIC_LABELS)
        self.assertEqual(metadata.feature_names, tuple(FEATURE_COLUMNS))
        self.assertEqual(metadata.label_counts, {label: 1 for label in EXPECTED_STATIC_LABELS})
        self.assertEqual(metadata.skipped_reasons, {})

    def test_filters_low_quality_frames_and_reports_reasons(self) -> None:
        pose_path = self.root / "poses.jsonl"
        self._write_jsonl(
            pose_path,
            [
                {
                    "sample_id": "valid-ten",
                    "bbox_xyxy": [10, 10, 90, 190],
                    "frame_shape": [200, 100, 3],
                    "keypoints": self._keypoints(confidence=0.6, valid_count=10),
                },
                {
                    "sample_id": "low-mean",
                    "bbox_xyxy": [10, 10, 90, 190],
                    "frame_shape": [200, 100, 3],
                    "keypoints": self._keypoints(confidence=0.3),
                },
                {
                    "sample_id": "few-valid",
                    "bbox_xyxy": [10, 10, 90, 190],
                    "frame_shape": [200, 100, 3],
                    "keypoints": self._keypoints(confidence=0.6, valid_count=9),
                },
            ],
        )
        self._write_registry(
            [
                {"sample_id": sample_id, "pose_jsonl": pose_path.name, "activity_label": "standing"}
                for sample_id in ("valid-ten", "low-mean", "few-valid")
            ],
        )

        metadata = self._export()

        self.assertEqual(metadata.exported_rows, 1)
        self.assertEqual(
            metadata.skipped_reasons,
            {"low_mean_pose_confidence": 1, "too_few_valid_keypoints": 1},
        )

    def test_invalid_registry_contract_preserves_existing_outputs(self) -> None:
        self.output_csv.write_text("old csv", encoding="utf-8")
        self.meta_out.write_text("old meta", encoding="utf-8")
        self._write_registry(
            [
                {"sample_id": "duplicate", "pose_jsonl": "poses.jsonl", "activity_label": "standing"},
                {"sample_id": "duplicate", "pose_jsonl": "poses.jsonl", "activity_label": "unknown_label"},
            ],
        )

        with self.assertRaises(exporter.RegistryContractError):
            self._export()

        self.assertEqual(self.output_csv.read_text(encoding="utf-8"), "old csv")
        self.assertEqual(self.meta_out.read_text(encoding="utf-8"), "old meta")

    def test_path_escape_is_rejected(self) -> None:
        self._write_registry(
            [{"sample_id": "escape", "pose_jsonl": "../outside.jsonl", "activity_label": "standing"}],
        )

        with self.assertRaises(exporter.RegistryContractError):
            self._export()

    def test_atomic_output_rolls_back_after_second_replace_failure(self) -> None:
        pose_path = self.root / "poses.jsonl"
        self._write_jsonl(
            pose_path,
            [
                {
                    "sample_id": "sample",
                    "bbox_xyxy": [10, 10, 90, 190],
                    "frame_shape": [200, 100, 3],
                    "keypoints": self._keypoints(),
                },
            ],
        )
        self._write_registry(
            [{"sample_id": "sample", "pose_jsonl": pose_path.name, "activity_label": "standing"}],
        )
        self.output_csv.write_text("old csv", encoding="utf-8")
        self.meta_out.write_text("old meta", encoding="utf-8")
        real_replace = static_feature_export_io.os.replace
        replace_calls = 0

        def fail_second_replace(source: str | Path, target: str | Path) -> None:
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls == 2:
                raise OSError("simulated interruption")
            real_replace(source, target)

        with mock.patch.object(static_feature_export_io.os, "replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                self._export()

        self.assertEqual(self.output_csv.read_text(encoding="utf-8"), "old csv")
        self.assertEqual(self.meta_out.read_text(encoding="utf-8"), "old meta")

    def test_cli_help_lists_explicit_output_paths(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.export_xgboost_static_features", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("--registry", result.stdout)
        self.assertIn("--output-csv", result.stdout)
        self.assertIn("--meta-out", result.stdout)

    def test_accepts_windows_utf8_bom_registry(self) -> None:
        pose_path = self.root / "poses.jsonl"
        self._write_jsonl(
            pose_path,
            [
                {
                    "sample_id": "bom-sample",
                    "bbox_xyxy": [10, 10, 90, 190],
                    "frame_shape": [200, 100, 3],
                    "keypoints": self._keypoints(),
                },
            ],
        )
        record = {
            "sample_id": "bom-sample",
            "pose_jsonl": pose_path.name,
            "activity_label": "standing",
        }
        self.registry.write_text(json.dumps(record) + "\n", encoding="utf-8-sig")

        metadata = self._export()

        self.assertEqual(metadata.exported_rows, 1)


if __name__ == "__main__":
    unittest.main()
