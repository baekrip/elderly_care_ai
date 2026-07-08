from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from shared.api_key_auth import ApiKeyAuthConfig, read_api_key_auth_config
from shared.protocol import ClipRequestEvent


class ClipRequestHandler:
    def __init__(self, clip_manager: Any, api_key: str = "", api_key_header: str = "X-API-Key") -> None:
        self.clip_manager = clip_manager
        manager_auth = getattr(clip_manager, "clip_request_auth", None)
        if isinstance(manager_auth, ApiKeyAuthConfig):
            self.auth_config = manager_auth
        else:
            self.auth_config = read_api_key_auth_config(
                {
                    "clip_request_server": {
                        "api_key": api_key,
                        "api_key_header": api_key_header,
                    }
                },
                "clip_request_server",
            )

    def handle_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        event = ClipRequestEvent(**payload)
        result = self.clip_manager.handle_clip_request(event)
        return {
            "status": "ok" if result is not None else "missing_clip",
            "event_id": event.event_id,
            "clip_path": str(result) if result is not None else None,
        }

    def authorize(self, headers: Any) -> str | None:
        if not self.auth_config.enabled:
            return None
        if not self.auth_config.api_key:
            return "clip_request_api_key_not_configured"
        if headers.get(self.auth_config.api_key_header) != self.auth_config.api_key:
            return "invalid_clip_request_api_key"
        return None

    def _make_request_handler(self) -> Callable[..., BaseHTTPRequestHandler]:
        return ClipRestServer._make_request_handler(self)


class ClipRestServer:
    def __init__(self, host: str, port: int, handler: ClipRequestHandler) -> None:
        self.host = host
        self.port = int(port)
        self.handler = handler
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        handler_factory = self._make_request_handler(self.handler)
        self._server = ThreadingHTTPServer((self.host, self.port), handler_factory)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    @staticmethod
    def _make_request_handler(handler: ClipRequestHandler) -> Callable[..., BaseHTTPRequestHandler]:
        class _RequestHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib API
                if self.path.rstrip("/") != "/clip/request":
                    self.send_response(404)
                    self.end_headers()
                    return
                auth_error = handler.authorize(self.headers)
                if auth_error is not None:
                    status = 503 if auth_error == "clip_request_api_key_not_configured" else 401
                    body = json.dumps({"status": "error", "error": auth_error}, ensure_ascii=False).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8"))
                    response = handler.handle_json(payload)
                    body = json.dumps(response, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as exc:
                    body = json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib API
                return

        return _RequestHandler
