from unittest.mock import AsyncMock

import httpx
import pytest

import server.config as config
import server.llm


@pytest.fixture(autouse=True)
def llm_enabled(monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", False, raising=False)
    monkeypatch.setattr(server.llm, "LLM_AVAILABLE", True, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "http://9router.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "cx/gpt-5.4")


async def test_call_llm_happy_path_parses_response_text(monkeypatch):
    payload = {"output": [{"content": [{"text": "A neon alley chase at golden hour."}]}]}

    request = httpx.Request("POST", "http://9router.test/v1/responses")
    response = httpx.Response(200, json=payload, request=request)

    async def fake_post(self, url, *, headers=None, json=None):
        assert url == "http://9router.test/v1/responses"
        assert headers == {"Authorization": "Bearer test-key"}
        # 9router rejects max_output_tokens; the field is omitted by
        # default and only included when LLM_SEND_MAX_TOKENS=1.
        assert json == {
            "model": "cx/gpt-5.4",
            "input": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "usr"},
            ],
        }
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await server.llm.call_llm(system="sys", user="usr", max_tokens=321)

    assert result == "A neon alley chase at golden hour."


async def test_auto_prompt_happy_path(api_client, monkeypatch):
    mock_call = AsyncMock(return_value="A neon alley chase at golden hour.")
    monkeypatch.setattr(server.llm, "call_llm", mock_call)

    response = await api_client.post("/api/llm/auto-prompt", json={"topic": "cyberpunk pursuit"})

    assert response.status_code == 200
    assert response.json() == {"prompt": "A neon alley chase at golden hour."}
    mock_call.assert_awaited_once()


async def test_expand_prompt_happy_path(api_client, monkeypatch):
    mock_call = AsyncMock(return_value="Wide lens dolly through a rain-soaked market, warm signs glowing in mist.")
    monkeypatch.setattr(server.llm, "call_llm", mock_call)

    response = await api_client.post("/api/llm/expand-prompt", json={"idea": "night market in rain"})

    assert response.status_code == 200
    assert response.json()["prompt"].startswith("Wide lens dolly")


async def test_shot_list_happy_path(api_client, monkeypatch):
    mock_call = AsyncMock(return_value=(
        '[{"shot_n":1,"description":"Establishing skyline","duration_seconds":4},'
        '{"shot_n":2,"description":"Runner turns corner","duration_seconds":3}]'
    ))
    monkeypatch.setattr(server.llm, "call_llm", mock_call)

    response = await api_client.post("/api/llm/shot-list", json={"scene": "city sprint", "n_shots": 2})

    assert response.status_code == 200
    assert response.json() == {
        "shots": [
            {"shot_n": 1, "description": "Establishing skyline", "duration_seconds": 4.0},
            {"shot_n": 2, "description": "Runner turns corner", "duration_seconds": 3.0},
        ]
    }


async def test_auto_prompt_validation_error(api_client):
    response = await api_client.post("/api/llm/auto-prompt", json={"topic": ""})

    assert response.status_code == 422


async def test_expand_prompt_validation_error(api_client):
    response = await api_client.post("/api/llm/expand-prompt", json={"idea": " "})

    assert response.status_code == 422


async def test_shot_list_validation_error(api_client):
    response = await api_client.post("/api/llm/shot-list", json={"scene": "opening", "n_shots": 13})

    assert response.status_code == 422


async def test_expand_prompt_returns_503_when_llm_disabled(api_client, monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True, raising=False)

    response = await api_client.post("/api/llm/expand-prompt", json={"idea": "slow aerial over cliffs"})

    assert response.status_code == 503
    assert "LLM_DISABLED" in response.json()["detail"]


async def test_shot_list_returns_503_when_llm_disabled(api_client, monkeypatch):
    monkeypatch.setattr(config, "LLM_DISABLED", True, raising=False)

    response = await api_client.post("/api/llm/shot-list", json={"scene": "desert convoy"})

    assert response.status_code == 503


async def test_llm_endpoints_return_503_when_httpx_missing(api_client, monkeypatch):
    monkeypatch.setattr(server.llm, "LLM_AVAILABLE", False, raising=False)

    auto_prompt = await api_client.post("/api/llm/auto-prompt", json={"topic": "forest spirits"})
    expand_prompt = await api_client.post("/api/llm/expand-prompt", json={"idea": "slow aerial over cliffs"})
    shot_list = await api_client.post("/api/llm/shot-list", json={"scene": "desert convoy"})

    assert auto_prompt.status_code == 503
    assert auto_prompt.json()["detail"] == "LLM is unavailable (set LLM_BASE_URL or check 9router connectivity)."
    assert expand_prompt.status_code == 503
    assert shot_list.status_code == 503


async def test_auto_prompt_returns_502_when_upstream_returns_non_2xx(api_client, monkeypatch):
    request = httpx.Request("POST", "http://9router.test/v1/responses")
    response = httpx.Response(502, request=request)
    mock_call = AsyncMock(side_effect=httpx.HTTPStatusError("bad gateway", request=request, response=response))
    monkeypatch.setattr(server.llm, "call_llm", mock_call)

    result = await api_client.post("/api/llm/auto-prompt", json={"topic": "forest spirits"})

    assert result.status_code == 502
    assert result.json()["detail"] == "LLM upstream request failed."
