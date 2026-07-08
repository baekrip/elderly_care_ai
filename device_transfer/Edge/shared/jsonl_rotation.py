from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable


class JsonlRotationPolicy:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.subdir = str(cfg.get("subdir", "daily")).strip() or "daily"
        self.retention_days = int(cfg.get("retention_days", 0) or 0)

    def output_path(self, base_path: Path) -> Path:
        if not self.enabled:
            return base_path
        return base_path.parent / self.subdir / date.today().isoformat() / base_path.name

    def prune(self, base_path: Path) -> None:
        if not self.enabled or self.retention_days <= 0:
            return
        rotation_root = base_path.parent / self.subdir
        if not rotation_root.exists():
            return
        cutoff = date.today() - timedelta(days=self.retention_days)
        for day_dir in rotation_root.iterdir():
            if not day_dir.is_dir():
                continue
            try:
                day = date.fromisoformat(day_dir.name)
            except ValueError:
                continue
            if day >= cutoff:
                continue
            for jsonl_file in day_dir.glob("*.jsonl"):
                jsonl_file.unlink(missing_ok=True)
            try:
                day_dir.rmdir()
            except OSError:
                pass


def append_jsonl_rows(base_path: Path, rows: Iterable[dict[str, Any]], rotation: JsonlRotationPolicy) -> None:
    path = rotation.output_path(base_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    rotation.prune(base_path)
