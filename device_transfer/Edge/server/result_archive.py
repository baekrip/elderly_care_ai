from __future__ import annotations

from pathlib import Path
from typing import Any

from shared.jsonl_rotation import JsonlRotationPolicy, append_jsonl_rows


class ResultArchive:
    def __init__(self, config: dict[str, Any]) -> None:
        archive_cfg = config.get("local_archive", {})
        self.enabled = bool(archive_cfg.get("enabled", True))
        self.base_dir = Path(archive_cfg.get("save_dir", "server/storage/results"))
        self.requests_file = self.base_dir / archive_cfg.get("candidate_requests_file", "candidate_requests.jsonl")
        self.results_file = self.base_dir / archive_cfg.get("stgcn_results_file", "stgcn_results.jsonl")
        self.pattern_file = self.base_dir / archive_cfg.get("pattern_results_file", "pattern_results.jsonl")
        self.clip_json_dir = self.base_dir / archive_cfg.get("clip_json_dir", "clip_json")
        self.clip_requests_file = self.clip_json_dir / archive_cfg.get("clip_requests_file", "server_clip_requests.jsonl")
        self.clip_results_file = self.clip_json_dir / archive_cfg.get("clip_results_file", "server_clip_results.jsonl")
        self.rotation = JsonlRotationPolicy(archive_cfg.get("rotation", {}))
        if self.enabled:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self.clip_json_dir.mkdir(parents=True, exist_ok=True)

    def write_candidate_request(self, payload: dict[str, Any]) -> None:
        self._append(self.requests_file, payload)

    def write_stgcn_result(self, payload: dict[str, Any]) -> None:
        self._append(self.results_file, payload)

    def write_pattern_result(self, payload: dict[str, Any]) -> None:
        self._append(self.pattern_file, payload)

    def write_clip_request(self, payload: dict[str, Any]) -> None:
        self._append(self.clip_requests_file, payload)

    def write_clip_result(self, payload: dict[str, Any]) -> None:
        self._append(self.clip_results_file, payload)

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        append_jsonl_rows(path, [payload], self.rotation)
