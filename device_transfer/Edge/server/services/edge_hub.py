from __future__ import annotations

import json
from collections import defaultdict

from fastapi import WebSocket


class EdgeHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, camera_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[camera_id].add(websocket)

    def disconnect(self, camera_id: str, websocket: WebSocket) -> None:
        connections = self._connections.get(camera_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self._connections.pop(camera_id, None)

    async def send_to_camera(self, camera_id: str, payload: dict) -> bool:
        connections = list(self._connections.get(camera_id, set()))
        if not connections:
            return False
        message = json.dumps(payload, ensure_ascii=False)
        stale = []
        for websocket in connections:
            try:
                await websocket.send_text(message)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(camera_id, websocket)
        return True
