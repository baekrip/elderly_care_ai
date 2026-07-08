from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from server.api.clips import resolve_clip_upload_path


class ClipPathSafetyTests(unittest.TestCase):
    def test_rejects_camera_id_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(HTTPException) as ctx:
                resolve_clip_upload_path(
                    video_root=Path(tmpdir),
                    clip_id="clip01",
                    camera_id="../outside",
                    filename="event.mp4",
                )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_upload_filename_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(HTTPException) as ctx:
                resolve_clip_upload_path(
                    video_root=Path(tmpdir),
                    clip_id="clip01",
                    camera_id="raspi_cam01",
                    filename="..\\outside.mp4",
                )

        self.assertEqual(ctx.exception.status_code, 400)

    def test_resolves_safe_clip_path_under_video_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = resolve_clip_upload_path(
                video_root=Path(tmpdir),
                clip_id="clip01",
                camera_id="raspi_cam01",
                filename="event.mp4",
            )

        self.assertEqual(path.name, "clip01_event.mp4")
        self.assertEqual(path.parent.name, "raspi_cam01")


if __name__ == "__main__":
    unittest.main()
