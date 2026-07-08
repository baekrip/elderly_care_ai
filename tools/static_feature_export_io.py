from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias


JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ExportMetadata:
    label_names: tuple[str, ...]
    feature_names: tuple[str, ...]
    exported_rows: int
    label_counts: dict[str, int]
    skipped_reasons: dict[str, int]

    def to_json(self, thresholds: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "label_names": list(self.label_names),
            "feature_names": list(self.feature_names),
            "exported_rows": self.exported_rows,
            "label_counts": self.label_counts,
            "skipped_reasons": self.skipped_reasons,
            "thresholds": thresholds,
        }


def _stage_file(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return Path(raw_path)


def _restore_file(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    os.replace(_stage_file(path, previous), path)


def atomic_write_pair(output_csv: Path, csv_text: str, meta_out: Path, meta_text: str) -> None:
    if output_csv.resolve() == meta_out.resolve():
        raise OSError("output CSV and metadata paths must differ")
    previous_csv = output_csv.read_bytes() if output_csv.exists() else None
    previous_meta = meta_out.read_bytes() if meta_out.exists() else None
    staged_csv = _stage_file(output_csv, csv_text.encode("utf-8"))
    staged_meta = _stage_file(meta_out, meta_text.encode("utf-8"))
    csv_replaced = False
    try:
        os.replace(staged_csv, output_csv)
        csv_replaced = True
        os.replace(staged_meta, meta_out)
    except OSError:
        if csv_replaced:
            _restore_file(output_csv, previous_csv)
        _restore_file(meta_out, previous_meta)
        raise
    finally:
        staged_csv.unlink(missing_ok=True)
        staged_meta.unlink(missing_ok=True)
