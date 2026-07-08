from __future__ import annotations

import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from math import ceil
from pathlib import Path
import json

import cv2
import numpy as np


@dataclass
class SegmentInfo:
    path: Path
    started_at_ms: int
    ended_at_ms: int


class RollingVideoBuffer:
    def __init__(
        self,
        save_dir: str,
        fps: int,
        resolution: tuple[int, int],
        segment_duration_sec: int = 5,
        max_segments: int | None = None,
        max_buffer_minutes: int | float | None = None,
        codec: str = "mp4v",
        index_file: str = "segments_index.jsonl",
        write_enabled: bool = True,  # OPT-POSE-2: False이면 인코딩/파일 쓰기 전체 스킵
    ) -> None:
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.fps = fps
        self.resolution = resolution
        self.segment_duration_sec = segment_duration_sec
        if max_segments is None:
            if max_buffer_minutes is None:
                max_segments = 36
            else:
                max_segments = max(1, ceil((float(max_buffer_minutes) * 60.0) / max(segment_duration_sec, 1)))
        self.max_segments = int(max_segments)
        self.codec = codec
        self.index_path = self.save_dir / index_file
        self.write_enabled = bool(write_enabled)  # OPT-POSE-2
        self._segments: deque[SegmentInfo] = deque()
        self._writer: cv2.VideoWriter | None = None
        self._segment_started_at_ms = 0
        self._current_path: Path | None = None
        self._frames_written = 0
        self._last_timestamp_ms = 0

    def write(self, frame: np.ndarray, timestamp_ms: int) -> None:
        # OPT-POSE-2: write_enabled=False 시 인코딩 및 파일 I/O 전체 스킵
        if not self.write_enabled:
            self._last_timestamp_ms = timestamp_ms
            return
        frame_h, frame_w = frame.shape[:2]
        if self._writer is None and (not self.resolution or self.resolution[0] <= 0 or self.resolution[1] <= 0):
            self.resolution = (frame_w, frame_h)
        if self._writer is None:
            self._start_segment(timestamp_ms)
        if self._frames_written >= self.fps * self.segment_duration_sec:
            self._close_segment(timestamp_ms)
            self._start_segment(timestamp_ms)

        if (frame_w, frame_h) != self.resolution:
            frame = cv2.resize(frame, self.resolution)
        self._writer.write(frame)
        self._frames_written += 1
        self._last_timestamp_ms = timestamp_ms

    def current_frame_ref(self, timestamp_ms: int) -> dict[str, int | str] | None:
        if self._current_path is not None and self._segment_started_at_ms:
            return {
                "segment_id": self._current_path.name,
                "segment_offset_ms": max(0, timestamp_ms - self._segment_started_at_ms),
            }
        if self._segments:
            segment = self._segments[-1]
            return {
                "segment_id": segment.path.name,
                "segment_offset_ms": max(0, timestamp_ms - segment.started_at_ms),
            }
        return None

    def export_clip(self, event_id: str, center_ts_ms: int, pre_ms: int, post_ms: int) -> Path | None:
        if self._writer is not None and self._current_path is not None:
            self._writer.release()
            self._writer = None
            ended_at_ms = max(self._last_timestamp_ms, self._segment_started_at_ms)
            self._append_segment(
                SegmentInfo(
                    path=self._current_path,
                    started_at_ms=self._segment_started_at_ms,
                    ended_at_ms=ended_at_ms,
                )
            )
        selected = [
            segment
            for segment in self._segments
            if segment.started_at_ms <= center_ts_ms + post_ms and segment.ended_at_ms >= center_ts_ms - pre_ms
        ]
        if not selected:
            return None

        output_path = self.save_dir / f"clip_{event_id}.mp4"
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            manifest = self.save_dir / f"clip_{event_id}.txt"
            with manifest.open("w", encoding="utf-8") as file:
                for segment in selected:
                    file.write(f"file '{segment.path.as_posix()}'\n")
            subprocess.run(
                [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", str(output_path)],
                check=False,
                capture_output=True,
            )
            manifest.unlink(missing_ok=True)
            if output_path.exists():
                return output_path

        writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*self.codec), self.fps, self.resolution)
        for segment in selected:
            capture = cv2.VideoCapture(str(segment.path))
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                writer.write(frame)
            capture.release()
        writer.release()
        return output_path if output_path.exists() else None

    def _start_segment(self, timestamp_ms: int) -> None:
        self._segment_started_at_ms = timestamp_ms
        self._frames_written = 0
        self._current_path = self.save_dir / f"segment_{timestamp_ms}.mp4"
        self._writer = cv2.VideoWriter(
            str(self._current_path),
            cv2.VideoWriter_fourcc(*self.codec),
            self.fps,
            self.resolution,
        )

    def _close_segment(self, timestamp_ms: int) -> None:
        if self._writer is None or self._current_path is None:
            return
        self._writer.release()
        self._writer = None
        self._append_segment(
            SegmentInfo(
                path=self._current_path,
                started_at_ms=self._segment_started_at_ms,
                ended_at_ms=timestamp_ms,
            )
        )

    def _append_segment(self, segment: SegmentInfo) -> None:
        self._segments.append(segment)
        self._write_index(segment)
        while len(self._segments) > self.max_segments:
            old = self._segments.popleft()
            old.path.unlink(missing_ok=True)

    def _write_index(self, segment: SegmentInfo) -> None:
        record = {
            "segment_id": segment.path.name,
            "path": str(segment.path),
            "started_at_ms": segment.started_at_ms,
            "ended_at_ms": segment.ended_at_ms,
        }
        with self.index_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
