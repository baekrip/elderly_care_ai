from __future__ import annotations

import unittest

import numpy as np

from edge import pose_estimator
from edge.pose_estimator import build_onnx_session_options, decode_yolo_pose_onnx_output


class PoseEstimatorOnnxTests(unittest.TestCase):
    def test_ultralytics_predict_applies_people_limit_after_quality_filters(self) -> None:
        class FakeTensor:
            def __init__(self, array: np.ndarray) -> None:
                self.array = array

            def cpu(self) -> "FakeTensor":
                return self

            def numpy(self) -> np.ndarray:
                return self.array

        class FakeBoxes:
            xyxy = FakeTensor(
                np.array(
                    [
                        [10.0, 10.0, 20.0, 20.0],
                        [100.0, 100.0, 220.0, 260.0],
                    ],
                    dtype=np.float32,
                )
            )
            conf = FakeTensor(np.array([0.99, 0.80], dtype=np.float32))

        class FakeKeypoints:
            low_pose = np.array([[12.0 + index, 12.0 + index, 0.01] for index in range(17)], dtype=np.float32)
            good_pose = np.array([[110.0 + index, 120.0 + index, 0.8] for index in range(17)], dtype=np.float32)
            data = FakeTensor(np.stack([low_pose, good_pose]))

        class FakeResult:
            boxes = FakeBoxes()
            keypoints = FakeKeypoints()

        class FakeModel:
            def predict(self, *args, **kwargs):
                return [FakeResult()]

        estimator = pose_estimator.YoloPoseEstimator.__new__(pose_estimator.YoloPoseEstimator)
        estimator.backend = "ultralytics"
        estimator.model = FakeModel()
        estimator.imgsz = 640
        estimator.conf = 0.03
        estimator.device = "cpu"
        estimator.max_people = 1
        estimator.min_pose_confidence = 0.1
        estimator.min_bbox_area_ratio = 0.002
        estimator.nms_iou_threshold = 0.65

        detections = estimator.predict(np.zeros((360, 640, 3), dtype=np.uint8))

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].bbox, [100, 100, 220, 260])
        self.assertAlmostEqual(detections[0].pose_confidence_mean, 0.8, places=4)

    def test_decode_yolo_pose_onnx_output_returns_scaled_detection(self) -> None:
        # YOLO pose ONNX export commonly returns [1, 56, N]:
        # x, y, w, h, confidence, then 17 * (x, y, confidence).
        row = np.zeros((56,), dtype=np.float32)
        row[0:5] = [320.0, 160.0, 200.0, 100.0, 0.92]
        for index in range(17):
            base = 5 + index * 3
            row[base : base + 3] = [320.0 + index, 160.0 + index, 0.8]

        output = row.reshape(1, 56, 1)
        detections = decode_yolo_pose_onnx_output(
            output,
            frame_shape=(360, 640, 3),
            imgsz=640,
            conf_threshold=0.35,
            max_people=1,
        )

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertAlmostEqual(detection.bbox_confidence, 0.92, places=4)
        self.assertEqual(detection.bbox, [220, 62, 420, 118])
        self.assertEqual(len(detection.keypoints), 17)
        self.assertAlmostEqual(detection.keypoints[0][0], 320.0, places=4)
        self.assertAlmostEqual(detection.keypoints[0][1], 90.0, places=4)
        self.assertAlmostEqual(detection.pose_confidence_mean, 0.8, places=4)

    def test_decode_yolo_pose_onnx_output_filters_low_confidence(self) -> None:
        row = np.zeros((56,), dtype=np.float32)
        row[0:5] = [320.0, 160.0, 200.0, 100.0, 0.1]
        output = row.reshape(1, 1, 56)

        detections = decode_yolo_pose_onnx_output(
            output,
            frame_shape=(360, 640, 3),
            imgsz=640,
            conf_threshold=0.35,
            max_people=1,
        )

        self.assertEqual(detections, [])

    def test_decode_yolo_pose_onnx_output_filters_low_pose_confidence(self) -> None:
        row = np.zeros((56,), dtype=np.float32)
        row[0:5] = [320.0, 160.0, 200.0, 100.0, 0.8]
        for index in range(17):
            base = 5 + index * 3
            row[base : base + 3] = [320.0 + index, 160.0 + index, 0.02]

        output = row.reshape(1, 1, 56)
        detections = decode_yolo_pose_onnx_output(
            output,
            frame_shape=(360, 640, 3),
            imgsz=640,
            conf_threshold=0.35,
            max_people=0,
            min_pose_confidence=0.12,
        )

        self.assertEqual(detections, [])

    def test_decode_yolo_pose_onnx_output_keeps_all_people_without_limit(self) -> None:
        rows = []
        for person_index, confidence in enumerate([0.9, 0.8, 0.7]):
            row = np.zeros((56,), dtype=np.float32)
            row[0:5] = [100.0 + person_index * 80.0, 100.0, 40.0, 80.0, confidence]
            for keypoint_index in range(17):
                base = 5 + keypoint_index * 3
                row[base : base + 3] = [
                    100.0 + person_index * 80.0 + keypoint_index,
                    100.0 + keypoint_index,
                    0.6,
                ]
            rows.append(row)

        output = np.stack(rows).reshape(1, 3, 56)
        detections = decode_yolo_pose_onnx_output(
            output,
            frame_shape=(360, 640, 3),
            imgsz=640,
            conf_threshold=0.35,
            max_people=0,
            min_pose_confidence=0.12,
        )

        self.assertEqual(len(detections), 3)
        self.assertEqual([round(d.bbox_confidence, 1) for d in detections], [0.9, 0.8, 0.7])

    def test_decode_yolo_pose_onnx_output_removes_duplicate_boxes_without_people_limit(self) -> None:
        rows = []
        for center_x, confidence in [(320.0, 0.9), (322.0, 0.8)]:
            row = np.zeros((56,), dtype=np.float32)
            row[0:5] = [center_x, 160.0, 200.0, 100.0, confidence]
            for index in range(17):
                base = 5 + index * 3
                row[base : base + 3] = [center_x + index, 160.0 + index, 0.7]
            rows.append(row)

        output = np.stack(rows).reshape(1, 2, 56)
        detections = decode_yolo_pose_onnx_output(
            output,
            frame_shape=(360, 640, 3),
            imgsz=640,
            conf_threshold=0.35,
            max_people=0,
            min_pose_confidence=0.12,
            nms_iou_threshold=0.65,
        )

        self.assertEqual(len(detections), 1)
        self.assertAlmostEqual(detections[0].bbox_confidence, 0.9, places=4)

    def test_decode_yolo_pose_onnx_output_keeps_low_confidence_large_person_candidate(self) -> None:
        row = np.zeros((56,), dtype=np.float32)
        row[0:5] = [320.0, 160.0, 180.0, 120.0, 0.04]
        for index in range(17):
            base = 5 + index * 3
            row[base : base + 3] = [320.0 + index, 160.0 + index, 0.6]

        detections = decode_yolo_pose_onnx_output(
            row.reshape(1, 1, 56),
            frame_shape=(360, 640, 3),
            imgsz=640,
            conf_threshold=0.03,
            max_people=0,
            min_pose_confidence=0.12,
            min_bbox_area_ratio=0.01,
        )

        self.assertEqual(len(detections), 1)
        self.assertAlmostEqual(detections[0].bbox_confidence, 0.04, places=4)

    def test_decode_yolo_pose_onnx_output_filters_small_false_positive_candidate(self) -> None:
        row = np.zeros((56,), dtype=np.float32)
        row[0:5] = [320.0, 160.0, 20.0, 20.0, 0.9]
        for index in range(17):
            base = 5 + index * 3
            row[base : base + 3] = [320.0 + index, 160.0 + index, 0.8]

        detections = decode_yolo_pose_onnx_output(
            row.reshape(1, 1, 56),
            frame_shape=(360, 640, 3),
            imgsz=640,
            conf_threshold=0.03,
            max_people=0,
            min_pose_confidence=0.12,
            min_bbox_area_ratio=0.01,
        )

        self.assertEqual(detections, [])

    def test_decode_yolo_pose_onnx_output_handles_nms_rows_with_class_column(self) -> None:
        row = np.zeros((57,), dtype=np.float32)
        row[0:6] = [110.0, 40.0, 210.0, 180.0, 0.88, 0.0]
        for index in range(17):
            base = 6 + index * 3
            row[base : base + 3] = [120.0 + index, 60.0 + index, 0.7]

        output = row.reshape(1, 1, 57)
        detections = decode_yolo_pose_onnx_output(
            output,
            frame_shape=(180, 320, 3),
            imgsz=320,
            conf_threshold=0.35,
            max_people=1,
        )

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertAlmostEqual(detection.bbox_confidence, 0.88, places=4)
        self.assertEqual(detection.bbox, [110, 22, 210, 101])
        self.assertEqual(len(detection.keypoints), 17)
        self.assertAlmostEqual(detection.keypoints[0][0], 120.0, places=4)
        self.assertAlmostEqual(detection.keypoints[0][1], 33.75, places=4)
        self.assertAlmostEqual(detection.pose_confidence_mean, 0.7, places=4)

    def test_decode_report_records_reject_reasons_and_quality_metrics(self) -> None:
        good = np.zeros((56,), dtype=np.float32)
        good[0:5] = [320.0, 160.0, 180.0, 120.0, 0.5]
        wall = np.zeros((56,), dtype=np.float32)
        wall[0:5] = [100.0, 100.0, 20.0, 20.0, 0.95]
        for index in range(17):
            base = 5 + index * 3
            good[base : base + 3] = [320.0 + index, 160.0 + index, 0.8]
            wall[base : base + 3] = [100.0 + index, 100.0 + index, 0.2]

        detections, report = decode_yolo_pose_onnx_output(
            np.stack([wall, good]).reshape(1, 2, 56),
            frame_shape=(360, 640, 3),
            imgsz=640,
            conf_threshold=0.03,
            max_people=1,
            min_pose_confidence=0.12,
            min_bbox_area_ratio=0.01,
            return_report=True,
        )

        self.assertEqual(len(detections), 1)
        self.assertEqual(report["accepted_track_count"], 1)
        self.assertGreater(report["rejected_reason_count"]["bbox_area_below_threshold"], 0)
        self.assertGreater(report["ghost_track_rate"], 0.0)
        self.assertGreater(report["pose_confidence_mean"], 0.0)
        self.assertGreater(report["visible_joint_ratio"], 0.0)

    def test_onnx_session_options_are_configurable(self) -> None:
        class FakeSessionOptions:
            def __init__(self) -> None:
                self.intra_op_num_threads = 0
                self.graph_optimization_level = None
                self.execution_mode = None
                self.config_entries: dict[str, str] = {}
                self.enable_profiling = False

            def add_session_config_entry(self, key: str, value: str) -> None:
                self.config_entries[key] = value

        class FakeOrt:
            ORT_ENABLE_ALL = "ORT_ENABLE_ALL"
            ORT_SEQUENTIAL = "ORT_SEQUENTIAL"

            @staticmethod
            def SessionOptions() -> FakeSessionOptions:
                return FakeSessionOptions()

        original_ort = pose_estimator.ort
        pose_estimator.ort = FakeOrt()
        try:
            options = build_onnx_session_options(
                {
                    "intra_op_num_threads": 2,
                    "graph_optimization_level": "ORT_ENABLE_ALL",
                    "execution_mode": "ORT_SEQUENTIAL",
                    "session.intra_op.allow_spinning": "0",
                    "enable_profiling": True,
                }
            )
        finally:
            pose_estimator.ort = original_ort

        self.assertEqual(options.intra_op_num_threads, 2)
        self.assertEqual(options.graph_optimization_level, "ORT_ENABLE_ALL")
        self.assertEqual(options.execution_mode, "ORT_SEQUENTIAL")
        self.assertEqual(options.config_entries["session.intra_op.allow_spinning"], "0")
        self.assertTrue(options.enable_profiling)

    def test_onnx_session_options_reject_unsafe_thread_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "intra_op_num_threads"):
            build_onnx_session_options({"intra_op_num_threads": 8})


if __name__ == "__main__":
    unittest.main()
