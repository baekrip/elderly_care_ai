from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _stage(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return Path(raw_path)


def _restore(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    os.replace(_stage(path, previous), path)


def write_atomic_pair(
    first_path: Path,
    first_content: bytes,
    second_path: Path,
    second_content: bytes,
) -> None:
    if first_path.resolve() == second_path.resolve():
        raise OSError("artifact paths must differ")
    previous_first = first_path.read_bytes() if first_path.exists() else None
    previous_second = second_path.read_bytes() if second_path.exists() else None
    staged_first = _stage(first_path, first_content)
    staged_second = _stage(second_path, second_content)
    first_replaced = False
    try:
        os.replace(staged_first, first_path)
        first_replaced = True
        os.replace(staged_second, second_path)
    except (OSError, KeyboardInterrupt):
        if first_replaced:
            _restore(first_path, previous_first)
        _restore(second_path, previous_second)
        raise
    finally:
        staged_first.unlink(missing_ok=True)
        staged_second.unlink(missing_ok=True)
