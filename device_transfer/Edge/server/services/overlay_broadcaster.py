from __future__ import annotations

import asyncio
from typing import Any

from server.services.risk_smoothing import coerce_risk_score, to_risk_score
from shared.protocol import OverlayFrame, OverlayTrack, SkeletonFrame


class OverlayBroadcaster:
    def __init__(self) -> None:
        self._latest: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._drop_counts: dict[str, int] = {}

    def subscribe(self, camera_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._subscribers.setdefault(camera_id, set()).add(queue)
        latest = self._latest.get(camera_id)
        if latest is not None:
            queue.put_nowait(latest)
        return queue

    def unsubscribe(self, camera_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(camera_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(camera_id, None)

    def latest(self, camera_id: str) -> dict[str, Any] | None:
        return self._latest.get(camera_id)

    def publish(self, frame: dict[str, Any] | OverlayFrame) -> None:
        payload = frame.model_dump(mode="json") if isinstance(frame, OverlayFrame) else dict(frame)
        tracks = payload.get("tracks")
        if isinstance(tracks, list):
            payload["tracks"] = [self._normalize_track(track) for track in tracks]
        camera_id = str(payload["camera_id"])
        payload["drop_count"] = int(self._drop_counts.get(camera_id, 0))
        self._latest[camera_id] = payload
        for queue in list(self._subscribers.get(camera_id, set())):
            if queue.full():
                try:
                    queue.get_nowait()
                    self._drop_counts[camera_id] = self._drop_counts.get(camera_id, 0) + 1
                    payload["drop_count"] = int(self._drop_counts[camera_id])
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(dict(payload))

    @staticmethod
    def _normalize_track(track: Any) -> Any:
        if not isinstance(track, dict):
            return track
        normalized = dict(track)
        risk_score = normalized.get("risk_score")
        if risk_score is not None:
            normalized["risk_score"] = coerce_risk_score(risk_score)
        return normalized

    @staticmethod
    def frame_from_skeleton(
        frame: SkeletonFrame,
        *,
        event: dict[str, Any] | None = None,
        fps: float | None = None,
    ) -> dict[str, Any]:
        event = event or {}
        features = frame.features or {}
        source_width = int(features.get("source_width") or features.get("frame_width") or 0)
        source_height = int(features.get("source_height") or features.get("frame_height") or 0)
        stream_width = features.get("stream_width")
        stream_height = features.get("stream_height")
        risk_label = str(event.get("risk_label") or frame.event_state or "NORMAL").upper()
        if risk_label == "DANGER":
            risk_label = "DANGER"
        risk_score = event.get("risk_score")
        if risk_score is None:
            risk_confidence = frame.risk.get("ema_score") if isinstance(frame.risk, dict) else None
            risk_score = to_risk_score(risk_confidence) if risk_confidence is not None else None
        overlay = OverlayFrame(
            camera_id=frame.camera_id,
            frame_id=frame.frame_id,
            sequence_id=frame.sequence_id,
            capture_ts=frame.capture_ts or frame.timestamp_ms,
            analysis_ts=frame.analysis_ts,
            source_width=source_width,
            source_height=source_height,
            stream_width=int(stream_width) if stream_width is not None else None,
            stream_height=int(stream_height) if stream_height is not None else None,
            latency_ms=None,
            fps=fps,
            tracks=[
                OverlayTrack(
                    track_id=str(frame.track_id),
                    bbox=frame.bbox.as_list(),
                    keypoints=[[kp.x, kp.y, kp.confidence] for kp in frame.keypoints],
                    action_label=features.get("action_label"),
                    risk_label=risk_label,
                    risk_score=coerce_risk_score(risk_score) if risk_score is not None else None,
                    event_state=str(event.get("state") or frame.event_state),
                )
            ],
        )
        return overlay.model_dump(mode="json")
