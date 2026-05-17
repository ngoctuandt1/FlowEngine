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


async def test_generate_idea_rejects_ssrf_private_ip_host(api_client, monkeypatch):
    """A reference URL whose hostname resolves to a private/loopback IP must
    be refused BEFORE any HTTP request goes out — otherwise an attacker can
    reach the cloud metadata service or any internal admin endpoint."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    import server.routes.idea as idea

    # Pretend `attacker.example.com` resolves to AWS link-local metadata IP.
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(idea.socket.AF_INET, None, None, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(idea.socket, "getaddrinfo", fake_getaddrinfo)

    async def boom(*args, **kwargs):  # gemini must NOT be called
        raise AssertionError("gemini_client.generate must not be reached")

    monkeypatch.setattr(idea.gemini_client, "generate", boom)

    response = await api_client.post(
        "/api/idea/generate",
        json={
            "prompt": "ignore",
            "ref_image_urls": ["http://attacker.example.com/leak.png"],
        },
    )
    assert response.status_code == 502
    assert "not allowed" in response.json()["error"]


async def test_generate_idea_rejects_oversized_reference_image(
    api_client, monkeypatch
):
    """A reference URL that streams more than 10 MB must be truncated/refused
    instead of being buffered into worker memory."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    import server.routes.idea as idea

    # Resolve to a public IP so SSRF guard lets the request through.
    monkeypatch.setattr(
        idea.socket,
        "getaddrinfo",
        lambda *a, **kw: [(idea.socket.AF_INET, None, None, "", ("93.184.216.34", 0))],
    )

    # Mock httpx stream → yield chunks until > REF_IMAGE_MAX_BYTES.
    class _FakeStreamResponse:
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self, chunk_size=None):
            chunk = b"\x00" * (1024 * 1024)
            for _ in range(idea.REF_IMAGE_MAX_BYTES // len(chunk) + 2):
                yield chunk

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeStreamResponse()

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
            return _FakeStreamCtx()

    monkeypatch.setattr(idea.httpx, "AsyncClient", _FakeClient)

    async def boom(*args, **kwargs):  # gemini must NOT be called
        raise AssertionError("gemini_client.generate must not be reached")

    monkeypatch.setattr(idea.gemini_client, "generate", boom)

    response = await api_client.post(
        "/api/idea/generate",
        json={
            "prompt": "ignore",
            "ref_image_urls": ["https://cdn.example.com/huge.png"],
        },
    )
    assert response.status_code == 502
    assert "exceeds" in response.json()["error"]


async def test_generate_idea_rejects_cgnat_ip_host(api_client, monkeypatch):
    """CGNAT (100.64.0.0/10, RFC 6598) must be blocked even though
    ipaddress.IPv4Address.is_private returns False for that range."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    import server.routes.idea as idea

    monkeypatch.setattr(
        idea.socket,
        "getaddrinfo",
        lambda *a, **kw: [(idea.socket.AF_INET, None, None, "", ("100.64.0.5", 0))],
    )

    async def boom(*args, **kwargs):
        raise AssertionError("gemini_client.generate must not be reached")

    monkeypatch.setattr(idea.gemini_client, "generate", boom)

    response = await api_client.post(
        "/api/idea/generate",
        json={
            "prompt": "ignore",
            "ref_image_urls": ["http://cgnat.example.com/leak.png"],
        },
    )
    assert response.status_code == 502
    assert "not allowed" in response.json()["error"]


async def test_generate_idea_rejects_oversize_via_content_length(api_client, monkeypatch):
    """A server advertising content-length > 10 MB must be rejected without
    streaming the body."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    import server.routes.idea as idea

    monkeypatch.setattr(
        idea.socket,
        "getaddrinfo",
        lambda *a, **kw: [(idea.socket.AF_INET, None, None, "", ("93.184.216.34", 0))],
    )

    class _FakeStreamResponse:
        headers = {
            "content-type": "image/png",
            "content-length": str(idea.REF_IMAGE_MAX_BYTES + 1),
        }
        extensions: dict = {}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self, chunk_size=None):
            # Should NEVER be invoked once content-length pre-check fires.
            raise AssertionError("aiter_bytes must not be reached")
            yield b""  # pragma: no cover

    class _FakeStreamCtx:
        async def __aenter__(self):
            return _FakeStreamResponse()

        async def __aexit__(self, *exc):
            return False

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
            return _FakeStreamCtx()

    monkeypatch.setattr(idea.httpx, "AsyncClient", _FakeClient)

    async def boom(*args, **kwargs):
        raise AssertionError("gemini_client.generate must not be reached")

    monkeypatch.setattr(idea.gemini_client, "generate", boom)

    response = await api_client.post(
        "/api/idea/generate",
        json={
            "prompt": "ignore",
            "ref_image_urls": ["https://cdn.example.com/over.png"],
        },
    )
    assert response.status_code == 502
    assert "exceeds" in response.json()["error"]
