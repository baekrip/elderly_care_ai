from __future__ import annotations

import unittest

from server.services.pi5_pipeline import Pi5SkeletonPipeline
from shared.protocol import BoundingBox, Keypoint, SkeletonFrame, SkeletonFrameBatch


class _Archive:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def write_stgcn_result(self, payload: dict) -> None:
        self.results.append(payload)


def _frame(features: dict[str, float], timestamp_ms: int = 1000) -> SkeletonFrame:
    return SkeletonFrame(
        camera_id="raspi_cam01",
        frame_id=f"f-{timestamp_ms}",
        timestamp_ms=timestamp_ms,
        track_id=1,
        bbox=BoundingBox(x1=0, y1=0, x2=100, y2=200),
        bbox_confidence=0.9,
        keypoints=[Keypoint(x=1.0, y=2.0, confidence=0.8) for _ in range(17)],
        pose_confidence_mean=0.8,
        features=features,
    )


class Pi5PipelineTests(unittest.TestCase):
    def test_running_speed_feature_creates_suspicious_event(self) -> None:
        archive = _Archive()
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "running_speed_threshold_px_s": 100.0,
                    "danger_speed_threshold_px_s": 250.0,
                }
            },
            archive=archive,
        )
        batch = SkeletonFrameBatch(
            frames=[
                _frame({"center_velocity_px_s": 180.0}, timestamp_ms=1000),
                _frame({"center_velocity_px_s": 180.0}, timestamp_ms=1100),
                _frame({"center_velocity_px_s": 180.0}, timestamp_ms=1200),
            ]
        )

        result = pipeline.handle_batch(batch)

        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["events"][0]["event_type"], "running_over_speed")
        self.assertEqual(result["events"][0]["state"], "SUSPICIOUS")
        self.assertIsInstance(result["events"][0]["risk_score"], int)
        self.assertGreaterEqual(result["events"][0]["risk_score"], 1)
        self.assertLessEqual(result["events"][0]["risk_score"], 5)
        self.assertGreater(result["events"][0]["risk_confidence"], 0.0)
        self.assertEqual(archive.results[-1]["event_type"], "running_over_speed")

    def test_repeated_high_speed_frames_reach_danger_state(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "running_speed_threshold_px_s": 100.0,
                    "danger_speed_threshold_px_s": 250.0,
                },
                "risk_smoothing": {
                    "alpha": 0.3,
                    "suspicious_threshold": 0.45,
                    "danger_threshold": 0.70,
                    "vote_window": 10,
                },
            }
        )
        batch = SkeletonFrameBatch(
            frames=[
                _frame({"center_velocity_px_s": 320.0}, timestamp_ms=1000 + index * 100)
                for index in range(8)
            ]
        )

        result = pipeline.handle_batch(batch)

        self.assertTrue(any(event["risk_label"] == "danger" for event in result["events"]))
        self.assertTrue(any(event["state"] == "DANGEROUS" for event in result["events"]))

    def test_bed_roi_suppresses_static_faint_event(self) -> None:
        pipeline = Pi5SkeletonPipeline({"risk_features": {"static_threshold_ms": 30_000.0}})

        bed_event_type, bed_score = pipeline._score_frame(
            _frame(
                {
                    "center_velocity_px_s": 0.0,
                    "static_duration_ms": 120_000.0,
                    "bed_roi_overlap": 0.9,
                    "floor_roi_overlap": 0.1,
                }
            )
        )
        floor_event_type, floor_score = pipeline._score_frame(
            _frame(
                {
                    "center_velocity_px_s": 0.0,
                    "static_duration_ms": 120_000.0,
                    "bed_roi_overlap": 0.1,
                    "floor_roi_overlap": 0.9,
                },
                timestamp_ms=2000,
            )
        )

        self.assertEqual(bed_event_type, "normal_activity")
        self.assertEqual(bed_score, 0.0)
        self.assertEqual(floor_event_type, "faint_static")
        self.assertGreater(floor_score, 0.0)

    def test_low_quality_pose_suppresses_motion_rule_false_collision(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "collision_jerk_threshold": 180.0,
                    "min_rule_pose_confidence": 0.20,
                    "min_rule_visible_joint_ratio": 0.30,
                }
            }
        )

        event_type, score = pipeline._score_frame(
            _frame(
                {
                    "jerk_score": 999.0,
                    "pose_confidence_mean": 0.10,
                    "visible_joint_ratio": 0.12,
                }
            )
        )

        self.assertEqual(event_type, "normal_activity")
        self.assertEqual(score, 0.0)

    def test_high_quality_wide_lying_pose_creates_fall_candidate(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "lying_bbox_aspect_ratio_threshold": 1.20,
                    "lying_static_duration_ms": 500.0,
                    "min_rule_pose_confidence": 0.20,
                    "min_rule_visible_joint_ratio": 0.30,
                }
            }
        )

        event_type, score = pipeline._score_frame(
            _frame(
                {
                    "bbox_aspect_ratio": 1.45,
                    "static_duration_ms": 1200.0,
                    "pose_confidence_mean": 0.72,
                    "visible_joint_ratio": 0.82,
                    "bed_roi_overlap": 0.0,
                    "floor_roi_overlap": 0.0,
                }
            )
        )

        self.assertEqual(event_type, "fall_detected")
        self.assertGreater(score, 0.0)

    def test_torso_angle_delta_wraparound_does_not_create_fall_candidate(self) -> None:
        pipeline = Pi5SkeletonPipeline({"risk_features": {"fall_torso_delta_threshold": 35.0}})

        event_type, score = pipeline._score_frame(
            _frame(
                {
                    "torso_angle_delta": -350.8,
                    "pose_confidence_mean": 0.80,
                    "visible_joint_ratio": 0.90,
                }
            )
        )

        self.assertEqual(event_type, "normal_activity")
        self.assertEqual(score, 0.0)

    def test_torso_angle_delta_without_motion_evidence_does_not_create_fall_candidate(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "fall_torso_delta_threshold": 35.0,
                    "fall_motion_velocity_threshold_px_s": 50.0,
                    "fall_motion_bbox_area_change_threshold": 0.15,
                    "fall_motion_pose_delta_threshold": 10.0,
                }
            }
        )

        event_type, score = pipeline._score_frame(
            _frame(
                {
                    "torso_angle_delta": 48.0,
                    "vertical_velocity_px_s": 0.0,
                    "center_velocity_px_s": 0.0,
                    "bbox_area_change": 0.0,
                    "pose_delta_mean": 0.0,
                    "pose_confidence_mean": 0.80,
                    "visible_joint_ratio": 0.90,
                }
            )
        )

        self.assertEqual(event_type, "normal_activity")
        self.assertEqual(score, 0.0)

    def test_torso_angle_delta_with_vertical_motion_creates_fall_candidate(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "fall_torso_delta_threshold": 35.0,
                    "fall_motion_velocity_threshold_px_s": 50.0,
                }
            }
        )

        event_type, score = pipeline._score_frame(
            _frame(
                {
                    "torso_angle_delta": 48.0,
                    "vertical_velocity_px_s": -72.0,
                    "pose_confidence_mean": 0.80,
                    "visible_joint_ratio": 0.90,
                }
            )
        )

        self.assertEqual(event_type, "fall_detected")
        self.assertGreater(score, 0.0)

    def test_low_visibility_wide_lying_motion_creates_fall_candidate(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "lying_bbox_aspect_ratio_threshold": 1.20,
                    "min_rule_pose_confidence": 0.20,
                    "min_rule_visible_joint_ratio": 0.30,
                    "fall_motion_velocity_threshold_px_s": 50.0,
                    "fall_motion_pose_delta_threshold": 10.0,
                    "fall_motion_bbox_area_change_threshold": 0.15,
                }
            }
        )

        event_type, score = pipeline._score_frame(
            _frame(
                {
                    "bbox_aspect_ratio": 1.89,
                    "static_duration_ms": 0.0,
                    "pose_confidence_mean": 0.20,
                    "visible_joint_ratio": 0.23,
                    "center_velocity_px_s": 125.0,
                    "vertical_velocity_px_s": -27.0,
                    "bbox_area_change": -0.01,
                    "pose_delta_mean": 100.0,
                    "bed_roi_overlap": 0.0,
                    "floor_roi_overlap": 0.0,
                }
            )
        )

        self.assertEqual(event_type, "fall_detected")
        self.assertGreater(score, 0.0)

    def test_wide_lying_motion_is_prioritized_over_speed_or_collision(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "running_speed_threshold_px_s": 140.0,
                    "danger_speed_threshold_px_s": 260.0,
                    "collision_jerk_threshold": 180.0,
                    "collision_pose_delta_threshold": 20.0,
                    "lying_bbox_aspect_ratio_threshold": 1.20,
                    "fall_motion_velocity_threshold_px_s": 50.0,
                    "fall_motion_pose_delta_threshold": 10.0,
                }
            }
        )

        event_type, score = pipeline._score_frame(
            _frame(
                {
                    "bbox_aspect_ratio": 1.39,
                    "center_velocity_px_s": 190.0,
                    "vertical_velocity_px_s": 12.0,
                    "pose_delta_mean": 23.0,
                    "jerk_score": 240.0,
                    "pose_confidence_mean": 0.67,
                    "visible_joint_ratio": 0.70,
                    "bed_roi_overlap": 0.0,
                    "floor_roi_overlap": 0.0,
                }
            )
        )

        self.assertEqual(event_type, "fall_detected")
        self.assertGreater(score, 0.0)

    def test_normal_frame_resolves_previous_fall_event_for_same_track(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "events": {"resolve_after_ms": 500},
                "risk_features": {
                    "fall_torso_delta_threshold": 35.0,
                    "fall_motion_velocity_threshold_px_s": 50.0,
                },
                "risk_smoothing": {
                    "alpha": 1.0,
                    "suspicious_threshold": 0.45,
                    "danger_threshold": 0.70,
                    "vote_window": 3,
                },
            }
        )

        fall_event = pipeline.handle_frame(
            _frame(
                {
                    "torso_angle_delta": 80.0,
                    "vertical_velocity_px_s": -90.0,
                    "pose_confidence_mean": 0.80,
                    "visible_joint_ratio": 0.90,
                },
                timestamp_ms=1000,
            )
        )
        normal_event = pipeline.handle_frame(
            _frame(
                {
                    "torso_angle_delta": 0.0,
                    "vertical_velocity_px_s": 0.0,
                    "center_velocity_px_s": 0.0,
                    "pose_confidence_mean": 0.80,
                    "visible_joint_ratio": 0.90,
                },
                timestamp_ms=1700,
            )
        )

        self.assertIsNotNone(fall_event)
        self.assertEqual(fall_event["event_type"], "fall_detected")
        self.assertEqual(fall_event["state"], "DANGEROUS")
        self.assertIsNone(normal_event)
        self.assertFalse(
            any(
                event.event_type == "fall_detected" and event.state != "RESOLVED"
                for event in pipeline.events.active_events()
            )
        )

    def test_isolated_jerk_without_pose_instability_does_not_create_collision(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "risk_features": {
                    "collision_jerk_threshold": 180.0,
                    "collision_pose_delta_threshold": 20.0,
                    "collision_instability_threshold": 10.0,
                    "collision_bbox_area_change_threshold": 0.25,
                }
            }
        )

        event_type, score = pipeline._score_frame(
            _frame(
                {
                    "jerk_score": 200.5,
                    "center_velocity_px_s": 6.2,
                    "pose_delta_mean": 4.6,
                    "instability_score": 3.9,
                    "bbox_area_change": -0.03,
                    "pose_confidence_mean": 0.82,
                    "visible_joint_ratio": 0.88,
                }
            )
        )

        self.assertEqual(event_type, "normal_activity")
        self.assertEqual(score, 0.0)

    def test_model_normal_standing_suppresses_motion_rule_event(self) -> None:
        pipeline = Pi5SkeletonPipeline(
            {
                "stgcn": {"min_window_frames": 1},
                "risk_features": {
                    "running_speed_threshold_px_s": 100.0,
                    "danger_speed_threshold_px_s": 250.0,
                },
            }
        )
        pipeline._analyze_model_window = lambda window: {
            "xgboost": {"label": "NORMAL", "probability": 0.99, "fall_probability": 0.0},
            "stgcn": {"status": "ok", "label": "STANDING", "probability": 0.5, "fall_probability": 0.0},
            "fusion": {"final_label": "normal", "final_fall_probability": 0.0},
        }

        result = pipeline.handle_frame(
            _frame(
                {
                    "center_velocity_px_s": 180.0,
                    "pose_confidence_mean": 0.80,
                    "visible_joint_ratio": 0.90,
                }
            )
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
