from __future__ import annotations

import unittest

from server.services.pi5_pipeline import Pi5SkeletonPipeline
from shared.protocol import BoundingBox, Keypoint, SkeletonFrame, SkeletonFrameBatch


class _Tier:
    model_path = "edge/models/xgboost_fall_binary.json"

    def classify_summary(self, summary: dict[str, float]) -> tuple[str, float]:
        return "DROP", 0.8


class _STGCN:
    model = object()
    backend = "pytorch"

    def classify(self, window: object) -> tuple[str, float, str]:
        return "FALL", 0.9, "fake stgcn"


class _Archive:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def write_stgcn_result(self, payload: dict) -> None:
        self.results.append(payload)


def _frame(timestamp_ms: int) -> SkeletonFrame:
    return SkeletonFrame(
        camera_id="raspi_cam01",
        frame_id=f"f-{timestamp_ms}",
        timestamp_ms=timestamp_ms,
        track_id=1,
        bbox=BoundingBox(x1=0, y1=0, x2=100, y2=200),
        bbox_confidence=0.9,
        keypoints=[Keypoint(x=float(index), y=float(index + 2), confidence=0.8) for index in range(17)],
        pose_confidence_mean=0.8,
        features={"center_velocity_px_s": 20.0, "torso_angle_delta": 5.0},
    )


class Pi5PipelineModelFusionTests(unittest.TestCase):
    def test_ready_common_window_adds_model_outputs_and_fusion_to_event(self) -> None:
        archive = _Archive()
        pipeline = Pi5SkeletonPipeline(
            {
                "stgcn": {"window_ms": 5_000, "min_window_frames": 2},
                "model_fusion": {
                    "enabled": True,
                    "stgcn_stride_ms": 0,
                    "stgcn_prefilter_min_xgboost_probability": 0.0,
                    "decision_threshold": 0.7,
                    "stgcn_weight": 0.6,
                    "xgboost_weight": 0.4,
                },
                "risk_smoothing": {
                    "alpha": 1.0,
                    "suspicious_threshold": 0.45,
                    "danger_threshold": 0.7,
                    "vote_window": 2,
                },
            },
            archive=archive,
            stgcn_classifier=_STGCN(),
        )
        pipeline.tier_classifier = _Tier()

        result = pipeline.handle_batch(SkeletonFrameBatch(frames=[_frame(1_000), _frame(2_000)]))

        self.assertEqual(result["processed"], 2)
        event = result["events"][-1]
        self.assertEqual(event["event_type"], "fall_detected")
        self.assertEqual(event["risk_label"], "danger")
        self.assertEqual(event["model_outputs"]["xgboost"]["label"], "DROP")
        self.assertEqual(event["model_outputs"]["stgcn"]["label"], "FALL")
        self.assertAlmostEqual(event["model_outputs"]["fusion"]["final_fall_probability"], 0.86)
        self.assertEqual(event["model_outputs"]["fusion"]["final_label"], "fall_confirmed")
        self.assertEqual(archive.results[-1]["model_outputs"]["track_window"]["frame_count"], 2)


if __name__ == "__main__":
    unittest.main()
