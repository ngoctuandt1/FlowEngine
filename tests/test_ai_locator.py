import json

import httpx
import pytest

from flow.ai_locator import ai_locate, clear_cache


class FakeLocator:
    def __init__(self, *, visible=False, count=0, html="", visible_error=None):
        self.first = self
        self.visible = visible
        self.count_value = count
        self.html = html
        self.visible_error = visible_error

    async def is_visible(self, **_kwargs):
        if self.visible_error:
            raise self.visible_error
        return self.visible

    async def count(self):
        return self.count_value

    async def inner_html(self):
        return self.html


class FakePage:
    def __init__(self, *, locators=None, html="<button id='go'>Go</button>", tag_name="BUTTON"):
        self.url = "https://labs.google/fx/tools/flow?query=ignored#hash"
        self.locators = locators or {}
        self.body = FakeLocator(html=html)
        self.tag_name = tag_name

    def locator(self, selector):
        if selector == "body":
            return self.body
        return self.locators.get(selector, FakeLocator())

    async def screenshot(self, **_kwargs):
        return b"jpeg-bytes"

    async def evaluate(self, expression):
        assert expression.startswith("document.elementFromPoint(")
        return self.tag_name


@pytest.fixture(autouse=True)
def ai_locator_env(monkeypatch):
    clear_cache()
    monkeypatch.delenv("FLOW_AI_LOCATOR_ENABLED", raising=False)
    monkeypatch.delenv("FLOW_AI_LOCATOR_BASE_URL", raising=False)
    monkeypatch.delenv("FLOW_AI_LOCATOR_MODEL", raising=False)
    monkeypatch.delenv("FLOW_AI_LOCATOR_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("FLOW_AI_LOCATOR_WIRE", raising=False)
    yield
    clear_cache()


def _chat_response(selector="#go", *, prompt=1000, completion=200):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps({"selector": selector, "reasoning": "ok"})}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
        },
    )


def _responses_response(selector="#go"):
    return httpx.Response(
        200,
        json={
            "output": [{"content": [{"text": json.dumps({"selector": selector, "reasoning": "ok"})}]}],
            "usage": {"input_tokens": 500, "output_tokens": 50},
        },
    )


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr("flow.ai_locator.httpx.AsyncClient", make_client)


@pytest.mark.asyncio
async def test_fast_path_candidate_visible_returns_candidate(monkeypatch):
    page = FakePage(locators={"#ready": FakeLocator(visible=True, count=1)})

    result = await ai_locate(page, "click ready", candidates=["#ready"])

    assert result.selector == "#ready"
    assert result.coordinates is None
    assert result.method == "candidate"
    assert result.cost_estimate == 0.0


@pytest.mark.asyncio
async def test_all_candidates_fail_calls_ai(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    seen_requests = []

    def handler(request):
        seen_requests.append(request)
        return _chat_response("#go")

    _install_transport(monkeypatch, handler)
    page = FakePage(locators={"#go": FakeLocator(visible=True, count=1)})

    result = await ai_locate(page, "click go", candidates=["#missing"])

    assert result.selector == "#go"
    assert result.method == "ai"
    assert len(seen_requests) == 1
    assert seen_requests[0].url.path == "/v1/chat/completions"


@pytest.mark.asyncio
async def test_ai_disabled_returns_miss(monkeypatch):
    page = FakePage()

    result = await ai_locate(page, "click go", candidates=["#missing"])

    assert result.selector is None
    assert result.method == "miss"
    assert "ai_disabled" in result.debug_log


@pytest.mark.asyncio
async def test_cache_hit_skips_ai_call(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return _chat_response("#go")

    _install_transport(monkeypatch, handler)
    page = FakePage(locators={"#go": FakeLocator(visible=True, count=1)})

    first = await ai_locate(page, "click go", cache_key="landing-go")
    second = await ai_locate(page, "click go", cache_key="landing-go")

    assert first.method == "ai"
    assert second.method == "cache"
    assert second.selector == "#go"
    assert calls == 1


@pytest.mark.asyncio
async def test_invalid_selector_from_ai_returns_miss(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")

    def handler(_request):
        return _chat_response("#missing")

    _install_transport(monkeypatch, handler)
    page = FakePage()

    result = await ai_locate(page, "click go")

    assert result.method == "miss"
    assert result.selector is None
    assert any("ai_selector_empty:#missing" in item for item in result.debug_log)


@pytest.mark.asyncio
async def test_coordinates_fallback_validated_via_element_from_point(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")

    def handler(_request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"x": 350, "y": 549, "reasoning": "ok"})}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    _install_transport(monkeypatch, handler)
    page = FakePage(tag_name="BUTTON")

    result = await ai_locate(page, "click by point")

    assert result.selector is None
    assert result.coordinates == (350, 549)
    assert result.method == "ai"


@pytest.mark.asyncio
async def test_visibility_check_rejects_hidden_element(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    replies = ["#hidden", "#visible"]

    def handler(_request):
        return _chat_response(replies.pop(0))

    _install_transport(monkeypatch, handler)
    page = FakePage(
        locators={
            "#hidden": FakeLocator(visible=False, count=1),
            "#visible": FakeLocator(visible=True, count=1),
        }
    )

    result = await ai_locate(page, "click visible")

    assert result.selector == "#visible"
    assert result.method == "ai"
    assert any("ai_selector_hidden:#hidden" in item for item in result.debug_log)


@pytest.mark.asyncio
async def test_cost_estimate_computed(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")

    def handler(_request):
        return _chat_response("#go", prompt=1_000_000, completion=1_000_000)

    _install_transport(monkeypatch, handler)
    page = FakePage(locators={"#go": FakeLocator(visible=True, count=1)})

    result = await ai_locate(page, "click go")

    assert result.cost_estimate == 18.0


@pytest.mark.asyncio
async def test_wire_auto_falls_back_to_responses_on_404(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    seen_paths = []

    def handler(request):
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": "missing"})
        return _responses_response("#go")

    _install_transport(monkeypatch, handler)
    page = FakePage(locators={"#go": FakeLocator(visible=True, count=1)})

    result = await ai_locate(page, "click go")

    assert result.selector == "#go"
    assert result.method == "ai"
    assert seen_paths == ["/v1/chat/completions", "/v1/responses"]


@pytest.mark.asyncio
async def test_wire_explicit_chat_only_no_fallback(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    monkeypatch.setenv("FLOW_AI_LOCATOR_WIRE", "chat")
    seen_paths = []

    def handler(request):
        seen_paths.append(request.url.path)
        return httpx.Response(404, json={"error": "missing"})

    _install_transport(monkeypatch, handler)
    page = FakePage(locators={"#go": FakeLocator(visible=True, count=1)})

    result = await ai_locate(page, "click go")

    assert result.method == "miss"
    assert result.selector is None
    assert seen_paths == ["/v1/chat/completions"]
