from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_THRESHOLDS = {
    "danger_recall": 0.90,
    "lying_bed_f1": 0.80,
    "lying_floor_f1": 0.80,
    "normal_fp_per_hour": 2.0,
    "near_fall_precision": 0.70,
}


@dataclass(frozen=True)
class ModelGateResult:
    ok: bool
    failures: list[str]


def _metric(report: dict[str, Any], label: str, name: str) -> float | None:
    try:
        value = report["class_metrics"][label][name]
        return float(value)
    except (KeyError, TypeError, ValueError):
        return None


def _normal_fp_per_hour(report: dict[str, Any]) -> float | None:
    try:
        return float(report["normal_adl"]["fp_per_hour"])
    except (KeyError, TypeError, ValueError):
        return None


def _require_min(failures: list[str], label: str, actual: float | None, threshold: float) -> None:
    if actual is None:
        failures.append(f"{label} missing")
    elif actual < threshold:
        failures.append(f"{label} {actual:.4f} < {threshold:.4f}")


def _require_max(failures: list[str], label: str, actual: float | None, threshold: float) -> None:
    if actual is None:
        failures.append(f"{label} missing")
    elif actual > threshold:
        failures.append(f"{label} {actual:.4f} > {threshold:.4f}")


def check_production_gate(
    report: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> ModelGateResult:
    resolved = DEFAULT_THRESHOLDS | dict(thresholds or {})
    failures: list[str] = []

    _require_min(failures, "DANGER recall", _metric(report, "DANGER", "recall"), resolved["danger_recall"])
    _require_min(failures, "LYING_BED F1", _metric(report, "LYING_BED", "f1"), resolved["lying_bed_f1"])
    _require_min(failures, "LYING_FLOOR F1", _metric(report, "LYING_FLOOR", "f1"), resolved["lying_floor_f1"])
    _require_min(
        failures,
        "NEAR_FALL_STUMBLE precision",
        _metric(report, "NEAR_FALL_STUMBLE", "precision"),
        resolved["near_fall_precision"],
    )
    _require_max(
        failures,
        "normal ADL FP/hour",
        _normal_fp_per_hour(report),
        resolved["normal_fp_per_hour"],
    )

    return ModelGateResult(ok=not failures, failures=failures)
