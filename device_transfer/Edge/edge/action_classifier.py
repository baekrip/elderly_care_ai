from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from edge.feature_extractor import FEATURE_COLUMNS

try:
    import xgboost as xgb
except ImportError:  # pragma: no cover
    xgb = None


DEFAULT_LABELS = ["STANDING", "SITTING", "LYING", "WALKING", "TRANSITION", "UNKNOWN"]


class ActionClassifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.labels = config["classification"].get("labels", DEFAULT_LABELS)
        self.model_path = Path(config["classification"].get("model_path", "")).expanduser()
        self.threshold = float(config["classification"].get("confidence_threshold", 0.45))
        self._booster: Any | None = None
        self._last_labels: dict[int, str] = {}
        self.model_name = self.model_path.name if self.model_path else "heuristic"

        if self.model_path and self.model_path.exists() and xgb is not None:
            self._booster = xgb.Booster()
            self._booster.load_model(str(self.model_path))

    def predict(self, track_id: int, feature_map: dict[str, Any], feature_vector: list[float]) -> tuple[str, float]:
        if self._booster is not None:
            values = np.asarray([feature_vector], dtype=np.float32)
            prediction = self._booster.inplace_predict(values)
            if prediction.ndim == 2:
                probabilities = prediction[0]
            else:
                probabilities = np.asarray([1.0 - float(prediction[0]), float(prediction[0])], dtype=np.float32)
            label_index = int(np.argmax(probabilities))
            confidence = float(probabilities[label_index])
            if label_index < len(self.labels) and confidence >= self.threshold:
                label = self.labels[label_index]
                self._last_labels[track_id] = label
                return label, confidence

        label, confidence = self._heuristic_predict(track_id, feature_map)
        self._last_labels[track_id] = label
        return label, confidence

    def _heuristic_predict(self, track_id: int, feature_map: dict[str, Any]) -> tuple[str, float]:
        torso_angle = abs(float(feature_map.get("torso_angle_deg", 0.0)))
        knee_angle = float(
            np.mean(
                [
                    feature_map.get("left_knee_angle_deg", 180.0),
                    feature_map.get("right_knee_angle_deg", 180.0),
                ]
            )
        )
        velocity = float(feature_map.get("center_velocity_px_s", 0.0))
        aspect_ratio = float(feature_map.get("bbox_aspect_ratio", 0.0))
        visible_ratio = float(feature_map.get("visible_joint_ratio", 0.0))

        if visible_ratio < 0.45:
            # calibrated: drop min=0.71, wander min=0.65 → 0.45 catches real occlusion
            return "UNKNOWN", 0.2
        if torso_angle > 100 or aspect_ratio > 1.4:
            # calibrated: drop torso p75=124.6°, drop aspect_ratio mean=1.55
            # wander: torso p50=135.7° (but mostly upright), aspect mean=0.53
            return "LYING", 0.7
        if knee_angle < 77 and torso_angle < 55:
            # calibrated: drop knee p25=70.2°, wander knee p25=150.9°
            return "SITTING", 0.65
        if velocity > 80 and torso_angle < 35:
            # calibrated: wander velocity p75=80.2
            return "WALKING", 0.7
        previous = self._last_labels.get(track_id)
        if previous and previous not in {"UNKNOWN", "TRANSITION"} and previous != "STANDING":
            return "TRANSITION", 0.55
        return "STANDING", 0.6

    @property
    def feature_columns(self) -> list[str]:
        return list(FEATURE_COLUMNS)
