from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class ManagedEvent:
    event_id: str
    camera_id: str
    person_id: str
    event_type: str
    state: str
    started_at_ms: int
    updated_at_ms: int
    risk_score: float
    transitions: list[dict[str, object]] = field(default_factory=list)
    stale_input: bool = False

    @property
    def temporal_decision(self) -> str:
        return "fall" if self.state == "CONFIRMED" else "normal"


class EventQueue:
    def __init__(self, *, confirm_after_ms: int = 30_000, resolve_after_ms: int = 5_000) -> None:
        self.confirm_after_ms = int(confirm_after_ms)
        self.resolve_after_ms = int(resolve_after_ms)
        self._events: dict[tuple[str, str, str], ManagedEvent] = {}

    def update(
        self,
        *,
        camera_id: str,
        person_id: str,
        event_type: str,
        risk_level: str,
        risk_score: float,
        timestamp_ms: int,
        is_pose_lost: bool = False,
    ) -> ManagedEvent:
        key = (camera_id, person_id, event_type)
        event = self._events.get(key)
        current_timestamp_ms = int(timestamp_ms)
        if event is None or event.state == "RESOLVED":
            event = ManagedEvent(
                event_id=f"evt_{camera_id}_{person_id}_{uuid4().hex[:8]}",
                camera_id=camera_id,
                person_id=person_id,
                event_type=event_type,
                state="NORMAL",
                started_at_ms=current_timestamp_ms,
                updated_at_ms=current_timestamp_ms,
                risk_score=float(risk_score),
            )
            self._events[key] = event
        elif current_timestamp_ms < event.updated_at_ms:
            event.stale_input = True
            return event

        event.stale_input = False
        next_state = self._next_state(event, risk_level, current_timestamp_ms)
        if next_state != event.state:
            event.transitions.append(
                {
                    "from": event.state,
                    "to": next_state,
                    "timestamp_ms": current_timestamp_ms,
                    "risk_level": risk_level,
                    "risk_score": float(risk_score),
                }
            )
            event.state = next_state
        if not is_pose_lost:
            event.updated_at_ms = current_timestamp_ms
        event.risk_score = float(risk_score)
        return event

    def resolve_competing_events(
        self,
        *,
        camera_id: str,
        person_id: str,
        keep_event_type: str,
        timestamp_ms: int,
    ) -> list[ManagedEvent]:
        resolved: list[ManagedEvent] = []
        for event in list(self._events.values()):
            if event.camera_id != camera_id or event.person_id != person_id:
                continue
            if event.event_type == keep_event_type or event.state == "RESOLVED":
                continue
            updated = self.update(
                camera_id=camera_id,
                person_id=person_id,
                event_type=event.event_type,
                risk_level="normal",
                risk_score=0.0,
                timestamp_ms=timestamp_ms,
            )
            if updated.state == "RESOLVED":
                resolved.append(updated)
        return resolved

    def _next_state(self, event: ManagedEvent, risk_level: str, timestamp_ms: int) -> str:
        level = risk_level.lower()
        if level == "normal":
            if event.state in {"SUSPICIOUS", "DANGEROUS", "CONFIRMED"} and timestamp_ms - event.updated_at_ms >= self.resolve_after_ms:
                return "RESOLVED"
            return event.state if event.state != "NORMAL" else "NORMAL"
        if level == "suspicious":
            return "SUSPICIOUS" if event.state == "NORMAL" else event.state
        if level == "danger":
            if event.state == "DANGEROUS" and timestamp_ms - event.started_at_ms >= self.confirm_after_ms:
                return "CONFIRMED"
            if event.state in {"NORMAL", "SUSPICIOUS"}:
                return "DANGEROUS"
            return event.state
        return event.state

    def active_events(self) -> list[ManagedEvent]:
        return [event for event in self._events.values() if event.state != "RESOLVED"]
