from __future__ import annotations

import unittest
from pathlib import Path


class TrainingReferenceContractTests(unittest.TestCase):
    def test_fall_pipeline_tier_export_forwards_limits_and_streams_progress(self) -> None:
        launcher = Path("run_fall_pipeline.bat").read_text(encoding="utf-8")

        self.assertIn("python.exe -u tools\\export_xgboost_tier_features.py", launcher)
        self.assertIn(
            "--output-csv experiments/behavior_training/features/xgboost_tier_multiclass_features.csv %2 %3 %4 %5 %6 %7 %8 %9",
            launcher,
        )
        self.assertIn("python.exe -u tools\\export_stgcn_sequences.py", launcher)
        self.assertIn(
            "--meta-out experiments/behavior_training/reports/stgcn_sequence_meta_fall.json %2 %3 %4 %5 %6 %7 %8 %9",
            launcher,
        )

    def test_current_command_section_has_no_copy_paste_traps(self) -> None:
        text = Path("docs/학습참고.md").read_text(encoding="utf-8")
        current, archive = text.split("\n---\n", maxsplit=1)

        self.assertNotIn("<LOCAL_FRAME.jpg>", current)
        self.assertIn(".\\run_fall_pipeline.bat auto-train --profile coarse-distill-v1 --plan", current)
        self.assertIn(".\\run_fall_pipeline.bat auto-train --profile coarse-distill-v1 --train-candidates", current)
        self.assertNotIn("$ManualInputsReady", current)
        self.assertIn("과거 기록", archive)

    def test_daily_activity_section_uses_canonical_contracts_and_candidate_outputs(self) -> None:
        text = Path("docs/학습참고.md").read_text(encoding="utf-8")

        self.assertIn("## 자동 Pseudo-Labeling 및 후보 학습 — 현재 유효 명령", text)
        self.assertIn(".\\run_fall_pipeline.bat auto-train --profile coarse-distill-v1 --plan", text)
        self.assertIn(".\\run_fall_pipeline.bat auto-train --profile coarse-distill-v1 --train-candidates", text)
        self.assertIn("## 과거 기록: 일상 세부 분류 수동 학습 — 실행 금지", text)
        self.assertIn("`normal`, `abnormal`, `danger`", text)
        self.assertIn("experiments\\behavior_training\\candidates\\xgboost_static_posture.json", text)
        self.assertIn("experiments\\behavior_training\\candidates\\stgcn_activity_multiclass.pth", text)
        self.assertIn("--dry-run", text)
        self.assertIn("수동 라벨 입력이 아직 없으므로 실제 21-class dataset export는 차단", text)
        self.assertNotIn("30-class", text)
        self.assertNotIn("미구현", text)

    def test_manual_training_guide_has_ordered_commands_and_no_auto_training_rule(self) -> None:
        text = Path("docs/학습참고.md").read_text(encoding="utf-8")
        required_markers = [
            "수동 학습 실행 가이드",
            "Codex 자동 학습 실행 금지",
            "목표 기준 학습 순서",
            "현재 발표 목표 우선 완료 게이트",
            "rtsp://54.116.119.98:8554/P001",
            "http://54.116.119.98:8889/P001/",
            "Pi5의 `UNKNOWN NORMAL`은 행동추론 결과가 아니므로 최종 표시 대상에서 제외",
            "backend SSE 또는 JSON 패널",
            "activity_label",
            "risk_label",
            "tools/train_stgcn_activity.py",
            "backend media DB",
            "반복 추가학습 도구 생성 기준",
            "tools\\register_training_batch.py",
            "tools\\merge_xgboost_feature_batches.py",
            "tools\\merge_stgcn_sequence_batches.py",
            "tools\\build_yolo_pose_dataset.py",
            "추가학습 순서",
            "검증 순서",
            "검증 이후 순서",
            ".\\run_fall_pipeline.bat prepare",
            ".\\run_fall_pipeline.bat export-xgb-tier",
            "xgboost_tier_multiclass.json",
            "xgboost_tier_multiclass_meta.json",
            "--label-column tier_label",
            "tools\\train_xgboost_tier.py",
            ".\\run_fall_pipeline.bat export-stgcn-fall",
            "tools\\train_stgcn.py",
            "accuracy",
            "precision",
            "recall",
        ]

        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
