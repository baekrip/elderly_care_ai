from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter(tags=["overlay"])


@router.websocket("/ws/overlay/{camera_id}")
async def overlay_socket(websocket: WebSocket, camera_id: str) -> None:
    await websocket.accept()
    broadcaster = websocket.app.state.overlay_broadcaster
    queue = broadcaster.subscribe(camera_id)
    try:
        while True:
            payload = await asyncio.wait_for(queue.get(), timeout=10.0)
            await websocket.send_json(payload)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        return
    finally:
        broadcaster.unsubscribe(camera_id, queue)
