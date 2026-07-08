from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
import json
import tempfile

from tools.autolabel_contracts import (
    SourceAnnotation,
    SourceAnnotationRejected,
    TeacherPrediction,
    classify_coarse_features,
    resolve_source_annotation,
)
from tools.auto_label_generator import visible_joint_ratio


class AutoLabelGeneratorTests(unittest.TestCase):
    def test_source_annotation_rejects_uncovered_unknown_and_conflicting_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            label_path = Path(temp_dir) / "labels.json"
            label_path.write_text(json.dumps({"annotations": {"object": [
                {"startFrame": 10, "endFrame": 20, "actionType": "ABNOR_H", "actionName": "H"},
            ]}}), encoding="utf-8")
            uncovered = resolve_source_annotation(label_path, 5)
            self.assertEqual(uncovered, SourceAnnotationRejected("no_covering_source_annotation"))

            label_path.write_text(json.dumps({"annotations": {"object": [
                {"startFrame": 0, "endFrame": 20, "actionType": "OTHER"},
            ]}}), encoding="utf-8")
            unknown = resolve_source_annotation(label_path, 5)
            self.assertEqual(unknown, SourceAnnotationRejected("unsupported_source_action_type"))

            label_path.write_text(json.dumps({"annotations": {"object": [
                {"startFrame": 0, "endFrame": 20, "actionType": "ABNOR_H"},
                {"startFrame": 0, "endFrame": 20, "actionType": "ABNOR_W"},
            ]}}), encoding="utf-8")
            conflict = resolve_source_annotation(label_path, 5)
            self.assertEqual(conflict, SourceAnnotationRejected("conflicting_source_annotations"))

    def test_source_annotation_maps_only_explicit_whitelist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            label_path = Path(temp_dir) / "labels.json"
            label_path.write_text(json.dumps({"annotations": {"object": [
                {"startFrame": 0, "endFrame": 20, "actionType": "ABNOR_W", "actionName": "W12W24"},
            ]}}), encoding="utf-8")
            self.assertEqual(
                resolve_source_annotation(label_path, 5),
                SourceAnnotation("ABNOR_W", "W12W24", "abnormal"),
            )
            label_path.write_text(json.dumps({"annotations": {"object": [
                {"startFrame": "0", "endFrame": "20", "actionType": "M_I_001"},
            ]}}), encoding="utf-8")
            self.assertEqual(
                resolve_source_annotation(label_path, 5),
                SourceAnnotation("M_I_001", "", "normal"),
            )

    def test_teacher_has_explicit_standing_rule_and_no_catch_all(self) -> None:
        standing = classify_coarse_features({
            "torso_angle_deg": 10.0,
            "left_knee_angle_deg": 170.0,
            "right_knee_angle_deg": 172.0,
            "bbox_aspect_ratio": 0.5,
            "center_velocity_px_s": 5.0,
        })
        self.assertEqual(standing, TeacherPrediction("standing", 0.8, "upright_straight_legs"))
        mirrored_standing = classify_coarse_features({
            "torso_angle_deg": 170.0,
            "left_knee_angle_deg": 170.0,
            "right_knee_angle_deg": 172.0,
            "bbox_aspect_ratio": 0.5,
            "center_velocity_px_s": 5.0,
        })
        self.assertEqual(mirrored_standing, TeacherPrediction("standing", 0.8, "upright_straight_legs"))
        self.assertIsNone(classify_coarse_features({
            "torso_angle_deg": 60.0,
            "left_knee_angle_deg": 120.0,
            "right_knee_angle_deg": 120.0,
            "bbox_aspect_ratio": 1.0,
            "center_velocity_px_s": 20.0,
        }))

    def test_visible_joint_ratio_rejects_missing_or_low_confidence_points(self) -> None:
        points = [[1.0, 2.0, 0.9] for _ in range(17)]
        self.assertEqual(visible_joint_ratio(points, 0.2), 1.0)
        points[0][2] = 0.1
        self.assertAlmostEqual(visible_joint_ratio(points, 0.2), 16 / 17)
        self.assertEqual(visible_joint_ratio(points[:-1], 0.2), 0.0)

    def test_cli_help_exposes_fail_closed_inputs_and_outputs(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.auto_label_generator", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        for option in (
            "--pose-jsonl",
            "--activity-frames-out",
            "--static-registry-out",
            "--rejected-out",
            "--report-out",
        ):
            self.assertIn(option, result.stdout)


if __name__ == "__main__":
    unittest.main()
