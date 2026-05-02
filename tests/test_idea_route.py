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
