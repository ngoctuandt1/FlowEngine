"""Regression tests for issue #45 — L1 cold-start download race.

Two independent layers:
  1. ``flow.client.FlowClient`` binds its ``page.on('response')`` hook as
     soon as ``self.page`` is assigned inside the launch helpers, not
     after they return. Tested by asserting the hook is live by the time
     ``_start_persistent`` returns control to ``start``.
  2. ``flow.wait._finalize_dom_completion`` falls back to a DOM scrape
     when the passive-network buffers are empty at DOM-driven completion,
     recording recovered ids on the client so downstream code sees them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow import client as client_module
from flow.wait import _finalize_dom_completion, _scrape_media_ids_from_dom


UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
UUID_C = "12345678-9abc-def0-1234-56789abcdef0"


# ---------------------------------------------------------------------------
# Layer 2 — wait.py DOM scrape fallback
# ---------------------------------------------------------------------------


def _fake_client(media_events=None):
    events = list(media_events or [])

    def record(mid, source="", url=""):
        events.append({"mid": mid, "source": source, "url": url})

    client = SimpleNamespace(_media_id_events=events)
    client._record_media_id = record
    return client


def _fake_page(evaluate_values):
    page = SimpleNamespace()
    page.evaluate = AsyncMock(return_value=evaluate_values)
    return page


async def test_finalize_dom_completion_returns_existing_buffer_without_scraping():
    client = _fake_client(media_events=[{"mid": UUID_A}])
    page = _fake_page(evaluate_values=[UUID_B])

    ids = await _finalize_dom_completion(client, page)

    assert ids == [UUID_A]
    page.evaluate.assert_not_called()


async def test_finalize_dom_completion_scrapes_from_data_tile_when_buffer_empty():
    client = _fake_client(media_events=[])
    page = _fake_page(evaluate_values=[UUID_A, UUID_B])

    ids = await _finalize_dom_completion(client, page)

    assert set(ids) == {UUID_A, UUID_B}
    # Scraped ids were persisted on the client so download.py can see them.
    recorded = {ev["mid"] for ev in client._media_id_events}
    assert recorded == {UUID_A, UUID_B}
    assert all(ev.get("source") == "dom_scrape" for ev in client._media_id_events)


async def test_finalize_dom_completion_scrapes_from_edit_href_when_buffer_empty():
    href = f"https://labs.google/fx/tools/flow/project/xyz/edit/{UUID_C}"
    client = _fake_client(media_events=[])
    page = _fake_page(evaluate_values=[href])

    ids = await _finalize_dom_completion(client, page)

    assert ids == [UUID_C]


async def test_finalize_dom_completion_returns_empty_when_nothing_found():
    client = _fake_client(media_events=[])
    page = _fake_page(evaluate_values=[])

    ids = await _finalize_dom_completion(client, page)

    assert ids == []
    assert client._media_id_events == []


async def test_scrape_filters_non_media_id_tile_values():
    page = _fake_page(
        evaluate_values=[
            "not-a-uuid",
            "short",
            UUID_A,
            f"/edit/{UUID_B}?x=1",
        ]
    )

    ids = await _scrape_media_ids_from_dom(page)

    # junk dropped, both valid ids recovered (order preserved, de-duped)
    assert ids == [UUID_A, UUID_B]


async def test_scrape_survives_page_evaluate_failure():
    page = SimpleNamespace()
    page.evaluate = AsyncMock(side_effect=RuntimeError("page closed"))

    ids = await _scrape_media_ids_from_dom(page)

    assert ids == []


# ---------------------------------------------------------------------------
# Layer 1 — client.py hook bind order
# ---------------------------------------------------------------------------


class _HookedPage:
    """Minimal Page stand-in that records the order of .on / .goto calls."""

    def __init__(self):
        self.url = "about:blank"
        self.calls: list[tuple[str, object]] = []

    def on(self, event, handler):
        self.calls.append(("on", event))

    async def goto(self, url, *a, **kw):
        self.calls.append(("goto", url))


def _new_client():
    # Bypass FlowClient.__init__ entirely — we only exercise lifecycle methods.
    c = client_module.FlowClient.__new__(client_module.FlowClient)
    c.page = None
    c.context = None
    c.browser = None
    c._pw = None
    c._video_urls = []
    c._calls = []
    c._media_id_events = []
    c._gen_id = None
    c._account_info = None
    c._hooks_bound = False
    return c


def test_setup_network_hooks_is_idempotent():
    c = _new_client()
    page = _HookedPage()
    c.page = page

    c._setup_network_hooks()
    c._setup_network_hooks()
    c._setup_network_hooks()

    on_calls = [call for call in page.calls if call[0] == "on"]
    assert len(on_calls) == 1
    assert on_calls[0][1] == "response"


async def test_start_persistent_binds_hook_before_returning(monkeypatch):
    """After `_start_persistent` returns, a subsequent page.goto must see a
    live response hook — otherwise cold-start response events slip past it
    (issue #45)."""
    c = _new_client()
    c.real_chrome = False
    c.headless = True
    c.action_delay_ms = 0
    c._temp_profile = "ignored"
    c.download_dir = None

    page = _HookedPage()
    context = SimpleNamespace(pages=[page])

    launch = AsyncMock(return_value=context)
    c._pw = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))

    monkeypatch.setattr(c, "_prepare_profile", lambda: None)
    monkeypatch.setattr(client_module.os.environ, "get", lambda *a, **k: "")

    await c._start_persistent()

    # Caller now performs a navigation — must happen AFTER the hook is bound.
    await page.goto("https://labs.google/fx/tools/flow/")

    kinds = [kind for kind, _ in page.calls]
    assert kinds == ["on", "goto"], (
        f"expected hook bound before first goto, got {page.calls!r}"
    )
    assert c._hooks_bound is True
