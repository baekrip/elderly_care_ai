from __future__ import annotations

import unittest

from edge.feature_extractor import FeatureExtractor


def _keypoints(offset_x: float = 0.0, offset_y: float = 0.0) -> list[list[float]]:
    points = []
    for index in range(17):
        points.append([100.0 + offset_x + index, 80.0 + offset_y + index, 0.9])
    return points


class MotionFeatureTests(unittest.TestCase):
    def test_feature_extractor_emits_roi_overlap_features_without_changing_vector(self) -> None:
        extractor = FeatureExtractor(
            room_rois={
                "bed": [0.0, 0.0, 0.5, 1.0],
                "floor": [0.5, 0.0, 1.0, 1.0],
                "chair": {"x1": 0.25, "y1": 0.0, "x2": 0.75, "y2": 1.0},
            }
        )

        features, vector = extractor.extract(
            track_id=1,
            bbox=[10, 10, 90, 90],
            keypoints=_keypoints(),
            frame_shape=(100, 200, 3),
            timestamp_ms=1000,
        )

        self.assertEqual(features["bed_roi_overlap"], 1.0)
        self.assertEqual(features["floor_roi_overlap"], 0.0)
        self.assertEqual(features["chair_roi_overlap"], 0.5)
        self.assertEqual(features["dominant_roi"], "bed")
        self.assertNotIn("bed_roi_overlap", vector)

    def test_feature_extractor_emits_motion_and_static_features(self) -> None:
        extractor = FeatureExtractor()
        bbox1 = [80, 60, 160, 180]
        bbox2 = [100, 75, 180, 195]

        extractor.extract(
            track_id=1,
            bbox=bbox1,
            keypoints=_keypoints(),
            frame_shape=(360, 640, 3),
            timestamp_ms=1000,
        )
        features, _ = extractor.extract(
            track_id=1,
            bbox=bbox2,
            keypoints=_keypoints(offset_x=20.0, offset_y=15.0),
            frame_shape=(360, 640, 3),
            timestamp_ms=2000,
        )

        for key in [
            "center_accel_px_s2",
            "jerk_score",
            "motion_energy",
            "pose_delta_mean",
            "instability_score",
            "static_duration_ms",
            "visibility_ratio",
            "bbox_area_change",
        ]:
            self.assertIn(key, features)
        self.assertGreater(features["motion_energy"], 0.0)
        self.assertGreater(features["pose_delta_mean"], 0.0)

    def test_static_duration_increases_when_person_barely_moves(self) -> None:
        extractor = FeatureExtractor()
        bbox = [80, 60, 160, 180]

        extractor.extract(1, bbox, _keypoints(), (360, 640, 3), 1000)
        features, _ = extractor.extract(1, bbox, _keypoints(offset_x=0.2), (360, 640, 3), 2500)

        self.assertGreaterEqual(features["static_duration_ms"], 1500.0)


if __name__ == "__main__":
    unittest.main()
