from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import cv2
import numpy as np

from edge.clip_manager import ClipManager
from shared.protocol import ClipRequestEvent


class _FakeBuffer:
    def __init__(self, clip_path: Path) -> None:
        self.clip_path = clip_path
        self.calls: list[tuple[str, int, int, int]] = []

    def export_clip(self, event_id: str, center_ts_ms: int, pre_ms: int, post_ms: int) -> Path | None:
        self.calls.append((event_id, center_ts_ms, pre_ms, post_ms))
        return self.clip_path


class _FakeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.upload_calls: list[tuple[Path, dict[str, str]]] = []
        self.fail = fail

    def upload_clip(self, file_path: Path, metadata: dict[str, str]) -> None:
        self.upload_calls.append((file_path, metadata))
        if self.fail:
            raise RuntimeError("upload failed")


class ClipManagerTests(unittest.TestCase):
    def test_handle_clip_request_writes_local_clip_json_without_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip_path = tmp / "clip_evt01.mp4"
            clip_path.write_bytes(b"fake")
            config = {
                "camera": {"camera_id": "cam01"},
                "server": {"enabled": False, "upload_clip_enabled": False},
                "local_output": {
                    "enabled": True,
                    "clip_json_dir": str(tmp / "clip_json"),
                    "clip_requests_file": "edge_clip_requests.jsonl",
                    "clip_results_file": "edge_clip_results.jsonl",
                },
            }
            client = _FakeClient()
            manager = ClipManager(config, _FakeBuffer(clip_path), client)
            event = ClipRequestEvent(
                event_id="evt01",
                camera_id="cam01",
                timestamp_ms=123456,
                label="DROP",
                pre_clip_ms=90_000,
                post_clip_ms=90_000,
                reason="DROP:danger",
            )

            result_path = manager.handle_clip_request(event)

            self.assertEqual(result_path, clip_path)
            self.assertEqual(client.upload_calls, [])
            request_file = tmp / "clip_json" / "edge_clip_requests.jsonl"
            result_file = tmp / "clip_json" / "edge_clip_results.jsonl"
            self.assertTrue(request_file.exists())
            self.assertTrue(result_file.exists())
            request_row = json.loads(request_file.read_text(encoding="utf-8").splitlines()[-1])
            result_row = json.loads(result_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(request_row["event_id"], "evt01")
            self.assertEqual(result_row["clip_path"], str(clip_path))

    def test_handle_clip_request_daily_rotates_clip_request_and_result_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            today = date.today().isoformat()
            clip_path = tmp / "clip_evt01.mp4"
            clip_path.write_bytes(b"fake")
            config = {
                "camera": {"camera_id": "cam01"},
                "server": {"enabled": False, "upload_clip_enabled": False},
                "local_output": {
                    "enabled": True,
                    "clip_json_dir": str(tmp / "clip_json"),
                    "clip_requests_file": "edge_clip_requests.jsonl",
                    "clip_results_file": "edge_clip_results.jsonl",
                    "rotation": {"enabled": True, "retention_days": 7},
                },
            }
            manager = ClipManager(config, _FakeBuffer(clip_path), _FakeClient())
            event = ClipRequestEvent(event_id="evt01", camera_id="cam01", timestamp_ms=123456, label="DROP")

            manager.handle_clip_request(event)

            request_file = tmp / "clip_json" / "daily" / today / "edge_clip_requests.jsonl"
            result_file = tmp / "clip_json" / "daily" / today / "edge_clip_results.jsonl"
            self.assertTrue(request_file.exists())
            self.assertTrue(result_file.exists())

    def test_handle_clip_request_uses_explicit_clip_interval_and_records_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip_path = tmp / "clip_evt02.mp4"
            clip_path.write_bytes(b"fake")
            config = {
                "camera": {"camera_id": "cam01"},
                "server": {"enabled": False, "upload_clip_enabled": False},
                "buffer": {
                    "danger_clip_max_duration_ms": 600_000,
                    "storage_mode": "segment_ring",
                },
                "local_output": {
                    "enabled": True,
                    "clip_json_dir": str(tmp / "clip_json"),
                    "clip_requests_file": "edge_clip_requests.jsonl",
                    "clip_results_file": "edge_clip_results.jsonl",
                },
            }
            buffer = _FakeBuffer(clip_path)
            manager = ClipManager(config, buffer, _FakeClient())
            event = ClipRequestEvent(
                event_id="evt02",
                camera_id="cam01",
                timestamp_ms=1_000_000,
                label="DROP",
                clip_start_ms=950_000,
                clip_end_ms=1_120_000,
                clip_duration_ms=170_000,
                storage_policy="segment_ring",
            )

            result_path = manager.handle_clip_request(event)

            self.assertEqual(result_path, clip_path)
            self.assertEqual(buffer.calls[-1], ("evt02", 1_000_000, 50_000, 120_000))
            result_file = tmp / "clip_json" / "edge_clip_results.jsonl"
            result_row = json.loads(result_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(result_row["clip_start_ms"], 950_000)
            self.assertEqual(result_row["clip_end_ms"], 1_120_000)
            self.assertEqual(result_row["clip_duration_ms"], 170_000)
            self.assertEqual(result_row["storage_policy"], "segment_ring")

    def test_handle_clip_request_records_pending_upload_when_upload_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip_path = tmp / "clip_evt03.mp4"
            clip_path.write_bytes(b"fake")
            config = {
                "camera": {"camera_id": "cam01"},
                "server": {"enabled": True, "upload_clip_enabled": True},
                "local_output": {
                    "enabled": True,
                    "clip_json_dir": str(tmp / "clip_json"),
                    "clip_requests_file": "edge_clip_requests.jsonl",
                    "clip_results_file": "edge_clip_results.jsonl",
                    "clip_upload_pending_file": "clip_upload_pending.jsonl",
                },
            }
            manager = ClipManager(config, _FakeBuffer(clip_path), _FakeClient(fail=True))
            event = ClipRequestEvent(
                event_id="evt03",
                camera_id="cam01",
                timestamp_ms=123456,
                label="DROP",
            )

            result_path = manager.handle_clip_request(event)

            self.assertEqual(result_path, clip_path)
            pending_file = tmp / "clip_json" / "clip_upload_pending.jsonl"
            self.assertTrue(pending_file.exists())
            pending_row = json.loads(pending_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(pending_row["event_id"], "evt03")
            self.assertEqual(pending_row["clip_path"], str(clip_path))
            self.assertEqual(pending_row["status"], "upload_failed")

    def test_handle_clip_request_uploads_blurred_clip_when_privacy_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip_path = tmp / "clip_evt04.mp4"
            writer = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (32, 32))
            writer.write(np.full((32, 32, 3), 120, dtype=np.uint8))
            writer.release()
            config = {
                "camera": {"camera_id": "cam01"},
                "server": {"enabled": True, "upload_clip_enabled": True},
                "privacy": {"face_blur_enabled": True},
                "local_output": {
                    "enabled": True,
                    "clip_json_dir": str(tmp / "clip_json"),
                    "clip_requests_file": "edge_clip_requests.jsonl",
                    "clip_results_file": "edge_clip_results.jsonl",
                    "clip_upload_pending_file": "clip_upload_pending.jsonl",
                },
            }
            client = _FakeClient()
            manager = ClipManager(config, _FakeBuffer(clip_path), client)
            event = ClipRequestEvent(
                event_id="evt04",
                camera_id="cam01",
                timestamp_ms=123456,
                label="DROP",
            )

            result_path = manager.handle_clip_request(event)

            self.assertEqual(result_path, clip_path)
            self.assertEqual(client.upload_calls[0][0].name, "clip_evt04_blurred.mp4")
            self.assertTrue(client.upload_calls[0][0].exists())

    def test_retry_pending_uploads_removes_successful_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            clip_path = tmp / "clip_evt05.mp4"
            clip_path.write_bytes(b"fake")
            config = {
                "camera": {"camera_id": "cam01"},
                "server": {"enabled": True, "upload_clip_enabled": True},
                "local_output": {
                    "enabled": True,
                    "clip_json_dir": str(tmp / "clip_json"),
                    "clip_results_file": "edge_clip_results.jsonl",
                    "clip_upload_pending_file": "clip_upload_pending.jsonl",
                },
            }
            manager = ClipManager(config, _FakeBuffer(clip_path), _FakeClient())
            pending_file = tmp / "clip_json" / "clip_upload_pending.jsonl"
            pending_file.parent.mkdir(parents=True, exist_ok=True)
            pending_file.write_text(
                json.dumps(
                    {
                        "event_id": "evt05",
                        "camera_id": "cam01",
                        "status": "upload_failed",
                        "clip_path": str(clip_path),
                        "metadata": {"event_id": "evt05"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = manager.retry_pending_uploads()

            self.assertEqual(result["attempted"], 1)
            self.assertEqual(result["uploaded"], 1)
            self.assertFalse(pending_file.exists())
            result_file = tmp / "clip_json" / "edge_clip_results.jsonl"
            result_row = json.loads(result_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(result_row["status"], "uploaded_retry")


if __name__ == "__main__":
    unittest.main()
