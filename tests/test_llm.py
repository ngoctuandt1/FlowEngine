from unittest.mock import AsyncMock

import pytest

import server.config as config
import server.llm


@pytest.fixture(autouse=True)
def llm_enabled(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", False, raising=False)


async def test_auto_prompt_happy_path(api_client, monkeypatch):
    mock_call = AsyncMock(return_value="A neon alley chase at golden hour.")
    monkeypatch.setattr(server.llm, "call_claude", mock_call)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    response = await api_client.post("/api/llm/auto-prompt", json={"topic": "cyberpunk pursuit"})

    assert response.status_code == 200
    assert response.json() == {"prompt": "A neon alley chase at golden hour."}
    mock_call.assert_awaited_once()


async def test_expand_prompt_happy_path(api_client, monkeypatch):
    mock_call = AsyncMock(return_value="Wide lens dolly through a rain-soaked market, warm signs glowing in mist.")
    monkeypatch.setattr(server.llm, "call_claude", mock_call)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    response = await api_client.post("/api/llm/expand-prompt", json={"idea": "night market in rain"})

    assert response.status_code == 200
    assert response.json()["prompt"].startswith("Wide lens dolly")


async def test_shot_list_happy_path(api_client, monkeypatch):
    mock_call = AsyncMock(return_value=(
        '[{"shot_n":1,"description":"Establishing skyline","duration_seconds":4},'
        '{"shot_n":2,"description":"Runner turns corner","duration_seconds":3}]'
    ))
    monkeypatch.setattr(server.llm, "call_claude", mock_call)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    response = await api_client.post("/api/llm/shot-list", json={"scene": "city sprint", "n_shots": 2})

    assert response.status_code == 200
    assert response.json() == {
        "shots": [
            {"shot_n": 1, "description": "Establishing skyline", "duration_seconds": 4.0},
            {"shot_n": 2, "description": "Runner turns corner", "duration_seconds": 3.0},
        ]
    }


async def test_auto_prompt_validation_error(api_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    response = await api_client.post("/api/llm/auto-prompt", json={"topic": ""})

    assert response.status_code == 422


async def test_expand_prompt_validation_error(api_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    response = await api_client.post("/api/llm/expand-prompt", json={"idea": " "})

    assert response.status_code == 422


async def test_shot_list_validation_error(api_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    response = await api_client.post("/api/llm/shot-list", json={"scene": "opening", "n_shots": 13})

    assert response.status_code == 422


async def test_auto_prompt_returns_503_when_api_key_unset(api_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = await api_client.post("/api/llm/auto-prompt", json={"topic": "forest spirits"})

    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


async def test_expand_prompt_returns_503_when_llm_disabled(api_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_DISABLED", True, raising=False)

    response = await api_client.post("/api/llm/expand-prompt", json={"idea": "slow aerial over cliffs"})

    assert response.status_code == 503
    assert "LLM_DISABLED" in response.json()["detail"]


async def test_shot_list_returns_503_when_api_key_unset(api_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = await api_client.post("/api/llm/shot-list", json={"scene": "desert convoy"})

    assert response.status_code == 503


async def test_shot_list_returns_503_when_llm_disabled(api_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_DISABLED", True, raising=False)

    response = await api_client.post("/api/llm/shot-list", json={"scene": "desert convoy"})

    assert response.status_code == 503
