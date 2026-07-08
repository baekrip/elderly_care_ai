from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from edge.ws_sender import SkeletonWebSocketSender
from shared.protocol import BoundingBox, Keypoint, SkeletonFrame, SkeletonFrameBatch


class SkeletonWebSocketSenderTests(unittest.TestCase):
    def test_sender_uses_only_stdlib_async_runtime(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "device_transfer" / "camera1" / "edge" / "ws_sender.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("import asyncio", source)
        self.assertNotIn("import anyio", source)

    def _frame(self, frame_id: str = "f1") -> SkeletonFrame:
        return SkeletonFrame(
            camera_id="raspi_cam01",
            frame_id=frame_id,
            timestamp_ms=1,
            track_id=1,
            bbox=BoundingBox(x1=0, y1=0, x2=100, y2=200),
            bbox_confidence=0.9,
            keypoints=[Keypoint(x=1.0, y=2.0, confidence=0.8) for _ in range(17)],
            pose_confidence_mean=0.8,
            features={"center_velocity_px_s": 12.0},
        )

    def test_build_message_serializes_skeleton_batch(self) -> None:
        sender = SkeletonWebSocketSender({"server": {"skeleton_ws_url": "ws://orin/ws/skeleton/raspi_cam01"}})
        frame = self._frame()

        message = sender.build_message(SkeletonFrameBatch(frames=[frame]))
        payload = json.loads(message)

        self.assertEqual(payload["frames"][0]["camera_id"], "raspi_cam01")
        self.assertEqual(payload["frames"][0]["features"]["center_velocity_px_s"], 12.0)

    def test_failed_send_is_saved_to_pending_file(self) -> None:
        class _FailingWebsockets:
            @staticmethod
            def connect(*args, **kwargs):
                raise OSError("offline")

        with tempfile.TemporaryDirectory() as tmpdir:
            pending_file = Path(tmpdir) / "skeleton_ws_pending.jsonl"
            sender = SkeletonWebSocketSender(
                {
                    "server": {
                        "skeleton_ws_url": "ws://orin/ws/skeleton/raspi_cam01",
                        "skeleton_ws_pending_file": str(pending_file),
                    }
                }
            )
            sys.modules["websockets"] = _FailingWebsockets
            with self.assertRaises(OSError):
                sender.send_batch_sync(SkeletonFrameBatch(frames=[self._frame()]))
            sys.modules.pop("websockets", None)

            rows = pending_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0])
            self.assertEqual(payload["frames"][0]["sequence_id"], 1)

    def test_pending_messages_are_replayed_before_current_batch(self) -> None:
        sent: list[str] = []

        class _Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, message: str) -> None:
                sent.append(message)

        class _WorkingWebsockets:
            @staticmethod
            def connect(*args, **kwargs):
                return _Connection()

        with tempfile.TemporaryDirectory() as tmpdir:
            pending_file = Path(tmpdir) / "skeleton_ws_pending.jsonl"
            pending_file.write_text('{"frames":[{"frame_id":"old"}]}\n', encoding="utf-8")
            sender = SkeletonWebSocketSender(
                {
                    "server": {
                        "skeleton_ws_url": "ws://orin/ws/skeleton/raspi_cam01",
                        "skeleton_ws_pending_file": str(pending_file),
                    }
                }
            )
            sys.modules["websockets"] = _WorkingWebsockets
            sender.send_batch_sync(SkeletonFrameBatch(frames=[self._frame("new")]))
            sys.modules.pop("websockets", None)

            self.assertEqual(json.loads(sent[0])["frames"][0]["frame_id"], "old")
            self.assertEqual(json.loads(sent[1])["frames"][0]["frame_id"], "new")
            self.assertFalse(pending_file.exists())

    def test_send_batch_retries_with_bounded_backoff(self) -> None:
        attempts = 0
        delays: list[float] = []

        class _Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, message: str) -> None:
                return None

        class _FailingThenWorkingWebsockets:
            @staticmethod
            def connect(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise OSError(f"offline-{attempts}")
                return _Connection()

        async def _record_sleep(delay: float) -> None:
            delays.append(delay)

        sender = SkeletonWebSocketSender(
            {
                "server": {
                    "skeleton_ws_url": "ws://orin/ws/skeleton/raspi_cam01",
                    "skeleton_ws_max_reconnect_attempts": 2,
                    "skeleton_ws_reconnect_backoff_initial_sec": 0.1,
                    "skeleton_ws_reconnect_backoff_max_sec": 0.15,
                }
            }
        )
        sys.modules["websockets"] = _FailingThenWorkingWebsockets
        with patch("edge.ws_sender.asyncio.sleep", new=_record_sleep):
            sender.send_batch_sync(SkeletonFrameBatch(frames=[self._frame()]))
        sys.modules.pop("websockets", None)

        self.assertEqual(attempts, 3)
        self.assertEqual(delays, [0.1, 0.15])
        self.assertEqual(sender.transport_diagnostics()["reconnect_attempt_count"], 2)

    def test_pending_queue_drops_oldest_and_counts_dropped_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sender = SkeletonWebSocketSender(
                {
                    "server": {
                        "skeleton_ws_url": "ws://orin/ws/skeleton/raspi_cam01",
                        "skeleton_ws_pending_file": str(Path(tmpdir) / "pending.jsonl"),
                        "skeleton_ws_max_pending_batches": 2,
                    }
                }
            )
            messages = [
                json.dumps({"frames": [{"frame_id": "old"}]}),
                json.dumps({"frames": [{"frame_id": "middle-1"}, {"frame_id": "middle-2"}]}),
                json.dumps({"frames": [{"frame_id": "new-1"}, {"frame_id": "new-2"}, {"frame_id": "new-3"}]}),
            ]

            sender._write_pending_messages(messages)

            self.assertEqual(sender._read_pending_messages(), messages[1:])
            diagnostics = sender.transport_diagnostics()
            self.assertEqual(diagnostics["dropped_pending_batch_count"], 1)
            self.assertEqual(diagnostics["dropped_pending_frame_count"], 1)

    def test_sequence_ids_remain_monotonic_across_reconnect(self) -> None:
        sent: list[str] = []
        attempts = 0

        class _Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, message: str) -> None:
                nonlocal attempts
                sent.append(message)
                attempts += 1
                if attempts == 1:
                    raise OSError("disconnect")

        class _WorkingWebsockets:
            @staticmethod
            def connect(*args, **kwargs):
                return _Connection()

        async def _no_sleep(delay: float) -> None:
            return None

        sender = SkeletonWebSocketSender(
            {
                "server": {
                    "skeleton_ws_url": "ws://orin/ws/skeleton/raspi_cam01",
                    "skeleton_ws_max_reconnect_attempts": 1,
                }
            }
        )
        first_batch = SkeletonFrameBatch(frames=[self._frame("f1"), self._frame("f2")])
        second_batch = SkeletonFrameBatch(frames=[self._frame("f3")])
        sys.modules["websockets"] = _WorkingWebsockets
        with patch("edge.ws_sender.asyncio.sleep", new=_no_sleep):
            sender.send_batch_sync(first_batch)
            sender.send_batch_sync(second_batch)
        sys.modules.pop("websockets", None)

        sequence_samples = [
            [frame["sequence_id"] for frame in json.loads(message)["frames"]]
            for message in sent
        ]
        self.assertEqual(sequence_samples, [[1, 2], [1, 2], [3]])

    def test_transport_diagnostics_excludes_secrets_and_payloads(self) -> None:
        sender = SkeletonWebSocketSender(
            {
                "camera": {"camera_id": "raspi_cam01"},
                "server": {
                    "skeleton_ws_url": "ws://orin/ws?token=do-not-expose",
                    "ingest_api_key": "test-secret-api-key",
                },
            }
        )

        diagnostics = sender.transport_diagnostics()

        self.assertEqual(
            set(diagnostics),
            {
                "pending_batch_count",
                "dropped_pending_batch_count",
                "dropped_pending_frame_count",
                "reconnect_attempt_count",
            },
        )
        serialized = json.dumps(diagnostics)
        for secret in ["do-not-expose", "test-secret-api-key", "X-Edge-API-Key", "ws://"]:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, serialized)

    def test_send_batch_uses_configured_ingest_api_key_header(self) -> None:
        connect_calls: list[dict] = []

        class _Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, message: str) -> None:
                return None

        class _WorkingWebsockets:
            @staticmethod
            def connect(*args, **kwargs):
                connect_calls.append({"args": args, "kwargs": kwargs})
                return _Connection()

        sender = SkeletonWebSocketSender(
            {
                "camera": {"camera_id": "raspi_cam01"},
                "server": {
                    "skeleton_ws_url": "ws://orin/ws/skeleton/raspi_cam01",
                    "ingest_api_key": "edge-secret",
                    "ingest_api_key_header": "X-Edge-API-Key",
                }
            }
        )
        sys.modules["websockets"] = _WorkingWebsockets
        sender.send_batch_sync(SkeletonFrameBatch(frames=[self._frame("new")]))
        sys.modules.pop("websockets", None)

        self.assertEqual(
            connect_calls[0]["kwargs"]["additional_headers"],
            {"X-Edge-API-Key": "edge-secret", "X-Device-ID": "raspi_cam01"},
        )


if __name__ == "__main__":
    unittest.main()
