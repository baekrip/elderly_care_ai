from __future__ import annotations

import json
from collections.abc import Mapping
from http import HTTPStatus
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.api_key_auth import ApiKeyAuthConfig, read_api_key_auth_config


PROTECTED_HTTP_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/cameras/register"),
    ("POST", "/api/activity/frames"),
    ("POST", "/api/activity/timeline"),
    ("POST", "/api/candidates/submit"),
    ("POST", "/api/risk-events"),
    ("GET", "/api/risk-events"),
    ("GET", "/api/clips"),
}
PROTECTED_HTTP_PREFIX_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/activity/timeline/"),
    ("GET", "/api/streams/"),
    ("GET", "/api/clips/"),
)
PROTECTED_WEBSOCKET_PREFIXES = ("/ws/edges/", "/ws/skeleton/", "/ws/overlay/")


class IngestAuthMiddleware:
    def __init__(self, app: ASGIApp, config: Mapping[str, object]) -> None:
        self.app = app
        self.auth_config = read_api_key_auth_config(
            config,
            "ingest_auth",
            enabled_default=False,
            default_api_key_header="X-Edge-API-Key",
            require_device_id_default=True,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = str(scope.get("type", ""))
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "")).upper()

        if not self._requires_auth(scope_type, method, path):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        error = self._auth_error(headers, path, scope.get("query_string", b""))
        if error is None:
            await self.app(scope, receive, send)
            return

        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": error})
            return
        await self._send_json_error(send, error)

    @staticmethod
    def _requires_auth(scope_type: str, method: str, path: str) -> bool:
        if scope_type == "http":
            return (method, path) in PROTECTED_HTTP_ROUTES or any(
                method == protected_method and path.startswith(protected_prefix)
                for protected_method, protected_prefix in PROTECTED_HTTP_PREFIX_ROUTES
            )
        if scope_type == "websocket":
            return path.startswith(PROTECTED_WEBSOCKET_PREFIXES)
        return False

    def _auth_error(self, headers: Headers, path: str, query_string: bytes | str = b"") -> str | None:
        auth_config = self.auth_config
        if not auth_config.enabled:
            return None
        if not auth_config.api_key:
            return "ingest_auth_not_configured"
        supplied_key = headers.get(auth_config.api_key_header)
        supplied_device = headers.get(auth_config.device_id_header)
        if not supplied_key and path.startswith("/ws/overlay/"):
            query = parse_qs(
                query_string.decode("utf-8", errors="ignore") if isinstance(query_string, bytes) else str(query_string),
                keep_blank_values=False,
            )
            supplied_key = _first_query_value(query, "edge_api_key", "api_key")
            supplied_device = supplied_device or _first_query_value(query, "device_id", "camera_id")
        if supplied_key != auth_config.api_key:
            return "invalid_ingest_api_key"
        if auth_config.require_device_id:
            if not supplied_device:
                return "missing_ingest_device_id"
            path_device = self._path_device_id(path)
            if path_device and supplied_device != path_device:
                return "ingest_device_id_mismatch"
        return None

    @staticmethod
    def _path_device_id(path: str) -> str:
        for prefix in PROTECTED_WEBSOCKET_PREFIXES:
            if path.startswith(prefix):
                return path.removeprefix(prefix).split("/", 1)[0]
        return ""

    async def _send_json_error(self, send: Send, detail: str) -> None:
        status = HTTPStatus.SERVICE_UNAVAILABLE if detail == "ingest_auth_not_configured" else HTTPStatus.UNAUTHORIZED
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": int(status),
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _first_query_value(query: dict[str, list[str]], *keys: str) -> str:
    for key in keys:
        values = query.get(key)
        if values:
            return str(values[0]).strip()
    return ""
