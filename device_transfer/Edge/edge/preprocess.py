from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass
class PreprocessResult:
    frame: np.ndarray
    metadata: dict[str, Any]


class FramePreprocessor:
    def __init__(self, config: dict[str, Any]) -> None:
        preprocess_cfg = config.get("preprocess", {})
        self.enabled = bool(preprocess_cfg.get("enabled", False))
        self.clahe_enabled = bool(preprocess_cfg.get("clahe_enabled", False))
        self.clahe_clip_limit = float(preprocess_cfg.get("clahe_clip_limit", 2.0))
        self.clahe_tile_grid_size = int(preprocess_cfg.get("clahe_tile_grid_size", 8))
        self.gamma = float(preprocess_cfg.get("gamma", 1.0))
        self.brightness_warning_threshold = float(preprocess_cfg.get("brightness_warning_threshold", 70.0))

    def apply(self, frame: np.ndarray) -> PreprocessResult:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness_score = float(np.mean(gray))

        if not self.enabled:
            return PreprocessResult(
                frame=frame,
                metadata={
                    "enabled": False,
                    "clahe_applied": False,
                    "gamma_applied": False,
                    "brightness_score": brightness_score,
                    "low_light": brightness_score < self.brightness_warning_threshold,
                },
            )

        processed = frame.copy()
        gamma_applied = False
        if abs(self.gamma - 1.0) > 1e-3:
            inv_gamma = 1.0 / max(self.gamma, 1e-3)
            table = np.array([((index / 255.0) ** inv_gamma) * 255 for index in range(256)], dtype=np.uint8)
            processed = cv2.LUT(processed, table)
            gamma_applied = True

        clahe_applied = False
        if self.clahe_enabled:
            lab = cv2.cvtColor(processed, cv2.COLOR_BGR2LAB)
            l_channel, a_channel, b_channel = cv2.split(lab)
            clahe = cv2.createCLAHE(
                clipLimit=self.clahe_clip_limit,
                tileGridSize=(self.clahe_tile_grid_size, self.clahe_tile_grid_size),
            )
            l_channel = clahe.apply(l_channel)
            processed = cv2.cvtColor(cv2.merge([l_channel, a_channel, b_channel]), cv2.COLOR_LAB2BGR)
            clahe_applied = True

        processed_gray = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
        processed_brightness_score = float(np.mean(processed_gray))
        return PreprocessResult(
            frame=processed,
            metadata={
                "enabled": True,
                "clahe_applied": clahe_applied,
                "gamma_applied": gamma_applied,
                "brightness_score": brightness_score,
                "processed_brightness_score": processed_brightness_score,
                "low_light": brightness_score < self.brightness_warning_threshold,
            },
        )
