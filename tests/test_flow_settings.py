from __future__ import annotations

import json

import httpx

from flow.settings import FlowAuthContext, FlowSettingsClient, FlowSettingsProxyError


async def test_flow_settings_client_posts_captured_trpc_shape():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": {
                        "json": {
                            "result": {"return_silent_videos": True},
                            "status": 200,
                        }
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlowSettingsClient(http_client=http_client, timeout_seconds=1)
        result = await client.update_user_settings(
            {"return_silent_videos": True, "grid_density": "compact"},
            FlowAuthContext(headers={"authorization": "Bearer secret-token"}),
        )

    assert result == {"return_silent_videos": True}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == (
        "https://labs.google/fx/api/trpc/videoFx.updateUserSettings"
    )
    assert request.headers["authorization"] == "Bearer secret-token"
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "json": {"return_silent_videos": True, "grid_density": "compact"}
    }


def test_flow_auth_context_uses_explicit_credentials_only():
    context = FlowAuthContext.from_credentials(
        token="raw-token",
        cookie="SID=secret-cookie",
    )

    assert context.headers == {
        "authorization": "Bearer raw-token",
        "cookie": "SID=secret-cookie",
    }


async def test_flow_settings_client_gets_user_settings_with_trpc_input():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": {
                        "json": {
                            "result": {
                                "lastAcknowledgedChangeLogId": "change-1",
                                "completedOnboardingIds": ["AGENT"],
                            }
                        }
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlowSettingsClient(http_client=http_client, timeout_seconds=1)
        result = await client.get_user_settings(
            FlowAuthContext(headers={"cookie": "SID=secret-cookie"})
        )

    assert result == {
        "lastAcknowledgedChangeLogId": "change-1",
        "completedOnboardingIds": ["AGENT"],
    }
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.headers["cookie"] == "SID=secret-cookie"
    assert str(request.url).startswith(
        "https://labs.google/fx/api/trpc/videoFx.getUserSettings?input="
    )


async def test_flow_settings_client_maps_http_error_without_body_leak():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Bearer raw-secret-token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlowSettingsClient(http_client=http_client, timeout_seconds=1)
        try:
            await client.update_user_settings(
                {"return_silent_videos": False},
                FlowAuthContext(headers={"authorization": "Bearer raw-secret-token"}),
            )
        except FlowSettingsProxyError as exc:
            assert exc.error_kind == "flow_error"
            assert exc.status_code == 401
            assert "raw-secret-token" not in exc.message
        else:
            raise AssertionError("expected FlowSettingsProxyError")


async def test_flow_settings_client_rejects_trpc_error_envelope():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": {
                    "message": "raw-secret-token",
                    "data": {"httpStatus": 403},
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlowSettingsClient(http_client=http_client, timeout_seconds=1)
        try:
            await client.update_user_settings(
                {"return_silent_videos": False},
                FlowAuthContext(headers={"authorization": "Bearer raw-secret-token"}),
            )
        except FlowSettingsProxyError as exc:
            assert exc.error_kind == "flow_error"
            assert exc.status_code == 403
            assert "raw-secret-token" not in exc.message
        else:
            raise AssertionError("expected FlowSettingsProxyError")


async def test_flow_settings_client_rejects_embedded_non_ok_status():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": {
                    "data": {
                        "json": {
                            "result": {},
                            "status": 500,
                        }
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FlowSettingsClient(http_client=http_client, timeout_seconds=1)
        try:
            await client.get_user_settings(FlowAuthContext(headers={"cookie": "SID=x"}))
        except FlowSettingsProxyError as exc:
            assert exc.error_kind == "flow_error"
            assert exc.status_code == 500
        else:
            raise AssertionError("expected FlowSettingsProxyError")


