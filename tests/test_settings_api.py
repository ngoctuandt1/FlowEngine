async def test_veo_account_create_update_delete_round_trip(api_client):
    create_response = await api_client.post(
        "/api/settings/veo-accounts",
        json={
            "name": "Account 1",
            "token": "token-12345678",
            "cookie": "cookie-abcdef12",
            "enabled": True,
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()
    account_id = created["id"]
    assert created["token"] == "***5678"
    assert created["cookie"] == "***ef12"
    assert created["enabled"] is True

    list_response = await api_client.get("/api/settings/veo-accounts")

    assert list_response.status_code == 200
    assert list_response.json() == [
        {
            "id": account_id,
            "name": "Account 1",
            "token": "***5678",
            "cookie": "***ef12",
            "enabled": True,
        }
    ]

    update_response = await api_client.put(
        f"/api/settings/veo-accounts/{account_id}",
        json={
            "name": "Account 1 Updated",
            "token": "***5678",
            "cookie": "cookie-zzzz9999",
            "enabled": False,
        },
    )

    assert update_response.status_code == 204

    updated_list = await api_client.get("/api/settings/veo-accounts")

    assert updated_list.status_code == 200
    assert updated_list.json() == [
        {
            "id": account_id,
            "name": "Account 1 Updated",
            "token": "***5678",
            "cookie": "***9999",
            "enabled": False,
        }
    ]

    delete_response = await api_client.delete(f"/api/settings/veo-accounts/{account_id}")

    assert delete_response.status_code == 204

    final_list = await api_client.get("/api/settings/veo-accounts")

    assert final_list.status_code == 200
    assert final_list.json() == []


async def test_ai_settings_put_preserves_redacted_secret_and_persists_model(api_client):
    first_put = await api_client.put(
        "/api/settings/ai",
        json={
            "gemini_api_key": "gem-key-12345678",
            "gemini_model": "gemini-2-flash-preview",
            "nano_api_key": "nano-key-87654321",
        },
    )

    assert first_put.status_code == 204

    initial_get = await api_client.get("/api/settings/ai")

    assert initial_get.status_code == 200
    assert initial_get.json() == {
        "gemini_api_key": "***5678",
        "gemini_model": "gemini-2-flash-preview",
        "nano_api_key": "***4321",
    }

    second_put = await api_client.put(
        "/api/settings/ai",
        json={
            "gemini_api_key": "***5678",
            "gemini_model": "gemini-2.5-pro-preview",
        },
    )

    assert second_put.status_code == 204

    final_get = await api_client.get("/api/settings/ai")

    assert final_get.status_code == 200
    assert final_get.json() == {
        "gemini_api_key": "***5678",
        "gemini_model": "gemini-2.5-pro-preview",
        "nano_api_key": "***4321",
    }


async def test_ai_settings_post_alias_accepts_same_payload(api_client):
    """POST /api/settings/ai mirrors PUT — frontend dashboard sends POST."""
    response = await api_client.post(
        "/api/settings/ai",
        json={
            "gemini_api_key": "gem-key-POSTPOST",
            "gemini_model": "gemini-2-flash-preview",
            "nano_api_key": "nano-key-POSTNANO",
        },
    )

    assert response.status_code == 204

    fetched = await api_client.get("/api/settings/ai")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["gemini_api_key"].endswith("POST")
    assert body["gemini_model"] == "gemini-2-flash-preview"
    assert body["nano_api_key"].endswith("NANO")

class _RecordingFlowSettingsClient:
    def __init__(self, *, get_payload=None, update_exc=None, get_exc=None):
        self.get_payload = get_payload or {}
        self.update_exc = update_exc
        self.get_exc = get_exc
        self.updates = []
        self.get_auth_headers = None

    async def get_user_settings(self, auth_context):
        self.get_auth_headers = dict(auth_context.headers)
        if self.get_exc is not None:
            raise self.get_exc
        return self.get_payload

    async def update_user_settings(self, payload, auth_context):
        self.updates.append((payload, dict(auth_context.headers)))
        if self.update_exc is not None:
            raise self.update_exc
        return {}


async def test_view_settings_post_default_body_forwards_false(api_client, monkeypatch):
    import server.routes.settings as settings_route

    flow_client = _RecordingFlowSettingsClient()
    monkeypatch.setattr(settings_route, "_flow_settings_client", flow_client)

    response = await api_client.post("/api/settings", json={})

    assert response.status_code == 200
    assert response.json() == {"return_silent_videos": False}
    assert flow_client.updates == [({"return_silent_videos": False}, {})]


async def test_view_settings_post_forwards_passthrough_payload_and_auth(
    api_client,
    monkeypatch,
):
    import server.routes.settings as settings_route

    flow_client = _RecordingFlowSettingsClient()
    monkeypatch.setattr(settings_route, "_flow_settings_client", flow_client)

    response = await api_client.post(
        "/api/settings",
        json={"return_silent_videos": True, "grid_density": "compact"},
        headers={
            "Authorization": "Bearer raw-secret-token",
            "Cookie": "SID=raw-secret-cookie",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "return_silent_videos": True,
        "grid_density": "compact",
    }
    assert flow_client.updates == [
        (
            {"return_silent_videos": True, "grid_density": "compact"},
            {
                "authorization": "Bearer raw-secret-token",
                "cookie": "SID=raw-secret-cookie",
            },
        )
    ]


async def test_view_settings_get_merges_engine_defaults_with_flow_payload(
    api_client,
    monkeypatch,
):
    import server.routes.settings as settings_route

    flow_client = _RecordingFlowSettingsClient(
        get_payload={
            "lastAcknowledgedChangeLogId": "change-1",
            "completedOnboardingIds": ["AGENT"],
        }
    )
    monkeypatch.setattr(settings_route, "_flow_settings_client", flow_client)

    response = await api_client.get(
        "/api/settings",
        headers={"Authorization": "Bearer raw-secret-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "return_silent_videos": False,
        "lastAcknowledgedChangeLogId": "change-1",
        "completedOnboardingIds": ["AGENT"],
    }
    assert flow_client.get_auth_headers == {"authorization": "Bearer raw-secret-token"}


async def test_view_settings_post_timeout_returns_structured_504(
    api_client,
    monkeypatch,
):
    from flow.settings import FlowSettingsProxyTimeout
    import server.routes.settings as settings_route

    flow_client = _RecordingFlowSettingsClient(update_exc=FlowSettingsProxyTimeout())
    monkeypatch.setattr(settings_route, "_flow_settings_client", flow_client)

    response = await api_client.post(
        "/api/settings",
        json={"return_silent_videos": True},
        headers={"Authorization": "Bearer raw-secret-token"},
    )

    assert response.status_code == 504
    assert response.json() == {
        "detail": {
            "error_kind": "flow_timeout",
            "message": "Flow settings request timed out",
        }
    }
    assert "raw-secret-token" not in response.text


async def test_view_settings_post_flow_error_returns_structured_502(
    api_client,
    monkeypatch,
):
    from flow.settings import FlowSettingsProxyError
    import server.routes.settings as settings_route

    flow_client = _RecordingFlowSettingsClient(
        update_exc=FlowSettingsProxyError(
            "flow_error",
            "Flow settings request failed",
            401,
        )
    )
    monkeypatch.setattr(settings_route, "_flow_settings_client", flow_client)

    response = await api_client.post(
        "/api/settings",
        json={"return_silent_videos": True},
        headers={"Cookie": "SID=raw-secret-cookie"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "error_kind": "flow_error",
            "message": "Flow settings request failed",
            "flow_status_code": 401,
        }
    }
    assert "raw-secret-cookie" not in response.text
