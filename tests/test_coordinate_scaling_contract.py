from __future__ import annotations

import unittest

import numpy as np

from edge.pose_estimator import decode_yolo_pose_onnx_output
from server.services.overlay_broadcaster import OverlayBroadcaster
from shared.protocol import BoundingBox, Keypoint, OverlayFrame, SkeletonFrame


class CoordinateScalingContractTests(unittest.TestCase):
    def test_onnx_imgsz_coordinates_scale_to_processing_frame(self) -> None:
        row = np.zeros((56,), dtype=np.float32)
        row[0:5] = [160.0, 160.0, 100.0, 80.0, 0.9]
        for index in range(17):
            base = 5 + index * 3
            row[base : base + 3] = [160.0, 160.0, 0.8]

        detections = decode_yolo_pose_onnx_output(
            row.reshape(1, 1, 56),
            frame_shape=(360, 640, 3),
            imgsz=320,
            conf_threshold=0.35,
            max_people=1,
        )

        self.assertEqual(len(detections), 1)
        self.assertAlmostEqual(detections[0].keypoints[0][0], 320.0, delta=0.5)
        self.assertAlmostEqual(detections[0].keypoints[0][1], 180.0, delta=0.5)

    def test_overlay_frame_declares_source_and_stream_dimensions(self) -> None:
        frame = OverlayFrame(
            camera_id="raspi_cam01",
            frame_id="frame-1",
            source_width=640,
            source_height=360,
            stream_width=1280,
            stream_height=720,
            tracks=[],
        )

        dumped = frame.model_dump(mode="json")

        self.assertEqual(dumped["source_width"], 640)
        self.assertEqual(dumped["source_height"], 360)
        self.assertEqual(dumped["stream_width"], 1280)
        self.assertEqual(dumped["stream_height"], 720)

    def test_broadcaster_preserves_stream_dimensions_from_skeleton_features(self) -> None:
        skeleton = SkeletonFrame(
            camera_id="raspi_cam01",
            frame_id="f-1",
            timestamp_ms=1000,
            track_id=1,
            bbox=BoundingBox(x1=10, y1=20, x2=100, y2=200),
            bbox_confidence=0.9,
            keypoints=[Keypoint(x=1.0, y=2.0, confidence=0.8)],
            pose_confidence_mean=0.8,
            features={
                "source_width": 640,
                "source_height": 360,
                "stream_width": 1280,
                "stream_height": 720,
            },
        )

        frame = OverlayBroadcaster.frame_from_skeleton(skeleton)

        self.assertEqual(frame["source_width"], 640)
        self.assertEqual(frame["source_height"], 360)
        self.assertEqual(frame["stream_width"], 1280)
        self.assertEqual(frame["stream_height"], 720)


if __name__ == "__main__":
    unittest.main()
