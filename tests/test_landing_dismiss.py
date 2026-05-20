"""Unit tests for ``flow.landing.dismiss_flow_marketing_landing`` (issue #48).

Exercises the CTA-candidate loop with fake Playwright locators so we can
assert: (a) scoped `main ...` selectors are tried before broad ones,
(b) a candidate that scrolls to an anchor fragment is abandoned in
favor of the next candidate, (c) the *is_ready* predicate short-circuits
success.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from flow import landing


class FakeLocator:
    def __init__(self, *, visible: bool, on_click=None):
        self._visible = visible
        self._on_click = on_click
        self.click_calls = 0

    @property
    def first(self):
        return self

    async def is_visible(self, timeout=None):
        return self._visible

    async def click(self, timeout=None, force=False):
        self.click_calls += 1
        if self._on_click is not None:
            self._on_click()


class FakePage:
    def __init__(self, url="https://labs.google/fx/tools/flow"):
        self.url = url
        self._locators: dict[str, FakeLocator] = {}
        self.evaluate_calls: list[str] = []

    def set(self, selector: str, locator: FakeLocator) -> None:
        self._locators[selector] = locator

    def locator(self, selector):
        return self._locators.get(
            selector, FakeLocator(visible=False)
        )

    async def evaluate(self, script, arg=None):
        self.evaluate_calls.append(script)
        # Simulate JS `(el) => el.click()` by invoking the passed handle's click.
        if arg is not None and hasattr(arg, "_js_click"):
            await arg._js_click()

    async def reload(self, wait_until=None, timeout=None):
        self.reload_count = getattr(self, "reload_count", 0) + 1
        if getattr(self, "on_reload", None) is not None:
            self.on_reload()

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls = getattr(self, "goto_calls", [])
        self.goto_calls.append(url)
        self.url = url
        if getattr(self, "on_goto", None) is not None:
            self.on_goto(url)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://labs.google/fx/tools/flow", False),
        ("https://labs.google/fx/tools/flow#capabilities", True),
        ("https://labs.google/fx/tools/flow#partners", True),
        ("https://labs.google/fx/tools/flow#FAQ", True),  # case-insensitive
        ("https://labs.google/fx/tools/flow/project/abc", False),
        ("https://labs.google/fx/tools/flow/project/abc#anything", False),
        ("", False),
    ],
)
def test_is_marketing_anchor_url(url, expected):
    assert landing.is_marketing_anchor_url(url) is expected


@pytest.mark.asyncio
async def test_no_cta_visible_tries_nextauth_fallback_then_returns_false():
    page = FakePage()
    logger = logging.getLogger("test")
    is_ready = AsyncMock(return_value=False)
    result = await landing.dismiss_flow_marketing_landing(
        page, logger, is_ready, per_click_timeout_sec=0.05, max_reloads=0,
    )
    assert result is False
    assert page.goto_calls == [
        "https://labs.google/fx/api/auth/signin/google"
        "?callbackUrl=https%3A%2F%2Flabs.google%2Ffx%2Ftools%2Fflow"
    ]
    is_ready.assert_awaited_once()


@pytest.mark.asyncio
async def test_hero_selector_wins_on_first_try():
    page = FakePage()
    hero = FakeLocator(visible=True)
    page.set("main button:has-text('Create with Flow')", hero)

    ready_calls = {"n": 0}

    async def is_ready():
        ready_calls["n"] += 1
        return ready_calls["n"] >= 1  # ready on first poll

    logger = logging.getLogger("test")
    result = await landing.dismiss_flow_marketing_landing(
        page, logger, is_ready, per_click_timeout_sec=2.0
    )
    assert result is True
    assert hero.click_calls == 1


@pytest.mark.asyncio
async def test_anchor_scroll_candidate_is_abandoned_for_next():
    """First CTA scrolls to #capabilities → helper abandons and tries next."""
    page = FakePage()

    def scroll_to_anchor():
        page.url = "https://labs.google/fx/tools/flow#capabilities"

    def mount_app():
        page.url = "https://labs.google/fx/tools/flow"

    # Both candidates visible; the specific one scrolls to anchor,
    # the broad one correctly mounts.
    bad = FakeLocator(visible=True, on_click=scroll_to_anchor)
    good = FakeLocator(visible=True, on_click=mount_app)
    page.set("main button:has-text('Create with Flow')", bad)
    page.set("button:has-text('Create with Flow')", good)

    ready_flag = {"on": False}

    async def is_ready():
        return ready_flag["on"]

    def set_ready_after_good_click():
        ready_flag["on"] = page.url == "https://labs.google/fx/tools/flow"

    # Patch good.click to also flip the ready flag post-click.
    orig_click = good.click

    async def good_click(timeout=None, force=False):
        await orig_click(timeout=timeout, force=force)
        set_ready_after_good_click()

    good.click = good_click  # type: ignore[assignment]

    logger = logging.getLogger("test")
    result = await landing.dismiss_flow_marketing_landing(
        page, logger, is_ready, per_click_timeout_sec=2.0
    )
    assert result is True
    assert bad.click_calls == 1
    assert good.click_calls == 1


