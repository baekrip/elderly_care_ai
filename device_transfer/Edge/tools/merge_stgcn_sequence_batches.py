from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

import numpy as np


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge ST-GCN NPZ sequence batches without running training")
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument(
        "--meta-inputs",
        nargs="+",
        default=None,
        help="Per-batch ST-GCN meta JSON files that define each input's label_names order",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--meta-out", default="experiments/behavior_training/reports/stgcn_batch_merge_meta.json")
    return parser


def write_meta(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_label_names(meta_path: Path) -> list[str]:
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    labels = payload.get("label_names")
    if not isinstance(labels, list) or not labels or not all(isinstance(label, str) for label in labels):
        raise ValueError(f"{meta_path} does not contain a non-empty string label_names list")
    return labels


def remap_labels(labels: np.ndarray, source_label_names: list[str], target_label_names: list[str]) -> np.ndarray:
    remapped: list[int] = []
    for label in labels.tolist():
        label_index = int(label)
        if label_index < 0 or label_index >= len(source_label_names):
            raise ValueError(f"label index {label_index} is outside source label_names")
        label_name = source_label_names[label_index]
        remapped.append(target_label_names.index(label_name))
    return np.asarray(remapped, dtype=np.int64)


def main() -> int:
    args = build_parser().parse_args()
    inputs = [Path(item) for item in args.inputs]
    missing = [str(path) for path in inputs if not path.exists()]
    meta_out = Path(args.meta_out)
    if missing:
        write_meta(meta_out, {"status": "failed", "reason": "missing_inputs", "missing": missing})
        return 2

    if not args.meta_inputs:
        write_meta(
            meta_out,
            {
                "status": "failed",
                "reason": "missing_meta_inputs",
                "message": "ST-GCN batch merge requires per-batch meta files so label indices can be remapped safely.",
            },
        )
        return 2

    meta_inputs = [Path(item) for item in args.meta_inputs]
    if len(meta_inputs) != len(inputs):
        write_meta(
            meta_out,
            {
                "status": "failed",
                "reason": "meta_input_count_mismatch",
                "input_count": len(inputs),
                "meta_input_count": len(meta_inputs),
            },
        )
        return 2
    missing_meta = [str(path) for path in meta_inputs if not path.exists()]
    if missing_meta:
        write_meta(meta_out, {"status": "failed", "reason": "missing_meta_inputs", "missing": missing_meta})
        return 2

    try:
        source_label_names = [read_label_names(path) for path in meta_inputs]
    except (json.JSONDecodeError, ValueError) as exc:
        write_meta(meta_out, {"status": "failed", "reason": "invalid_meta_inputs", "error": str(exc)})
        return 1

    target_label_names: list[str] = []
    for labels in source_label_names:
        for label in labels:
            if label not in target_label_names:
                target_label_names.append(label)

    sequence_batches: list[np.ndarray] = []
    label_batches: list[np.ndarray] = []
    expected_shape: tuple[int, ...] | None = None
    rows = 0
    label_counts: Counter[str] = Counter()
    for path, batch_label_names in zip(inputs, source_label_names):
        data = np.load(path)
        sequences = np.asarray(data["sequences"])
        labels = np.asarray(data["labels"])
        shape_tail = tuple(sequences.shape[1:])
        if expected_shape is None:
            expected_shape = shape_tail
        elif shape_tail != expected_shape:
            write_meta(
                meta_out,
                {
                    "status": "failed",
                    "reason": "sequence_shape_mismatch",
                    "expected_shape": list(expected_shape),
                    "actual_shape": list(shape_tail),
                    "input": str(path),
                },
            )
            return 1
        if sequences.shape[0] != labels.shape[0]:
            write_meta(meta_out, {"status": "failed", "reason": "sequence_label_count_mismatch", "input": str(path)})
            return 1
        try:
            remapped_labels = remap_labels(labels, batch_label_names, target_label_names)
        except ValueError as exc:
            write_meta(meta_out, {"status": "failed", "reason": "invalid_label_index", "input": str(path), "error": str(exc)})
            return 1
        sequence_batches.append(sequences)
        label_batches.append(remapped_labels)
        for label_index in remapped_labels.tolist():
            label_counts[target_label_names[int(label_index)]] += 1
        rows += int(sequences.shape[0])

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    merged_sequences = np.concatenate(sequence_batches, axis=0)
    merged_labels = np.concatenate(label_batches, axis=0)
    np.savez_compressed(
        output,
        sequences=merged_sequences,
        labels=merged_labels,
        label_names=np.asarray(target_label_names, dtype=str),
    )
    write_meta(
        meta_out,
        {
            "status": "merged",
            "rows": rows,
            "source_batches": [str(path) for path in inputs],
            "source_meta": [str(path) for path in meta_inputs],
            "output": str(output),
            "sequence_shape": list(merged_sequences.shape),
            "label_shape": list(merged_labels.shape),
            "label_names": target_label_names,
            "label_counts": dict(label_counts),
            "training_started": False,
        },
    )
    print(f"merged sequences={rows} output={output} training_started=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
