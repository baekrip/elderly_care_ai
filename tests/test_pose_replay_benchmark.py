from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pose_replay_benchmark import (
    LABEL_SPECS,
    ResolutionMetrics,
    SelectedVideo,
    VideoResolutionResult,
    evaluate_resolution_gate,
    select_replay_videos,
    summarize_by_resolution,
)
from tools.pose_replay_processing import BenchmarkState, process_frame
from tools.run_pose_replay_benchmark import (
    apply_pose_overrides,
    build_blocked_replay_report,
    preflight_blocked_reason,
)
from edge.pose_estimator import PoseDetection


class PoseReplayBenchmarkTests(unittest.TestCase):
    def test_select_replay_videos_when_seed_fixed_returns_two_per_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Given: three label folders with multiple videos each.
            root = Path(tmp_dir)
            for spec in LABEL_SPECS:
                folder = root / spec.folder_name
                folder.mkdir(parents=True)
                for index in range(4):
                    (folder / f"{spec.expected_label.lower()}_{index}.mp4").write_bytes(b"video")

            # When: selecting with the approved fixed seed.
            selected = select_replay_videos(root=root, seed=20250602, per_label=2)

            # Then: every label contributes exactly two deterministic videos.
            by_label = {spec.expected_label: [] for spec in LABEL_SPECS}
            for item in selected:
                by_label[item.expected_label].append(item.path.name)
            self.assertEqual({label: len(names) for label, names in by_label.items()}, {
                "NORMAL": 2,
                "ABNORMAL": 2,
                "DANGER": 2,
            })
            self.assertEqual(
                [item.path.name for item in selected],
                [item.path.name for item in select_replay_videos(root=root, seed=20250602, per_label=2)],
            )

    def test_evaluate_resolution_gate_when_danger_missing_blocks_operation(self) -> None:
        # Given: acceptable pose quality but no danger event in a DANGER video.
        metrics = ResolutionMetrics(
            expected_label="DANGER",
            detection_rate=0.92,
            visible_joint_ratio=0.71,
            skeleton_confidence_mean=0.66,
            danger_detected=False,
        )

        # When: evaluating the operation gate.
        gate = evaluate_resolution_gate(metrics)

        # Then: the resolution is blocked for this video.
        self.assertFalse(gate.allowed)
        self.assertIn("DANGER not detected", gate.reasons)

    def test_evaluate_resolution_gate_when_pose_quality_low_blocks_operation(self) -> None:
        # Given: low detection and keypoint quality on a normal video.
        metrics = ResolutionMetrics(
            expected_label="NORMAL",
            detection_rate=0.50,
            visible_joint_ratio=0.40,
            skeleton_confidence_mean=0.50,
            danger_detected=False,
        )

        # When: evaluating the operation gate.
        gate = evaluate_resolution_gate(metrics)

        # Then: all failed quality thresholds are reported.
        self.assertFalse(gate.allowed)
        self.assertIn("detection_rate 0.5000 < 0.8000", gate.reasons)
        self.assertIn("visible_joint_ratio 0.4000 < 0.6000", gate.reasons)
        self.assertIn("skeleton_confidence_mean 0.5000 < 0.6000", gate.reasons)

    def test_summarize_by_resolution_when_results_available_calculates_recall_and_fp_rate(self) -> None:
        # Given: two DANGER videos and two NORMAL videos at one resolution.
        results = (
            VideoResolutionResult(
                expected_label="DANGER",
                video_path="danger_a.mp4",
                resolution="320x180",
                frame_count=100,
                detected_frames=90,
                duration_sec=10.0,
                pose_latency_p95_ms=20.0,
                pose_fps=50.0,
                skeleton_confidence_mean=0.70,
                visible_joint_ratio=0.80,
                danger_detected=True,
                danger_event_count=1,
                sample_images=(),
                gate_allowed=True,
                gate_reasons=(),
            ),
            VideoResolutionResult(
                expected_label="DANGER",
                video_path="danger_b.mp4",
                resolution="320x180",
                frame_count=100,
                detected_frames=90,
                duration_sec=10.0,
                pose_latency_p95_ms=20.0,
                pose_fps=50.0,
                skeleton_confidence_mean=0.70,
                visible_joint_ratio=0.80,
                danger_detected=False,
                danger_event_count=0,
                sample_images=(),
                gate_allowed=False,
                gate_reasons=("DANGER not detected",),
            ),
            VideoResolutionResult(
                expected_label="NORMAL",
                video_path="normal_a.mp4",
                resolution="320x180",
                frame_count=1800,
                detected_frames=1700,
                duration_sec=60.0,
                pose_latency_p95_ms=20.0,
                pose_fps=50.0,
                skeleton_confidence_mean=0.70,
                visible_joint_ratio=0.80,
                danger_detected=False,
                danger_event_count=0,
                sample_images=(),
                gate_allowed=True,
                gate_reasons=(),
            ),
            VideoResolutionResult(
                expected_label="NORMAL",
                video_path="normal_b.mp4",
                resolution="320x180",
                frame_count=1800,
                detected_frames=1700,
                duration_sec=60.0,
                pose_latency_p95_ms=20.0,
                pose_fps=50.0,
                skeleton_confidence_mean=0.70,
                visible_joint_ratio=0.80,
                danger_detected=True,
                danger_event_count=1,
                sample_images=(),
                gate_allowed=True,
                gate_reasons=(),
            ),
        )

        # When: summarizing by resolution.
        summary = summarize_by_resolution(results)

        # Then: video-level recall and event-level false positives are reported.
        self.assertEqual(summary[0].resolution, "320x180")
        self.assertEqual(summary[0].danger_recall, 0.5)
        self.assertEqual(summary[0].normal_fp_per_hour, 30.0)

    def test_process_frame_when_trigger_engine_uses_three_arg_contract_does_not_pass_timestamp(self) -> None:
        # Given: a trigger engine whose judge method matches the production contract.
        state = BenchmarkState()
        selected_video = SelectedVideo(expected_label="NORMAL", path=Path("normal.mp4"))
        frame = np.zeros((12, 12, 3), dtype=np.uint8)

        class PoseEstimator:
            def predict(self, _frame):
                return [
                    PoseDetection(
                        bbox=[1, 1, 10, 10],
                        bbox_confidence=0.9,
                        keypoints=[[5.0, 5.0, 0.9] for _ in range(17)],
                        pose_confidence_mean=0.9,
                    )
                ]

        class Extractor:
            def extract(self, track_id, bbox, keypoints, frame_shape, timestamp_ms):
                return {"visible_joint_ratio": 0.9}, []

        class Classifier:
            def predict(self, track_id, feature_map, feature_vector):
                return "STANDING", 0.9

        class Tier:
            def update(self, track_id, feature_map):
                return "NORMAL", 0.0

        class Trigger:
            def update(self, track_id, feature_map, coarse_label, ts_ms):
                return ["torso_angle_spike"]

            def judge_candidate(self, track_id, flags, coarse_label):
                return None

        # When: processing one frame through the replay runner seam.
        process_frame(
            frame=frame,
            fps=30.0,
            state=state,
            video=selected_video,
            pose_estimator=PoseEstimator(),
            extractor=Extractor(),
            action_classifier=Classifier(),
            tier_classifier=Tier(),
            trigger_engine=Trigger(),
            resolution="320x180",
            size=(12, 12),
            sample_indices=set(),
            samples_dir=Path("unused"),
        )

        # Then: no TypeError is raised and the frame is counted.
        self.assertEqual(state.frame_count, 1)

    def test_apply_pose_overrides_when_conf_threshold_provided_updates_model_config(self) -> None:
        # Given: a loaded benchmark config and Pi5 ONNX pose settings.
        config = {
            "model": {
                "model_path": "edge/models/yolo26s-pose.pt",
                "backend": "auto",
                "imgsz": 640,
                "conf_threshold": 0.35,
            }
        }

        # When: applying CLI pose overrides for the replay benchmark.
        apply_pose_overrides(
            config,
            pose_model_path=Path("edge/models/yolo26s-pose.onnx"),
            pose_backend="onnxruntime",
            pose_imgsz=320,
            pose_conf_threshold=0.08,
        )

        # Then: the runtime config uses the requested detector threshold.
        self.assertEqual(config["model"]["model_path"], str(Path("edge/models/yolo26s-pose.onnx")))
        self.assertEqual(config["model"]["backend"], "onnxruntime")
        self.assertEqual(config["model"]["imgsz"], 320)
        self.assertEqual(config["model"]["conf_threshold"], 0.08)

    def test_blocked_replay_report_records_missing_prerequisite_without_promotion(self) -> None:
        report = build_blocked_replay_report(reason="video folder not found: missing")

        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["operational_gate"]["allowed"])
        self.assertIn("missing_stage_metrics", report["operational_gate"]["reasons"])
        self.assertIn("video folder not found", report["blocked_reason"])
        self.assertFalse(report["promotion_allowed"])

    def test_preflight_blocks_replay_when_pose_gate_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            preflight_path = Path(tmpdir) / "preflight.json"
            preflight_path.write_text(
                '{"can_run":{"pose_replay_gate":false},"blocked_reasons":{"pose_replay_gate":"not enough clips"}}',
                encoding="utf-8",
            )

            self.assertEqual(preflight_blocked_reason(preflight_path), "not enough clips")


if __name__ == "__main__":
    unittest.main()
