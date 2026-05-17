from unittest.mock import AsyncMock

import server.services.gemini_client as gemini_client


async def test_generate_idea_returns_parsed_payload(api_client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    mock_generate = AsyncMock(
        return_value=(
            "Here is the plan:\n"
            "{\n"
            '  "script": "## Kịch bản đề xuất\\n\\n1. Mở đầu",\n'
            '  "nodes": [\n'
            '    {"type": "text-to-image", "prompt": "hero product shot", "ratio": "9:16", "parent_index": null},\n'
            '    {"type": "frames-to-video", "prompt": "camera pushes in", "ratio": "9:16", "parent_index": 0}\n'
            "  ]\n"
            "}"
        )
    )
    monkeypatch.setattr(gemini_client, "generate", mock_generate)

    response = await api_client.post(
        "/api/idea/generate",
        json={
            "prompt": "Lên ý tưởng video quảng cáo túi xách cao cấp",
            "chain_id": "chain-123",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "script": "## Kịch bản đề xuất\n\n1. Mở đầu",
        "nodes": [
            {
                "type": "text-to-image",
                "prompt": "hero product shot",
                "ratio": "9:16",
                "parent_index": None,
            },
            {
                "type": "frames-to-video",
                "prompt": "camera pushes in",
                "ratio": "9:16",
                "parent_index": 0,
            },
        ],
    }

    mock_generate.assert_awaited_once()
    kwargs = mock_generate.await_args.kwargs
    assert kwargs["api_key"] == "test-gemini-key"
    assert kwargs["model"] == "gemini-2-flash-preview"
    assert kwargs["images"] == []
    assert "chain-123" in kwargs["prompt"]


async def test_generate_idea_returns_503_when_api_key_missing(api_client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    response = await api_client.post(
        "/api/idea/generate",
        json={"prompt": "Lên ý tưởng video quảng cáo"},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "Gemini API key not configured"}


async def test_generate_idea_prefers_saved_settings_over_env(api_client, monkeypatch):
    """Saved AI settings (Settings UI -> get_ai_settings) take priority over env."""
    # Env supplies a stale fallback; saved settings must win.
    monkeypatch.setenv("GEMINI_API_KEY", "env-fallback-key")
    monkeypatch.setenv("GEMINI_MODEL", "env-fallback-model")

    put_response = await api_client.put(
        "/api/settings/ai",
        json={
            "gemini_api_key": "saved-key-abcd1234",
            "gemini_model": "saved-gemini-model",
        },
    )
    assert put_response.status_code == 204

    mock_generate = AsyncMock(
        return_value='{"script": "ok", "nodes": []}'
    )
    monkeypatch.setattr(gemini_client, "generate", mock_generate)

    response = await api_client.post(
        "/api/idea/generate",
        json={"prompt": "Sản phẩm cao cấp"},
    )

    assert response.status_code == 200
    kwargs = mock_generate.await_args.kwargs
    assert kwargs["api_key"] == "saved-key-abcd1234"
    assert kwargs["model"] == "saved-gemini-model"


async def test_generate_idea_falls_back_to_env_when_settings_empty(
    api_client, monkeypatch
):
    """No saved settings -> env vars are used."""
    monkeypatch.setenv("GEMINI_API_KEY", "env-only-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    mock_generate = AsyncMock(return_value='{"script": "ok", "nodes": []}')
    monkeypatch.setattr(gemini_client, "generate", mock_generate)

    response = await api_client.post(
        "/api/idea/generate",
        json={"prompt": "Ý tưởng"},
    )

    assert response.status_code == 200
    kwargs = mock_generate.await_args.kwargs
    assert kwargs["api_key"] == "env-only-key"
    assert kwargs["model"] == "gemini-2-flash-preview"  # default model
