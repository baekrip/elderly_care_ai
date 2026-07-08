from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil, isfinite


class InvalidRiskConfidenceError(ValueError):
    pass


class InvalidRiskScoreError(ValueError):
    pass


def to_risk_score(confidence: float) -> int:
    normalized = float(confidence)
    if not isfinite(normalized):
        raise InvalidRiskConfidenceError
    bounded = max(0.0, min(1.0, normalized))
    return max(1, min(5, ceil(bounded * 5.0)))


def coerce_risk_score(value: int | float) -> int:
    if type(value) is int:
        if 1 <= value <= 5:
            return value
        raise InvalidRiskScoreError
    if type(value) is float and isfinite(value) and 0.0 <= value <= 1.0:
        return to_risk_score(value)
    raise InvalidRiskScoreError


def normalize_risk_confidence(value: int | float) -> float:
    normalized = float(value)
    if not isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise InvalidRiskConfidenceError
    return normalized


@dataclass(frozen=True)
class SmoothedRisk:
    key: str
    current_risk: float
    ema_risk: float
    vote_ratio: float
    level: str


class RiskSmoother:
    def __init__(
        self,
        *,
        alpha: float = 0.3,
        suspicious_threshold: float = 0.45,
        danger_threshold: float = 0.70,
        vote_window: int = 10,
        vote_threshold: float = 0.6,
        min_vote_count: int = 3,
    ) -> None:
        self.alpha = float(alpha)
        self.suspicious_threshold = float(suspicious_threshold)
        self.danger_threshold = float(danger_threshold)
        self.vote_window = int(vote_window)
        self.vote_threshold = float(vote_threshold)
        self.min_vote_count = int(min_vote_count)
        self._ema: dict[str, float] = {}
        self._votes: dict[str, deque[bool]] = {}

    def update(self, key: str, current_risk: float) -> SmoothedRisk:
        current = max(0.0, min(1.0, float(current_risk)))
        previous = self._ema.get(key, 0.0)
        ema = self.alpha * current + (1.0 - self.alpha) * previous
        self._ema[key] = ema

        votes = self._votes.setdefault(key, deque(maxlen=self.vote_window))
        votes.append(current >= self.suspicious_threshold)
        vote_ratio = sum(1 for vote in votes if vote) / max(len(votes), 1)

        vote_ready = len(votes) >= self.min_vote_count

        if ema >= self.danger_threshold and (vote_ratio >= self.vote_threshold or not vote_ready):
            level = "danger"
        elif ema >= self.suspicious_threshold or (vote_ready and vote_ratio >= self.vote_threshold):
            level = "suspicious"
        else:
            level = "normal"

        return SmoothedRisk(
            key=key,
            current_risk=current,
            ema_risk=ema,
            vote_ratio=vote_ratio,
            level=level,
        )

    def reset(self, key: str) -> None:
        self._ema.pop(key, None)
        self._votes.pop(key, None)
