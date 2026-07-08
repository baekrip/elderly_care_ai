from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from edge.rtsp_streamer import RTSPStreamer


class RTSPStreamerTests(unittest.TestCase):
    def test_frame_pipe_stream_uses_stdin_without_opening_camera_device(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.stdin = Mock()
        frame = np.zeros((24, 32, 3), dtype=np.uint8)
        frame[0, 0] = [10, 20, 30]
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/raspi_cam01",
            },
        }

        with patch("edge.rtsp_streamer.shutil.which", return_value="ffmpeg"), patch(
            "edge.rtsp_streamer.subprocess.Popen",
            return_value=process,
        ) as popen:
            streamer = RTSPStreamer(config)
            streamer.start()
            streamer.write_frame(frame)
            streamer.stop()

        command = popen.call_args.args[0]
        self.assertIn("pipe:0", command)
        self.assertEqual(command[command.index("-pix_fmt") + 1], "bgr24")
        self.assertEqual(command[command.index("-r") + 1], "30")
        self.assertEqual(command[command.index("-rtsp_transport") + 1], "tcp")
        self.assertNotIn("/dev/video0", command)
        self.assertNotIn("v4l2", command)
        process.stdin.write.assert_called_once_with(frame.tobytes())

    def test_frame_pipe_can_use_rgb24_input_for_picamera_color_probe(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.stdin = Mock()
        frame = np.zeros((24, 32, 3), dtype=np.uint8)
        frame[0, 0] = [10, 20, 30]
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/raspi_cam01",
                "input_pix_fmt": "rgb24",
            },
        }

        with patch("edge.rtsp_streamer.shutil.which", return_value="ffmpeg"), patch(
            "edge.rtsp_streamer.subprocess.Popen",
            return_value=process,
        ) as popen:
            streamer = RTSPStreamer(config)
            streamer.start()
            streamer.write_frame(frame)
            streamer.stop()

        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-pix_fmt") + 1], "rgb24")
        process.stdin.write.assert_called_once_with(frame.tobytes())

    def test_frame_pipe_can_force_h264_output_pixel_format(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.stdin = Mock()
        frame = np.zeros((24, 32, 3), dtype=np.uint8)
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/raspi_cam01",
                "input_pix_fmt": "rgb24",
                "output_pix_fmt": "yuv420p",
            },
        }

        with patch("edge.rtsp_streamer.shutil.which", return_value="ffmpeg"), patch(
            "edge.rtsp_streamer.subprocess.Popen",
            return_value=process,
        ) as popen:
            streamer = RTSPStreamer(config)
            streamer.start()
            streamer.write_frame(frame)
            streamer.stop()

        command = popen.call_args.args[0]
        pix_fmt_indexes = [index for index, value in enumerate(command) if value == "-pix_fmt"]
        self.assertEqual(command[pix_fmt_indexes[0] + 1], "rgb24")
        self.assertEqual(command[pix_fmt_indexes[-1] + 1], "yuv420p")

    def test_frame_pipe_v4l2_encoder_omits_libx264_only_options(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.stdin = Mock()
        frame = np.zeros((24, 32, 3), dtype=np.uint8)
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/raspi_cam01",
                "input_pix_fmt": "rgb24",
                "encoder": "h264_v4l2m2m",
            },
        }

        with patch("edge.rtsp_streamer.shutil.which", return_value="ffmpeg"), patch(
            "edge.rtsp_streamer.subprocess.Popen",
            return_value=process,
        ) as popen:
            streamer = RTSPStreamer(config)
            streamer.start()
            streamer.write_frame(frame)
            streamer.stop()

        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-c:v") + 1], "h264_v4l2m2m")
        self.assertNotIn("-preset", command)
        self.assertNotIn("-tune", command)

    def test_overlay_enabled_draws_bbox_and_keypoints_before_streaming(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.stdin = Mock()
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        detection = SimpleNamespace(bbox=[10, 12, 60, 70], keypoints=[])
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/raspi_cam01",
                "overlay_enabled": True,
            },
        }

        with patch("edge.rtsp_streamer.shutil.which", return_value="ffmpeg"), patch(
            "edge.rtsp_streamer.subprocess.Popen",
            return_value=process,
        ):
            streamer = RTSPStreamer(config)
            streamer.start()
            streamer.write_frame(
                frame,
                [
                    {
                        "detection": detection,
                        "action_label": "WALKING",
                        "risk_label": "NORMAL",
                        "risk_confidence": 0.12,
                    }
                ],
            )
            streamer.stop()

        written = process.stdin.write.call_args.args[0]
        self.assertNotEqual(written, frame.tobytes())
        self.assertEqual(frame.sum(), 0)

    def test_overlay_enabled_draws_status_badge_without_detections(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.stdin = Mock()
        frame = np.zeros((80, 160, 3), dtype=np.uint8)
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/raspi_cam01",
                "overlay_enabled": True,
            },
        }

        with patch("edge.rtsp_streamer.shutil.which", return_value="ffmpeg"), patch(
            "edge.rtsp_streamer.subprocess.Popen",
            return_value=process,
        ), patch("edge.rtsp_streamer.time.strftime", return_value="12:34:56"):
            streamer = RTSPStreamer(config)
            streamer.start()
            streamer.write_frame(frame, [])
            streamer.stop()

        written = process.stdin.write.call_args.args[0]
        self.assertNotEqual(written, frame.tobytes())
        self.assertEqual(frame.sum(), 0)

    def test_overlay_hides_action_and_risk_labels_by_default(self) -> None:
        frame = np.zeros((80, 100, 3), dtype=np.uint8)
        detection = SimpleNamespace(
            bbox=[10, 12, 60, 70],
            keypoints=[[20.0 + index, 25.0 + index, 0.9] for index in range(17)],
        )
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/P001",
                "overlay_enabled": True,
            },
        }
        streamer = RTSPStreamer(config)
        labels: list[str] = []
        original_draw_label = streamer._draw_label

        def record_label(
            target_frame: np.ndarray,
            text: str,
            x: int,
            y: int,
            occupied: list[tuple[int, int, int, int]] | None = None,
        ) -> tuple[int, int, int, int] | None:
            labels.append(text)
            return original_draw_label(target_frame, text, x, y, occupied)

        streamer._draw_label = record_label
        streamer._draw_overlays(
            frame,
            [
                {
                    "detection": detection,
                    "action_label": "UNKNOWN",
                    "risk_label": "NORMAL",
                }
            ],
        )

        self.assertEqual(len(labels), 1)
        self.assertIn("OVERLAY ON", labels[0])
        self.assertNotIn("UNKNOWN NORMAL", " ".join(labels))

    def test_overlay_motion_compensation_tracks_keypoints_between_pose_results(self) -> None:
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/P001",
                "overlay_enabled": True,
                "overlay_keypoint_threshold": 0.1,
                "overlay_motion_compensation": True,
            },
        }
        streamer = RTSPStreamer(config)
        first_frame = np.zeros((80, 100, 3), dtype=np.uint8)
        second_frame = np.zeros((80, 100, 3), dtype=np.uint8)
        first_frame[30:36, 30:36] = 255
        second_frame[30:36, 40:46] = 255
        detection = SimpleNamespace(
            bbox=[25, 25, 45, 55],
            keypoints=[[33.0, 33.0, 0.9] for _ in range(17)],
        )
        overlays = [{"detection": detection, "action_label": "POSE", "risk_label": "NORMAL"}]

        first = streamer._motion_compensated_overlays(first_frame, overlays)
        second = streamer._motion_compensated_overlays(second_frame, overlays)

        self.assertEqual(first[0]["keypoints"][0][0], 33.0)
        self.assertGreater(second[0]["keypoints"][0][0], 38.0)
        self.assertGreater(second[0]["bbox"][0], 30.0)

    def test_overlay_labels_shift_when_boxes_overlap(self) -> None:
        frame = np.zeros((120, 220, 3), dtype=np.uint8)
        config = {
            "camera": {"fps": 30},
            "stream": {
                "enabled": True,
                "mode": "frame_pipe",
                "output_url": "rtsp://127.0.0.1:8554/raspi_cam01",
                "overlay_enabled": True,
            },
        }
        streamer = RTSPStreamer(config)
        first = streamer._draw_label(frame, "UNKNOWN NORMAL 0.00", 20, 50, [])
        second = streamer._draw_label(frame, "UNKNOWN NORMAL 0.00", 24, 52, [first])

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertFalse(
            first[2] > second[0] and second[2] > first[0] and first[3] > second[1] and second[3] > first[1]
        )

    def test_legacy_external_command_is_still_supported(self) -> None:
        process = Mock()
        process.poll.return_value = None
        config = {
            "camera": {"fps": 10},
            "stream": {
                "enabled": True,
                "mode": "external",
                "command": "echo start-stream",
            },
        }

        with patch("edge.rtsp_streamer.subprocess.Popen", return_value=process) as popen:
            RTSPStreamer(config).start()

        popen.assert_called_once_with("echo start-stream", shell=True)


if __name__ == "__main__":
    unittest.main()
