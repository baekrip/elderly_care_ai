import unittest
from collections import deque

from edge.trigger_engine import TriggerEngine


class TriggerEngineDurationTests(unittest.TestCase):
    def test_duration_rules_use_frame_timestamps(self):
        engine = TriggerEngine({
            "trigger_thresholds": {
                "center_velocity_near_zero_px_s": 7.0,
                "inactivity_duration_sec": 300,
                "prolonged_floor_lying_duration_sec": 60,
            }
        })
        features = {
            "center_velocity_px_s": 0.0,
            "torso_angle_deg": 110.0,
            "torso_angle_delta": 0.0,
            "vertical_velocity_px_s": 0.0,
            "bbox_aspect_ratio": 1.0,
            "shoulder_tilt_deg": 0.0,
            "left_knee_angle_deg": 180.0,
            "right_knee_angle_deg": 180.0,
            "pose_confidence_mean": 1.0,
            "visible_joint_ratio": 1.0,
            "head_hip_y_diff": -100.0,
        }

        self.assertNotIn(
            "prolonged_floor_lying",
            engine.update(track_id=1, features=features, coarse_label="LYING", ts_ms=0),
        )
        self.assertNotIn(
            "prolonged_floor_lying",
            engine.update(track_id=1, features=features, coarse_label="LYING", ts_ms=59_000),
        )
        self.assertIn(
            "prolonged_floor_lying",
            engine.update(track_id=1, features=features, coarse_label="LYING", ts_ms=61_000),
        )

    def test_bed_roi_suppresses_prolonged_floor_lying(self):
        engine = TriggerEngine({
            "trigger_thresholds": {
                "center_velocity_near_zero_px_s": 7.0,
                "inactivity_duration_sec": 300,
                "prolonged_floor_lying_duration_sec": 60,
            }
        })
        features = {
            "center_velocity_px_s": 0.0,
            "torso_angle_deg": 110.0,
            "torso_angle_delta": 0.0,
            "vertical_velocity_px_s": 0.0,
            "bbox_aspect_ratio": 1.0,
            "shoulder_tilt_deg": 0.0,
            "left_knee_angle_deg": 180.0,
            "right_knee_angle_deg": 180.0,
            "pose_confidence_mean": 1.0,
            "visible_joint_ratio": 1.0,
            "head_hip_y_diff": -100.0,
            "bed_roi_overlap": 0.85,
            "floor_roi_overlap": 0.10,
        }

        engine.update(track_id=3, features=features, coarse_label="LYING", ts_ms=0)
        flags = engine.update(track_id=3, features=features, coarse_label="LYING", ts_ms=61_000)

        self.assertNotIn("prolonged_floor_lying", flags)

    def test_night_activity_is_disabled_for_offline_replay_by_default(self):
        engine = TriggerEngine()
        engine._buffers[1] = deque([{"features": {"center_velocity_px_s": 20.0}}], maxlen=24)
        flags = ["shoulder_tilt_spike"]
        result = engine.judge_candidate(track_id=1, flags=flags, coarse_label="WALKING")

        self.assertIsNone(result)

    def test_gradual_fall_suspect_from_torso_trend_and_xgboost_probability(self):
        engine = TriggerEngine({
            "trigger_thresholds": {
                "gradual_fall_min_frames": 8,
                "gradual_fall_min_torso_delta_deg": 35.0,
                "gradual_fall_min_xgboost_prob": 0.4,
                "gradual_fall_min_head_drop_norm": 0.05,
            }
        })

        flags = []
        for index in range(8):
            features = {
                "center_velocity_px_s": 5.0,
                "torso_angle_deg": 15.0 + index * 6.0,
                "torso_angle_delta": 6.0,
                "vertical_velocity_px_s": 10.0,
                "bbox_aspect_ratio": 0.5,
                "shoulder_tilt_deg": 0.0,
                "left_knee_angle_deg": 160.0,
                "right_knee_angle_deg": 160.0,
                "pose_confidence_mean": 0.8,
                "visible_joint_ratio": 1.0,
                "head_hip_y_diff": -120.0 + index * 8.0,
                "nose_y_norm": 0.2 + index * 0.01,
                "xgboost_prob": 0.45,
            }
            flags = engine.update(track_id=7, features=features, coarse_label="TRANSITION", ts_ms=index * 100)

        self.assertIn("gradual_fall_trend", flags)
        result = engine.judge_candidate(track_id=7, flags=flags, coarse_label="TRANSITION")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "GRADUAL_FALL_SUSPECT")
        self.assertEqual(result[1], "ABNORMAL")


if __name__ == "__main__":
    unittest.main()