@pytest.mark.asyncio
async def test_project_url_after_click_counts_as_success():
    """Even if is_ready never fires, navigating to /project/ is a success."""
    page = FakePage()

    def navigate_to_project():
        page.url = "https://labs.google/fx/tools/flow/project/abc-123"

    cta = FakeLocator(visible=True, on_click=navigate_to_project)
    page.set("main button:has-text('Create with Flow')", cta)

    async def is_ready():
        return False  # never fires

    logger = logging.getLogger("test")
    result = await landing.dismiss_flow_marketing_landing(
        page, logger, is_ready, per_click_timeout_sec=2.0
    )
    assert result is True
    assert cta.click_calls == 1


@pytest.mark.asyncio
async def test_all_candidates_fail_returns_false():
    page = FakePage()
    stuck = FakeLocator(
        visible=True,
        on_click=lambda: setattr(
            page, "url", "https://labs.google/fx/tools/flow#capabilities"
        ),
    )
    # Every candidate clicks onto the same stuck anchor.
    for sel in landing._CREATE_WITH_FLOW_SELECTORS:
        page.set(sel, stuck)

    async def is_ready():
        return False

    logger = logging.getLogger("test")
    result = await landing.dismiss_flow_marketing_landing(
        page, logger, is_ready, per_click_timeout_sec=0.2, max_reloads=0,
    )
    assert result is False
    # Every visible candidate was attempted.
    assert stuck.click_calls == len(landing._CREATE_WITH_FLOW_SELECTORS)


@pytest.mark.asyncio
async def test_reload_retry_succeeds_when_second_pass_ready():
    """Issue #51 — A/B re-rolls on reload; second attempt enters app."""
    page = FakePage()
    # First pass: CTA click lands on anchor (bad A/B).
    bad = FakeLocator(
        visible=True,
        on_click=lambda: setattr(
            page, "url", "https://labs.google/fx/tools/flow#capabilities"
        ),
    )
    for sel in landing._CREATE_WITH_FLOW_SELECTORS:
        page.set(sel, bad)

    # After reload: is_ready flips to True (app already served).
    def flip_ready():
        page._post_reload = True
    page.on_reload = flip_ready

    async def is_ready():
        return getattr(page, "_post_reload", False)

    logger = logging.getLogger("test")
    result = await landing.dismiss_flow_marketing_landing(
        page, logger, is_ready, per_click_timeout_sec=0.2, max_reloads=2,
    )
    assert result is True
    assert getattr(page, "reload_count", 0) == 1


@pytest.mark.asyncio
async def test_max_reloads_zero_preserves_single_pass():
    """Default opt-out — no reload calls when max_reloads=0."""
    page = FakePage()
    stuck = FakeLocator(
        visible=True,
        on_click=lambda: setattr(
            page, "url", "https://labs.google/fx/tools/flow#capabilities"
        ),
    )
    for sel in landing._CREATE_WITH_FLOW_SELECTORS:
        page.set(sel, stuck)

    async def is_ready():
        return False

    logger = logging.getLogger("test")
    result = await landing.dismiss_flow_marketing_landing(
        page, logger, is_ready, per_click_timeout_sec=0.2, max_reloads=0,
    )
    assert result is False
    assert getattr(page, "reload_count", 0) == 0


@pytest.mark.asyncio
async def test_click_exception_moves_to_next_candidate():
    page = FakePage()

    async def raise_on_click(timeout=None, force=False):
        raise RuntimeError("detached")

    broken = FakeLocator(visible=True)
    broken.click = raise_on_click  # type: ignore[assignment]

    working = FakeLocator(
        visible=True,
        on_click=lambda: setattr(
            page, "url", "https://labs.google/fx/tools/flow/project/xyz"
        ),
    )

    page.set("main button:has-text('Create with Flow')", broken)
    page.set("main [role='button']:has-text('Create with Flow')", working)

    async def is_ready():
        return False

    logger = logging.getLogger("test")
    result = await landing.dismiss_flow_marketing_landing(
        page, logger, is_ready, per_click_timeout_sec=2.0
    )
    assert result is True
    assert working.click_calls == 1
