from __future__ import annotations

import json
import logging
import statistics
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

try:
    import xgboost as xgb
except ImportError:  # pragma: no cover
    xgb = None


LOGGER = logging.getLogger(__name__)


class TierClassifier:
    def __init__(self, config: dict[str, Any]) -> None:
        tier_cfg = config.get("tier_classification", {})
        self.enabled = bool(tier_cfg.get("enabled", False))
        self.model_path = Path(tier_cfg.get("model_path", "")).expanduser()
        self.meta_path = Path(tier_cfg.get("meta_path", "")).expanduser()
        self.threshold = float(tier_cfg.get("confidence_threshold", 0.55))
        self.window_size = int(tier_cfg.get("window_frames", 12))
        self.min_history = int(tier_cfg.get("min_history_frames", 4))
        self.default_label = str(tier_cfg.get("default_label", "NORMAL"))
        self.feature_columns: list[str] = []
        self.label_order: list[str] = []
        self.disabled_reason: str | None = None
        self._booster: Any | None = None
        self._windows: dict[int, deque[dict[str, float]]] = {}

        if not self.enabled:
            self.disabled_reason = "disabled by config"
            LOGGER.info("XGBoost disabled: %s", self.disabled_reason)
            return
        if xgb is None:
            self.enabled = False
            self.disabled_reason = "xgboost package not installed"
            LOGGER.warning("XGBoost disabled: %s", self.disabled_reason)
            return
        if not self.model_path.exists():
            self.enabled = False
            self.disabled_reason = f"model not found: {self.model_path}"
            LOGGER.warning("XGBoost disabled: %s", self.disabled_reason)
            return

        self._booster = xgb.Booster()
        self._booster.load_model(str(self.model_path))
        self._load_meta()
        LOGGER.info("XGBoost loaded: %s", self.model_path)

    def _load_meta(self) -> None:
        if self.meta_path.exists():
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            self.feature_columns = [str(item) for item in meta.get("feature_columns", [])]
            self.label_order = [str(item) for item in meta.get("label_order", [])]
        if not self.feature_columns and getattr(self._booster, "feature_names", None):
            self.feature_columns = list(self._booster.feature_names)
        if not self.label_order:
            self.label_order = [self.default_label, "DROP"]

    def _get_window(self, track_id: int) -> deque[dict[str, float]]:
        if track_id not in self._windows:
            self._windows[track_id] = deque(maxlen=self.window_size)
        return self._windows[track_id]

    def update(self, track_id: int, feature_map: dict[str, Any]) -> tuple[str, float]:
        if not self.enabled or self._booster is None or not self.feature_columns:
            return self.default_label, 0.0

        normalized = {
            str(key): float(value)
            for key, value in feature_map.items()
            if isinstance(value, (int, float))
        }
        window = self._get_window(track_id)
        window.append(normalized)
        required_history = max(3, min(self.min_history, self.window_size))
        if len(window) < required_history:
            return self.default_label, 0.0

        vector = self._summarize_window(list(window))
        return self.classify_summary(vector)

    def classify_summary(self, vector: dict[str, float]) -> tuple[str, float]:
        if not self.enabled or self._booster is None or not self.feature_columns:
            return self.default_label, 0.0

        values = np.asarray([[vector.get(column, 0.0) for column in self.feature_columns]], dtype=np.float32)
        prediction = self._booster.inplace_predict(values)
        probabilities = prediction[0] if prediction.ndim == 2 else np.asarray([1.0 - float(prediction[0]), float(prediction[0])], dtype=np.float32)
        label_index = int(np.argmax(probabilities))
        confidence = float(probabilities[label_index])
        if label_index >= len(self.label_order) or confidence < self.threshold:
            return self.default_label, confidence
        return self.label_order[label_index], confidence

    def cleanup_track(self, track_id: int) -> None:
        self._windows.pop(track_id, None)

    @staticmethod
    def _summarize_window(rows: list[dict[str, float]]) -> dict[str, float]:
        if not rows:
            return {}
        keys = sorted(rows[0].keys())
        summary: dict[str, float] = {}
        for key in keys:
            values = [float(row.get(key, 0.0)) for row in rows]
            summary[f"{key}_mean"] = float(statistics.fmean(values))
            summary[f"{key}_std"] = float(statistics.pstdev(values)) if len(values) > 1 else 0.0
            summary[f"{key}_min"] = float(min(values))
            summary[f"{key}_max"] = float(max(values))
            summary[f"{key}_last"] = float(values[-1])
        return summary
