from __future__ import annotations

import statistics
from dataclasses import dataclass, replace
from typing import Any

from server.services.sequence_buffer import SkeletonSequenceBuffer
from shared.protocol import CandidateWindowFrame, SkeletonFrame


FALL_LABELS = {
    "DROP",
    "FALL",
    "DANGER",
    "GRADUAL_FALL",
    "GRADUAL_COLLAPSE",
    "LOSS_OF_BALANCE",
    "FALL_CANDIDATE",
    "FALL_CONFIRMED",
    "FALL_THEN_NO_MOVE",
}


@dataclass(frozen=True)
class ModelInputWindow:
    camera_id: str
    track_id: int
    track_window: dict[str, Any]
    xgboost_summary: dict[str, float]
    sequence: list[CandidateWindowFrame]
    coarse_action: str = "UNKNOWN"
    coarse_confidence: float = 0.0

    def with_coarse(self, label: str, confidence: float) -> "ModelInputWindow":
        return replace(self, coarse_action=str(label), coarse_confidence=float(confidence))


class ModelInputWindowBuilder:
    def __init__(
        self,
        *,
        window_ms: int = 5_000,
        min_frames: int = 8,
        stgcn_stride_ms: int = 1_000,
    ) -> None:
        self.buffer = SkeletonSequenceBuffer(window_ms=window_ms, min_frames=min_frames)
        self.stgcn_stride_ms = max(int(stgcn_stride_ms), 0)
        self._last_emitted_ts: dict[tuple[str, int], int] = {}

    def add(self, frame: SkeletonFrame) -> ModelInputWindow | None:
        window = self.buffer.add(frame)
        if not window.ready or not window.frames:
            return None

        key = (window.camera_id, window.track_id)
        last_ts = self._last_emitted_ts.get(key)
        if last_ts is not None and self.stgcn_stride_ms and frame.timestamp_ms - last_ts < self.stgcn_stride_ms:
            return None
        self._last_emitted_ts[key] = int(frame.timestamp_ms)

        return ModelInputWindow(
            camera_id=window.camera_id,
            track_id=window.track_id,
            track_window={
                "camera_id": window.camera_id,
                "track_id": window.track_id,
                "start_ts_ms": int(window.frames[0].timestamp_ms),
                "end_ts_ms": int(window.frames[-1].timestamp_ms),
                "frame_count": len(window.frames),
            },
            xgboost_summary=summarize_feature_rows(_numeric_features(item.features) for item in window.frames),
            sequence=[_candidate_frame(item, index) for index, item in enumerate(window.frames)],
        )


def summarize_feature_rows(rows_iter: Any) -> dict[str, float]:
    rows = [dict(row) for row in rows_iter if row]
    if not rows:
        return {}
    keys = sorted({key for row in rows for key in row})
    summary: dict[str, float] = {}
    for key in keys:
        values = [float(row.get(key, 0.0)) for row in rows]
        summary[f"{key}_mean"] = float(statistics.fmean(values))
        summary[f"{key}_std"] = float(statistics.pstdev(values)) if len(values) > 1 else 0.0
        summary[f"{key}_min"] = float(min(values))
        summary[f"{key}_max"] = float(max(values))
        summary[f"{key}_last"] = float(values[-1])
    return summary


def fall_probability(label: str | None, probability: float | None) -> float:
    if label is None or probability is None:
        return 0.0
    if str(label).upper() in FALL_LABELS:
        return max(0.0, min(float(probability), 1.0))
    return 0.0


def fuse_model_outputs(
    *,
    xgboost_label: str,
    xgboost_probability: float,
    stgcn_label: str | None = None,
    stgcn_probability: float | None = None,
    stgcn_weight: float = 0.6,
    xgboost_weight: float = 0.4,
    decision_threshold: float = 0.7,
) -> dict[str, Any]:
    xgb_fall = fall_probability(xgboost_label, xgboost_probability)
    stgcn_fall = fall_probability(stgcn_label, stgcn_probability)
    threshold = float(decision_threshold)

    if stgcn_label is None or stgcn_probability is None:
        final_probability = xgb_fall
        method = "xgboost_only"
    else:
        total_weight = max(float(stgcn_weight) + float(xgboost_weight), 1e-9)
        final_probability = (stgcn_fall * float(stgcn_weight) + xgb_fall * float(xgboost_weight)) / total_weight
        method = "weighted_average"

    final_probability = round(float(final_probability), 6)
    if final_probability >= threshold:
        final_label = "fall_confirmed"
    elif final_probability > 0.0:
        final_label = "fall_suspicious"
    else:
        final_label = "normal"

    return {
        "method": method,
        "stgcn_weight": float(stgcn_weight),
        "xgboost_weight": float(xgboost_weight),
        "final_fall_probability": final_probability,
        "decision_threshold": threshold,
        "final_label": final_label,
    }


def _numeric_features(value: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, item in (value or {}).items():
        if isinstance(item, (int, float)):
            result[str(key)] = float(item)
    return result


def _candidate_frame(frame: SkeletonFrame, index: int) -> CandidateWindowFrame:
    return CandidateWindowFrame(
        frame_idx=index,
        ts_ms=int(frame.timestamp_ms),
        capture_ts=frame.capture_ts,
        analysis_ts=frame.analysis_ts,
        pose_conf_mean=float(frame.pose_confidence_mean),
        bbox_xyxy=frame.bbox.as_list(),
        keypoints_17=[[float(kp.x), float(kp.y), float(kp.confidence)] for kp in frame.keypoints[:17]],
        features=_numeric_features(frame.features),
        inference_timing={},
    )
