from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.jsonl_rotation import JsonlRotationPolicy, append_jsonl_rows


class LocalOutputWriter:
    def __init__(self, config: dict[str, Any]) -> None:
        output_cfg = config.get("local_output", {})
        self.enabled = bool(output_cfg.get("enabled", False))
        self.save_dir = Path(output_cfg.get("save_dir", "edge/storage/results"))
        self.frames_file = self.save_dir / output_cfg.get("frames_file", "activity_frames.jsonl")
        self.timeline_file = self.save_dir / output_cfg.get("timeline_file", "timeline_segments.jsonl")
        self.candidates_file = self.save_dir / output_cfg.get("candidates_file", "candidate_windows.jsonl")
        self.trigger_debug_file = self.save_dir / output_cfg.get("trigger_debug_file", "trigger_debug.jsonl")
        self.perf_stats_file = self.save_dir / output_cfg.get("perf_stats_file", "perf_stats.jsonl")
        self.rotation = JsonlRotationPolicy(output_cfg.get("rotation", {}))

        if self.enabled:
            self.save_dir.mkdir(parents=True, exist_ok=True)

    def write_frames(self, frames: list[dict[str, Any]]) -> None:
        self._append_rows(self.frames_file, frames)

    def write_timeline_segments(self, segments: list[dict[str, Any]]) -> None:
        self._append_rows(self.timeline_file, segments)

    def write_candidates(self, candidates: list[dict[str, Any]]) -> None:
        self._append_rows(self.candidates_file, candidates)

    def write_trigger_debug(self, rows: list[dict[str, Any]]) -> None:
        self._append_rows(self.trigger_debug_file, rows)

    def write_perf_stats(self, rows: list[dict[str, Any]]) -> None:
        self._append_rows(self.perf_stats_file, rows)

    def _append_rows(self, path: Path, rows: list[dict[str, Any]]) -> None:
        if not self.enabled or not rows:
            return
        append_jsonl_rows(path, rows, self.rotation)
