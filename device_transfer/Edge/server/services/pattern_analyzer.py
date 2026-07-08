"""Pattern Analyzer — Phase 11.

5-minute interval behavior pattern analysis.
Stage 1: rule-based anomaly detection.
Stage 2 (future): LSTM-based temporal pattern learning.

Role separation:
  ST-GCN   = instant danger detection (2-second window)
  PatternAI = lifestyle pattern anomaly detection (5min ~ 24h window)
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
SNAPSHOT_SCHEMA_VERSION = "1.0"


def _snapshot_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _prune_snapshot_payload(payload: dict[str, Any], *, max_size_bytes: int) -> dict[str, Any]:
    pruned = json.loads(json.dumps(payload, ensure_ascii=False))
    daily_patterns = ((pruned.get("state") or {}).get("daily_patterns") or {})
    if not isinstance(daily_patterns, dict):
        return pruned

    removed = 0
    while _snapshot_size_bytes(pruned) > max_size_bytes:
        candidates = [
            (len(values), key)
            for key, values in daily_patterns.items()
            if isinstance(values, list) and values
        ]
        if not candidates:
            break
        _, key = max(candidates)
        daily_patterns[key] = daily_patterns[key][1:]
        removed += 1
    if removed:
        logger.warning("pattern snapshot pruned oldest rows to fit size limit: removed=%s", removed)
    return pruned


class PatternAnalyzer:
    """Rule-based behavior pattern analyzer (Stage 1).

    Analyzes accumulated behavior timelines every 5 minutes
    and flags anomalies based on configurable rules.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.inactivity_threshold_sec: float = cfg.get("inactivity_threshold_sec", 300)
        self.night_start_hour: int = cfg.get("night_start_hour", 22)
        self.night_end_hour: int = cfg.get("night_end_hour", 6)
        self.pattern_deviation_pct: float = cfg.get("pattern_deviation_pct", 30.0)

        # historical pattern storage (per patient)
        self._daily_patterns: dict[str, list[dict[str, float]]] = defaultdict(list)

    def analyze_window(self, patient_id: str, segments: list[dict[str, Any]],
                       window_start_ms: int, window_end_ms: int) -> list[dict[str, Any]]:
        """Analyze a 5-minute window of timeline segments.

        Args:
            patient_id: patient identifier
            segments: list of TimelineSegment-like dicts
                      (action_label, started_at_ms, ended_at_ms, duration_ms)
            window_start_ms: window start timestamp
            window_end_ms: window end timestamp

        Returns:
            list of anomaly events (may be empty)
        """
        anomalies: list[dict[str, Any]] = []

        if not segments:
            return anomalies

        # --- Rule 1: Prolonged inactivity ---
        total_inactive_ms = 0
        for seg in segments:
            label = seg.get("action_label", "UNKNOWN")
            dur = seg.get("duration_ms", 0)
            if label in ("SITTING", "LYING", "RESTING_IN_CHAIR", "SLEEPING_IN_BED", "UNKNOWN"):
                total_inactive_ms += dur

        if total_inactive_ms / 1000 > self.inactivity_threshold_sec:
            anomalies.append({
                "type": "PROLONGED_INACTIVITY",
                "level": "ABNORMAL",
                "patient_id": patient_id,
                "detail": f"Inactive for {total_inactive_ms / 1000:.0f}s in 5-min window",
                "window_start_ms": window_start_ms,
                "window_end_ms": window_end_ms,
            })

        # --- Rule 2: Night unusual activity ---
        current_hour = datetime.fromtimestamp(window_start_ms / 1000).hour
        is_night = current_hour >= self.night_start_hour or current_hour < self.night_end_hour

        if is_night:
            active_ms = 0
            for seg in segments:
                label = seg.get("action_label", "UNKNOWN")
                if label in ("WALKING", "STANDING", "EXERCISING"):
                    active_ms += seg.get("duration_ms", 0)

            if active_ms > 60_000:  # more than 1 minute active at night
                anomalies.append({
                    "type": "NIGHT_UNUSUAL_ACTIVITY",
                    "level": "ABNORMAL",
                    "patient_id": patient_id,
                    "detail": f"Active for {active_ms / 1000:.0f}s during night hours ({current_hour}:00)",
                    "window_start_ms": window_start_ms,
                    "window_end_ms": window_end_ms,
                })

        # --- Rule 3: Pattern deviation (requires 1+ day of history) ---
        behavior_ratio = self._compute_behavior_ratio(segments)
        historical = self._get_historical_ratio(patient_id, current_hour)

        if historical:
            for label, ratio in behavior_ratio.items():
                hist_ratio = historical.get(label, 0)
                if hist_ratio > 0.05:  # only compare if historically meaningful
                    deviation_pct = abs(ratio - hist_ratio) / hist_ratio * 100
                    if deviation_pct > self.pattern_deviation_pct:
                        anomalies.append({
                            "type": "PATTERN_DEVIATION",
                            "level": "ABNORMAL",
                            "patient_id": patient_id,
                            "detail": f"{label}: {ratio:.1%} vs historical {hist_ratio:.1%} ({deviation_pct:.0f}% deviation)",
                            "window_start_ms": window_start_ms,
                            "window_end_ms": window_end_ms,
                        })

        # store current ratio for future comparison
        self._store_pattern(patient_id, current_hour, behavior_ratio)

        return anomalies

    def _compute_behavior_ratio(self, segments: list[dict[str, Any]]) -> dict[str, float]:
        """Compute ratio of each behavior label in the window."""
        total_ms = sum(seg.get("duration_ms", 0) for seg in segments)
        if total_ms <= 0:
            return {}

        ratios: dict[str, float] = defaultdict(float)
        for seg in segments:
            label = seg.get("action_label", "UNKNOWN")
            ratios[label] += seg.get("duration_ms", 0) / total_ms

        return dict(ratios)

    def _get_historical_ratio(self, patient_id: str, hour: int) -> dict[str, float] | None:
        """Get historical behavior ratio for the same hour from past days."""
        key = f"{patient_id}_h{hour}"
        history = self._daily_patterns.get(key)
        if not history or len(history) < 1:
            return None

        # average across stored days
        avg: dict[str, float] = defaultdict(float)
        count = len(history)
        for entry in history:
            for label, ratio in entry.items():
                avg[label] += ratio / count

        return dict(avg)

    def _store_pattern(self, patient_id: str, hour: int, ratio: dict[str, float]) -> None:
        """Store current behavior ratio for future comparison."""
        key = f"{patient_id}_h{hour}"
        self._daily_patterns[key].append(ratio)
        # keep only last 7 days
        if len(self._daily_patterns[key]) > 7 * 12:  # 12 windows per hour
            self._daily_patterns[key] = self._daily_patterns[key][-7 * 12:]

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "daily_patterns": {
                key: [dict(entry) for entry in values]
                for key, values in self._daily_patterns.items()
            }
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        daily_patterns = state.get("daily_patterns", {})
        restored: dict[str, list[dict[str, float]]] = {}
        if isinstance(daily_patterns, dict):
            for key, values in daily_patterns.items():
                if not isinstance(values, list):
                    continue
                rows: list[dict[str, float]] = []
                for item in values:
                    if isinstance(item, dict):
                        rows.append({str(label): float(ratio) for label, ratio in item.items()})
                restored[str(key)] = rows
        self._daily_patterns = defaultdict(list, restored)

    def save_snapshot(
        self,
        path: str | Path,
        *,
        camera_id: str,
        window_start_ts: int,
        window_end_ts: int,
        max_snapshot_size_kb: int | None = None,
    ) -> None:
        snapshot_path = Path(path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "camera_id": camera_id,
            "window_start_ts": int(window_start_ts),
            "window_end_ts": int(window_end_ts),
            "state": self.snapshot_state(),
        }
        if max_snapshot_size_kb is not None and int(max_snapshot_size_kb) > 0:
            payload = _prune_snapshot_payload(payload, max_size_bytes=int(max_snapshot_size_kb) * 1024)
        tmp_path = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(snapshot_path)

    def restore_snapshot(
        self,
        path: str | Path,
        *,
        now_ts_ms: int | None = None,
        max_gap_sec: int = 300,
    ) -> dict[str, Any]:
        snapshot_path = Path(path)
        if not snapshot_path.exists():
            return {"status": "missing"}
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("pattern snapshot is invalid json: %s", snapshot_path)
            return {"status": "invalid_json"}
        if not isinstance(payload, dict):
            return {"status": "invalid_snapshot"}
        if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            return {"status": "schema_mismatch", "schema_version": payload.get("schema_version")}

        state = payload.get("state")
        if not isinstance(state, dict):
            return {"status": "invalid_state"}

        status = "restored"
        window_end_ts = payload.get("window_end_ts")
        if now_ts_ms is not None and window_end_ts is not None:
            try:
                gap_ms = int(now_ts_ms) - int(window_end_ts)
                if gap_ms > int(max_gap_sec) * 1000:
                    status = "partial_restored_gap"
            except (TypeError, ValueError):
                status = "restored"

        self.restore_state(state)
        return {"status": status, "path": str(snapshot_path)}
