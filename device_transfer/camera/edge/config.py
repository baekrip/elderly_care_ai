from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    for key in ["camera", "model", "classification", "sender", "buffer", "stream", "server", "preprocess", "local_output"]:
        data.setdefault(key, {})
    return data
