from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from device_transfer.Edge.shared.labels import TARGET_ACTION_LABELS
from tools.atomic_artifact_pair import write_atomic_pair
from tools.stgcn_sequence_input import (
    RISK_LABELS,
    FrameRecord,
    SequenceContractError,
    load_frames,
)
from tools.static_feature_export_io import JsonObject, JsonValue


@dataclass(frozen=True, slots=True)
class ExportMetadata:
    exported_sequences: int
    sequence_shape: tuple[int, int, int, int]
    activity_counts: dict[str, int]
    risk_counts: dict[str, int]
    skipped_reasons: dict[str, int]


def export_activity_sequences(
    input_jsonl: Path,
    output_npz: Path,
    meta_out: Path,
    sequence_length: int,
    stride: int,
) -> ExportMetadata:
    if sequence_length <= 0 or stride <= 0:
        raise SequenceContractError("sequence length and stride must be positive")
    frames, skipped = load_frames(input_jsonl)
    grouped: dict[str, list[FrameRecord]] = defaultdict(list)
    for frame in frames:
        grouped[frame.sample_id].append(frame)
    activity_index = {name: index for index, name in enumerate(TARGET_ACTION_LABELS)}
    risk_index = {name: index for index, name in enumerate(RISK_LABELS)}
    sequences: list[np.ndarray] = []
    activity_labels: list[int] = []
    risk_labels: list[int] = []
    sample_ids: list[str] = []
    activity_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    for sample_id, sample_frames in sorted(grouped.items()):
        ordered = sorted(sample_frames, key=lambda frame: frame.frame_index)
        if len(ordered) < sequence_length:
            skipped["partial_window"] += 1
            continue
        for start in range(0, len(ordered) - sequence_length + 1, stride):
            window = ordered[start : start + sequence_length]
            if not all(
                current.frame_index == previous.frame_index + 1
                for previous, current in zip(window, window[1:])
            ):
                skipped["non_contiguous_window"] += 1
                continue
            activity_names = {frame.activity_label for frame in window}
            risk_names = {frame.risk_label for frame in window}
            if len(activity_names) != 1 or len(risk_names) != 1:
                raise SequenceContractError(f"mixed labels in window: {sample_id}:{start}")
            activity_name = window[0].activity_label
            risk_name = window[0].risk_label
            sequences.append(np.stack([frame.keypoints for frame in window]))
            activity_labels.append(activity_index[activity_name])
            risk_labels.append(risk_index[risk_name])
            sample_ids.append(f"{sample_id}:{window[0].frame_index}")
            activity_counts[activity_name] += 1
            risk_counts[risk_name] += 1
    if not sequences:
        raise SequenceContractError("no complete sequences exported")
    tensor = np.stack(sequences).astype(np.float32, copy=False)
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer,
        sequences=tensor,
        activity_labels=np.asarray(activity_labels, dtype=np.int64),
        risk_labels=np.asarray(risk_labels, dtype=np.int64),
        sample_ids=np.asarray(sample_ids),
    )
    metadata = ExportMetadata(
        exported_sequences=len(sequences),
        sequence_shape=tuple(int(value) for value in tensor.shape),
        activity_counts=dict(sorted(activity_counts.items())),
        risk_counts=dict(sorted(risk_counts.items())),
        skipped_reasons=dict(sorted(skipped.items())),
    )
    meta: JsonObject = {
        "schema_version": 1,
        "activity_label_names": list(TARGET_ACTION_LABELS),
        "risk_label_names": list(RISK_LABELS),
        "exported_sequences": metadata.exported_sequences,
        "sequence_shape": list(metadata.sequence_shape),
        "sequence_length": sequence_length,
        "stride": stride,
        "activity_counts": metadata.activity_counts,
        "risk_counts": metadata.risk_counts,
        "skipped_reasons": metadata.skipped_reasons,
        "training_started": False,
    }
    meta_bytes = (json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    write_atomic_pair(output_npz, buffer.getvalue(), meta_out, meta_bytes)
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export dual-head ST-GCN activity/risk sequences")
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--output-npz", required=True)
    parser.add_argument("--meta-out", required=True)
    parser.add_argument("--sequence-length", type=int, default=24)
    parser.add_argument("--stride", type=int, default=12)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        metadata = export_activity_sequences(
            input_jsonl=Path(args.input_jsonl),
            output_npz=Path(args.output_npz),
            meta_out=Path(args.meta_out),
            sequence_length=args.sequence_length,
            stride=args.stride,
        )
    except (OSError, SequenceContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"exported_sequences={metadata.exported_sequences}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
