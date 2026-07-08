from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from device_transfer.Edge.edge.feature_extractor import FEATURE_COLUMNS
from device_transfer.Edge.shared.labels import STATIC_POSTURE_LABELS
from tools import train_xgboost_static_posture as trainer


class StaticPostureTrainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.features_csv = self.root / "features.csv"
        self.report_out = self.root / "report.json"
        self.model_out = self.root / "model.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _rows(self, samples_per_label: int = 3) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for label_index, label in enumerate(STATIC_POSTURE_LABELS):
            for sample_index in range(samples_per_label):
                row = {
                    "sample_id": f"{label_index:02d}-{sample_index:02d}",
                    "activity_label": label,
                }
                row.update(
                    {
                        feature: str(label_index * 100 + sample_index * 10 + feature_index)
                        for feature_index, feature in enumerate(FEATURE_COLUMNS)
                    },
                )
                rows.append(row)
        return rows

    def _write_csv(
        self,
        rows: list[dict[str, str]],
        fieldnames: list[str] | None = None,
    ) -> None:
        columns = fieldnames or ["sample_id", "activity_label", *FEATURE_COLUMNS]
        with self.features_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def _config(self) -> trainer.TrainConfig:
        return trainer.TrainConfig(
            features_csv=self.features_csv,
            report_out=self.report_out,
            model_out=None,
            dry_run=True,
            seed=19,
            validation_fraction=0.34,
            min_samples_per_class=2,
            num_round=5,
            max_depth=2,
            eta=0.1,
        )

    def test_dry_run_is_deterministic_and_writes_no_model(self) -> None:
        self._write_csv(list(reversed(self._rows())))
        config = replace(self._config(), model_out=self.model_out)

        with mock.patch.dict(sys.modules, {"xgboost": None}):
            first = trainer.execute(config)
            second = trainer.execute(config)

        self.assertEqual(first["split"], second["split"])
        self.assertFalse(self.model_out.exists())
        self.assertEqual(first["label_order"], list(STATIC_POSTURE_LABELS))
        self.assertEqual(first["mode"], "dry_run")
        self.assertIsNone(first["valid_accuracy"])
        self.assertIsNone(first["confusion_matrix"])
        self.assertEqual(first, json.loads(self.report_out.read_text(encoding="utf-8")))

    def test_split_contains_every_label_in_both_partitions(self) -> None:
        self._write_csv(self._rows(samples_per_label=4))

        report = trainer.execute(replace(self._config(), validation_fraction=0.25))

        self.assertEqual(report["train_label_counts"], {label: 3 for label in STATIC_POSTURE_LABELS})
        self.assertEqual(report["valid_label_counts"], {label: 1 for label in STATIC_POSTURE_LABELS})
        self.assertEqual(report["train_rows"], 18)
        self.assertEqual(report["valid_rows"], 6)

    def test_rejects_missing_extra_or_reordered_feature_schema(self) -> None:
        rows = self._rows()
        schemas = (
            ["sample_id", "activity_label", *FEATURE_COLUMNS[:-1]],
            ["sample_id", "activity_label", *FEATURE_COLUMNS, "extra"],
            ["activity_label", "sample_id", *FEATURE_COLUMNS],
        )
        for schema in schemas:
            with self.subTest(schema=schema):
                shaped_rows = [{name: row.get(name, "0") for name in schema} for row in rows]
                self._write_csv(shaped_rows, schema)
                with self.assertRaisesRegex(trainer.ContractError, "feature schema mismatch"):
                    trainer.execute(self._config())

    def test_rejects_duplicate_ids_and_non_finite_features(self) -> None:
        duplicate_rows = self._rows()
        duplicate_rows[1]["sample_id"] = duplicate_rows[0]["sample_id"]
        self._write_csv(duplicate_rows)
        with self.assertRaisesRegex(trainer.ContractError, "duplicate sample_id"):
            trainer.execute(self._config())
        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(invalid=invalid):
                rows = self._rows()
                rows[0][FEATURE_COLUMNS[0]] = str(invalid)
                self._write_csv(rows)
                with self.assertRaisesRegex(trainer.ContractError, "finite numeric"):
                    trainer.execute(self._config())

    def test_rejects_missing_unknown_and_underfilled_labels(self) -> None:
        single_label_rows = [
            row for row in self._rows() if row["activity_label"] == STATIC_POSTURE_LABELS[0]
        ]
        unknown_rows = self._rows()
        unknown_rows[0]["activity_label"] = "other"
        underfilled_rows = [
            row
            for row in self._rows()
            if row["activity_label"] != STATIC_POSTURE_LABELS[-1]
            or row["sample_id"].endswith("-00")
        ]
        cases = (
            (single_label_rows, "missing required labels"),
            (unknown_rows, "unknown activity_label"),
            (underfilled_rows, "minimum samples per class"),
        )
        for rows, message in cases:
            with self.subTest(message=message):
                self._write_csv(rows)
                with self.assertRaisesRegex(trainer.ContractError, message):
                    trainer.execute(self._config())

    def test_rejects_output_collisions_and_missing_training_model_path(self) -> None:
        self._write_csv(self._rows())
        configs = (
            replace(self._config(), report_out=self.features_csv),
            replace(self._config(), dry_run=False, model_out=None),
            replace(self._config(), dry_run=False, model_out=self.report_out),
            replace(self._config(), dry_run=False, model_out=self.features_csv),
        )
        for config in configs:
            with self.subTest(config=config):
                with self.assertRaises(trainer.ContractError):
                    trainer.execute(config)

    def test_metrics_have_six_class_confusion_and_classification_schema(self) -> None:
        accuracy, matrix, report = trainer.classification_metrics(
            expected=[0, 1, 2, 3, 4, 5],
            predicted=[0, 2, 2, 3, 5, 5],
        )

        self.assertAlmostEqual(accuracy, 4 / 6)
        self.assertEqual((len(matrix), len(matrix[0])), (6, 6))
        self.assertEqual(matrix[1][2], 1)
        self.assertEqual(matrix[4][5], 1)
        self.assertEqual(
            set(report[STATIC_POSTURE_LABELS[0]]),
            {"precision", "recall", "f1-score", "support"},
        )
        self.assertIn("accuracy", report)
        self.assertIn("macro avg", report)

    def test_cli_help_lists_dry_run_and_explicit_paths(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.train_xgboost_static_posture", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("--dry-run", result.stdout)
        self.assertIn("--features-csv", result.stdout)
        self.assertIn("--report-out", result.stdout)
        self.assertIn("--model-out", result.stdout)


if __name__ == "__main__":
    unittest.main()
