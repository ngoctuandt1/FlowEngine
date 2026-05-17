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
