"""Flow settings tRPC proxy helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import quote

import httpx

_FLOW_TRPC_BASE_URL = "https://labs.google/fx/api/trpc"
_GET_USER_SETTINGS_URL = f"{_FLOW_TRPC_BASE_URL}/videoFx.getUserSettings"
_UPDATE_USER_SETTINGS_URL = f"{_FLOW_TRPC_BASE_URL}/videoFx.updateUserSettings"
_GET_USER_SETTINGS_INPUT = {"json": None, "meta": {"values": ["undefined"]}}
_DEFAULT_TIMEOUT_SECONDS = 10.0
_AUTH_HEADER_NAMES = ("authorization", "cookie")


@dataclass(frozen=True)
class FlowAuthContext:
    """Whitelisted auth headers for same-origin Flow requests."""

    headers: dict[str, str]

    @classmethod
    def from_headers(cls, headers: Any) -> "FlowAuthContext":
        allowed: dict[str, str] = {}
        for name in _AUTH_HEADER_NAMES:
            value = headers.get(name) if hasattr(headers, "get") else None
            if isinstance(value, str) and value:
                allowed[name] = value
        return cls(headers=allowed)

    @property
    def is_available(self) -> bool:
        return bool(self.headers)


class FlowSettingsProxyError(Exception):
    """Structured Flow proxy failure without secret-bearing response details."""

    def __init__(self, error_kind: str, message: str, status_code: int | None = None):
        super().__init__(message)
        self.error_kind = error_kind
        self.message = message
        self.status_code = status_code


class FlowSettingsProxyTimeout(FlowSettingsProxyError):
    """Flow settings proxy timeout."""

    def __init__(self) -> None:
        super().__init__("flow_timeout", "Flow settings request timed out")


class FlowSettingsClient:
    """Small tRPC client for Flow user view settings."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    async def get_user_settings(self, auth_context: FlowAuthContext) -> dict[str, Any]:
        input_value = quote(json.dumps(_GET_USER_SETTINGS_INPUT, separators=(",", ":")))
        url = f"{_GET_USER_SETTINGS_URL}?input={input_value}"
        response = await self._request(
            "GET",
            url,
            headers=auth_context.headers,
        )
        return _extract_result(await _json_response(response))

    async def update_user_settings(
        self,
        payload: dict[str, Any],
        auth_context: FlowAuthContext,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            _UPDATE_USER_SETTINGS_URL,
            headers={"content-type": "application/json", **auth_context.headers},
            json={"json": payload},
        )
        return _extract_result(await _json_response(response))

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            if self._http_client is not None:
                response = await self._http_client.request(
                    method,
                    url,
                    timeout=self._timeout_seconds,
                    **kwargs,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise FlowSettingsProxyTimeout() from exc
        except httpx.HTTPError as exc:
            raise FlowSettingsProxyError(
                "flow_proxy_error",
                "Flow settings proxy request failed",
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise FlowSettingsProxyError(
                "flow_error",
                "Flow settings request failed",
                response.status_code,
            )
        return response


async def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise FlowSettingsProxyError(
            "flow_invalid_response",
            "Flow settings response was not JSON",
            response.status_code,
        ) from exc
    if not isinstance(data, dict):
        raise FlowSettingsProxyError(
            "flow_invalid_response",
            "Flow settings response was not an object",
            response.status_code,
        )
    return data


def _extract_result(data: dict[str, Any]) -> dict[str, Any]:
    result = (
        data.get("result", {})
        .get("data", {})
        .get("json", {})
        .get("result", {})
    )
    if isinstance(result, dict):
        return result
    return {}
