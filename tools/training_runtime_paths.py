from __future__ import annotations

from pathlib import Path


def resolve_bundle_model_path(config_path: str | Path, model_path: str | Path) -> Path:
    configured = Path(model_path)
    if configured.is_absolute() or configured.exists():
        return configured.resolve()

    bundle_relative = Path(config_path).resolve().parent.parent / configured
    if bundle_relative.exists():
        return bundle_relative.resolve()
    return configured
