from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiKeyAuthConfig:
    enabled: bool
    api_key: str
    api_key_header: str
    device_id: str
    device_id_header: str
    require_device_id: bool


def read_api_key_auth_config(
    config: Mapping[str, object],
    section_name: str,
    *,
    enabled_field: str = "enabled",
    enabled_default: bool = False,
    api_key_field: str = "api_key",
    api_key_env_field: str = "api_key_env",
    api_key_header_field: str = "api_key_header",
    default_api_key_header: str = "X-API-Key",
    device_id: str = "",
    device_id_header_field: str = "device_id_header",
    default_device_id_header: str = "X-Device-ID",
    require_device_id_field: str = "require_device_id",
    require_device_id_default: bool = False,
) -> ApiKeyAuthConfig:
    section = _as_mapping(config.get(section_name))
    api_key = _string(section.get(api_key_field))
    env_name = _string(section.get(api_key_env_field))
    if env_name:
        api_key = os.getenv(env_name, api_key)

    enabled = _bool(section.get(enabled_field), enabled_default)
    if enabled_field not in section and (api_key or env_name):
        enabled = True

    return ApiKeyAuthConfig(
        enabled=enabled,
        api_key=api_key,
        api_key_header=_string(section.get(api_key_header_field), default_api_key_header),
        device_id=device_id,
        device_id_header=_string(section.get(device_id_header_field), default_device_id_header),
        require_device_id=_bool(section.get(require_device_id_field), require_device_id_default),
    )


def build_api_key_headers(auth_config: ApiKeyAuthConfig) -> dict[str, str]:
    headers: dict[str, str] = {}
    if auth_config.enabled and auth_config.api_key:
        headers[auth_config.api_key_header] = auth_config.api_key
    if auth_config.require_device_id and auth_config.device_id:
        headers[auth_config.device_id_header] = auth_config.device_id
    return headers


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def _string(value: object, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
