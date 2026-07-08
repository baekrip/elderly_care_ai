from __future__ import annotations

import unittest

from server.services.model_input_window import (
    ModelInputWindowBuilder,
    fuse_model_outputs,
)
from shared.protocol import BoundingBox, Keypoint, SkeletonFrame


def _frame(timestamp_ms: int, *, velocity: float, confidence: float = 0.9) -> SkeletonFrame:
    return SkeletonFrame(
        camera_id="raspi_cam01",
        frame_id=f"f-{timestamp_ms}",
        timestamp_ms=timestamp_ms,
        track_id=7,
        bbox=BoundingBox(x1=10, y1=20, x2=110, y2=220),
        bbox_confidence=confidence,
        keypoints=[Keypoint(x=float(index), y=float(index + 1), confidence=confidence) for index in range(17)],
        pose_confidence_mean=confidence,
        features={
            "center_velocity_px_s": velocity,
            "pose_confidence_mean": confidence,
            "frame_width": 640,
            "frame_height": 360,
        },
    )


class ModelInputWindowTests(unittest.TestCase):
    def test_builder_returns_same_window_summary_and_stgcn_sequence(self) -> None:
        builder = ModelInputWindowBuilder(window_ms=5_000, min_frames=3, stgcn_stride_ms=0)

        self.assertIsNone(builder.add(_frame(1_000, velocity=10.0)))
        self.assertIsNone(builder.add(_frame(2_000, velocity=20.0)))
        window = builder.add(_frame(3_000, velocity=30.0))

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.track_window["camera_id"], "raspi_cam01")
        self.assertEqual(window.track_window["track_id"], 7)
        self.assertEqual(window.track_window["start_ts_ms"], 1_000)
        self.assertEqual(window.track_window["end_ts_ms"], 3_000)
        self.assertEqual(window.track_window["frame_count"], 3)
        self.assertEqual(window.xgboost_summary["center_velocity_px_s_mean"], 20.0)
        self.assertEqual(window.xgboost_summary["center_velocity_px_s_last"], 30.0)
        self.assertEqual(len(window.sequence), 3)
        self.assertEqual(window.sequence[-1].keypoints_17[0], [0.0, 1.0, 0.9])
        self.assertEqual(window.coarse_action, "UNKNOWN")
        self.assertEqual(window.coarse_confidence, 0.0)

    def test_builder_respects_stgcn_stride(self) -> None:
        builder = ModelInputWindowBuilder(window_ms=5_000, min_frames=2, stgcn_stride_ms=1_000)

        self.assertIsNone(builder.add(_frame(1_000, velocity=10.0)))
        self.assertIsNotNone(builder.add(_frame(2_000, velocity=20.0)))
        self.assertIsNone(builder.add(_frame(2_500, velocity=30.0)))
        self.assertIsNotNone(builder.add(_frame(3_000, velocity=40.0)))

    def test_fusion_uses_stgcn_weight_when_stgcn_is_present(self) -> None:
        fusion = fuse_model_outputs(
            xgboost_label="DROP",
            xgboost_probability=0.8,
            stgcn_label="FALL",
            stgcn_probability=0.9,
            stgcn_weight=0.6,
            xgboost_weight=0.4,
            decision_threshold=0.7,
        )

        self.assertEqual(fusion["method"], "weighted_average")
        self.assertAlmostEqual(fusion["final_fall_probability"], 0.86)
        self.assertEqual(fusion["final_label"], "fall_confirmed")

    def test_fusion_falls_back_to_xgboost_without_stgcn(self) -> None:
        fusion = fuse_model_outputs(
            xgboost_label="DROP",
            xgboost_probability=0.65,
            stgcn_label=None,
            stgcn_probability=None,
            decision_threshold=0.7,
        )

        self.assertEqual(fusion["method"], "xgboost_only")
        self.assertEqual(fusion["final_fall_probability"], 0.65)
        self.assertEqual(fusion["final_label"], "fall_suspicious")

    def test_multiclass_danger_label_counts_as_fall_probability(self) -> None:
        fusion = fuse_model_outputs(
            xgboost_label="DANGER",
            xgboost_probability=0.91,
            stgcn_label=None,
            stgcn_probability=None,
            decision_threshold=0.7,
        )

        self.assertEqual(fusion["method"], "xgboost_only")
        self.assertEqual(fusion["final_fall_probability"], 0.91)
        self.assertEqual(fusion["final_label"], "fall_confirmed")


if __name__ == "__main__":
    unittest.main()
