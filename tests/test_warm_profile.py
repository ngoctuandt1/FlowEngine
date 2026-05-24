"""Source-level trip-wires for `scripts/warm_profile.py`.

The approved flow is auto-login via `flow.login.handle_login_redirect`
with ``labs.google/fx/tools/flow`` as the entry URL. Three revisions
have been rejected by the user (see memory
``feedback_warm_profile_manual_gmail.md`` — its filename is historical;
the rule it documents now scopes to "auto-login at the Flow entry
URL", not Gmail):

  1. Hard-coded ``accounts.google.com/ServiceLogin?service=googlefx``
     URL (2026-04-20: "sao tao có bảo login bằng url này đâu").
  2. Manual sign-in via ``ctx.wait_for_event("close", timeout=0)`` with
     no auto-drive (2026-04-20: "tao bảo mày là tao login bằng tay bao
     giờ").
  3. ``mail.google.com`` as the entry URL (2026-05-21: Workspace orgs
     can disable Gmail while keeping Flow enabled — using Gmail as the
     login proxy reports "Already signed in" on a
     ``access.workspace.google.com/ServiceNotAllowed`` URL even when
     Flow itself is reachable, and vice-versa).

These tests read the script source with ``Path.read_text`` and assert
the contract via substring checks — no Playwright runtime, no Chrome
launch. They fail CI immediately if a future edit reintroduces any
of the rejected paths.
"""

from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "warm_profile.py"


def _read_source() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_warm_profile_navigates_to_flow() -> None:
    """Approved entry URL is ``labs.google/fx/tools/flow``."""
    src = _read_source()
    assert "labs.google/fx/tools/flow" in src, (
        "warm_profile.py must navigate to labs.google/fx/tools/flow — "
        "the approved entry URL (see feedback_warm_profile_manual_gmail.md; "
        "Gmail entry was retired 2026-05-21 because Workspace orgs can "
        "disable Gmail while keeping Flow enabled)."
    )


def test_warm_profile_does_not_use_gmail_entry() -> None:
    """``mail.google.com`` as the entry URL is rejected (2026-05-21)."""
    src = _read_source()
    banned = ["mail.google.com"]
    hits = [t for t in banned if _live_occurrence(src, t)]
    assert not hits, (
        "warm_profile.py must NOT use mail.google.com as the entry URL. "
        "Workspace orgs that disable Gmail while keeping Flow enabled "
        "report 'Already signed in' on a ServiceNotAllowed URL — the "
        "warm signal must match what the worker needs (Flow access), "
        f"not Gmail. Banned tokens outside comments/docstring: {hits}."
    )


def test_warm_profile_auto_logs_in_via_flow_login() -> None:
    """Auto-login via ``flow.login.handle_login_redirect``."""
    src = _read_source()
    assert "handle_login_redirect" in src, (
        "warm_profile.py must import and call `handle_login_redirect` from "
        "flow.login to drive the Google sign-in flow automatically."
    )
    assert "from flow.login import" in src, (
        "warm_profile.py must import from flow.login (the authoritative "
        "auto-login module; uses profiles_ultra.txt credentials)."
    )


def test_warm_profile_uses_real_chrome_over_cdp() -> None:
    """Google sign-in warming must attach to a real Chrome instance over CDP."""
    src = _read_source()
    assert "connect_over_cdp" in src, (
        "warm_profile.py must connect to a real Chrome instance over CDP. "
        "Google blocks sign-in in the Playwright-managed browser path used "
        "before this fix."
    )
    assert "--remote-debugging-port=" in src, (
        "warm_profile.py must launch Chrome with a CDP port so Playwright "
        "can attach without becoming the browser launcher."
    )


def test_warm_profile_rejects_service_login_url() -> None:
    """Hard-coded ``accounts.google.com/ServiceLogin`` is unauthorized."""
    src = _read_source()
    banned = ["ServiceLogin", "service=googlefx", "passive=true"]
    hits = [t for t in banned if _live_occurrence(src, t)]
    assert not hits, (
        f"warm_profile.py must NOT hard-code a Google ServiceLogin URL. "
        f"Banned tokens found outside comments/docstrings: {hits}. See "
        f"memory feedback_warm_profile_manual_gmail.md."
    )


def test_warm_profile_does_not_wait_for_manual_close() -> None:
    """Manual sign-in via ``wait_for_event('close')`` was rejected."""
    src = _read_source()
    banned = ['wait_for_event("close"', "wait_for_event('close'"]
    hits = [t for t in banned if _live_occurrence(src, t)]
    assert not hits, (
        f"warm_profile.py must not block on manual window close — the user "
        f"rejected a manual-only flow. Use handle_login_redirect instead. "
        f"Banned tokens outside comments: {hits}."
    )


def test_warm_profile_positively_confirms_flow_landing() -> None:
    """Signed-in state must be confirmed by a Flow URL match.

    The 2026-04-20 regression treated "URL does not match sign-in
    patterns" as "signed in" and silently succeeded on anonymous
    profiles whose redirect had not yet completed. The 2026-05-21 Gmail
    regression went one step further: ``is_gmail_inbox`` was the
    positive signal but Workspace's ``ServiceNotAllowed`` URL ALSO
    failed to match it — Gmail-disabled-Flow-enabled accounts looked
    healthy because warm picked the wrong app to probe. Enforce that
    the script uses ``is_flow_app_url`` (the new positive signal
    matching Flow's own origin) AND ``is_service_blocked`` (the
    dead-account negative signal) so neither failure class can recur.
    """
    src = _read_source()
    assert "is_flow_app_url" in src, (
        "warm_profile.py must use `is_flow_app_url` from flow.login to "
        "positively confirm the Flow landing URL before declaring the "
        "profile signed in. Checking only for the absence of a sign-in "
        "URL mis-labels anonymous sessions whose redirect has not yet "
        "completed."
    )
    assert "is_flow_app_authenticated" in src, (
        "warm_profile.py must require `is_flow_app_authenticated` before "
        "declaring a labs.google/fx landing signed in. The anonymous Flow "
        "marketing page shares that URL and has no auth cookies."
    )
    assert "is_service_blocked" in src, (
        "warm_profile.py must check `is_service_blocked` so Workspace's "
        "ServiceNotAllowed redirect surfaces as a hard failure (the dead "
        "account class — feedback_flow_service_not_allowed_account_dead.md "
        "— must not look like 'already signed in')."
    )


def test_warm_profile_raises_on_service_blocked() -> None:
    """Dead-account landing must raise, not silently exit 0."""
    src = _read_source()
    assert "FlowServiceDisabled" in src, (
        "warm_profile.py must define and raise FlowServiceDisabled when "
        "Workspace returns ServiceNotAllowed — silently succeeding on a "
        "dead profile is the 2026-05-21 regression class."
    )


@pytest.mark.asyncio
async def test_resolve_flow_landing_clicks_cta_on_anonymous_marketing(
    monkeypatch,
) -> None:
    """Anonymous Flow marketing URL must not be terminal signed-in state."""
    from scripts import warm_profile

    page = _FakeFlowLandingPage(
        url="https://labs.google/fx/tools/flow",
        cta_redirect_url="https://accounts.google.com/v3/signin/identifier",
    )
    monkeypatch.setattr(warm_profile, "FLOW_AUTH_POLL_TIMEOUT_SEC", 0)

    state = await warm_profile._resolve_flow_landing(page, timeout_sec=1)

    assert state == "signin"
    assert page.clicked_selectors, "Flow CTA click must be attempted"


@pytest.mark.asyncio
async def test_resolve_flow_landing_times_out_without_auth_signal() -> None:
    """Bare labs.google/fx marketing URL cannot resolve as already signed in."""
    from scripts import warm_profile

    page = _FakeFlowLandingPage(url="https://labs.google/fx/tools/flow")

    with pytest.raises(
        TimeoutError,
        match="Flow landing did not resolve to signed-in/login/blocked",
    ):
        await warm_profile._resolve_flow_landing(page, timeout_sec=0)


@pytest.mark.asyncio
async def test_is_flow_app_authenticated_requires_explicit_signal() -> None:
    """Auth helper accepts app shell signals, not marketing URL alone."""
    from flow.login import is_flow_app_authenticated

    marketing = _FakeFlowLandingPage(url="https://labs.google/fx/tools/flow")
    project = _FakeFlowLandingPage(
        url="https://labs.google/fx/tools/flow/project/project-id"
    )
    new_project_button = _FakeFlowLandingPage(
        url="https://labs.google/fx/tools/flow",
        visible_selectors={"button:has-text('New project')"},
    )
    vi_new_project_button = _FakeFlowLandingPage(
        url="https://labs.google/fx/tools/flow",
        visible_selectors={"button:has-text('Dự án mới')"},
    )
    icon_only_new_project_button = _FakeFlowLandingPage(
        url="https://labs.google/fx/tools/flow",
        visible_selectors={"button:has(i:has-text('add_2'))"},
    )
    account_menu = _FakeFlowLandingPage(
        url="https://labs.google/fx/tools/flow",
        visible_selectors={'nav [aria-label*="Google Account" i]'},
    )

    assert await is_flow_app_authenticated(marketing) is False
    assert await is_flow_app_authenticated(project) is True
    assert await is_flow_app_authenticated(new_project_button) is True
    assert await is_flow_app_authenticated(vi_new_project_button) is True
    assert await is_flow_app_authenticated(icon_only_new_project_button) is True
    assert await is_flow_app_authenticated(account_menu) is True


class _FakeFlowLandingPage:
    def __init__(
        self,
        *,
        url: str,
        cta_redirect_url: str | None = None,
        visible_selectors: set[str] | None = None,
    ):
        self.url = url
        self.cta_redirect_url = cta_redirect_url
        self.visible_selectors = visible_selectors or set()
        self.clicked_selectors: list[str] = []

    def locator(self, selector: str):
        return _FakeLocator(self, selector)

    async def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None


class _FakeLocator:
    def __init__(self, page: _FakeFlowLandingPage, selector: str):
        self.page = page
        self.selector = selector
        self.first = self

    async def is_visible(self, **_kwargs) -> bool:
        return self.selector in self.page.visible_selectors

    async def click(self, **_kwargs) -> None:
        if self.page.cta_redirect_url is None:
            raise RuntimeError(f"selector not clickable: {self.selector}")
        self.page.clicked_selectors.append(self.selector)
        self.page.url = self.page.cta_redirect_url


def _live_occurrence(src: str, token: str) -> bool:
    """True if ``token`` appears in code (not just in comments / module docstring).

    The module docstring + ``#`` comments intentionally mention the
    banned names to document why they are banned. We only fail on a
    token that survives past the module docstring and outside ``#``
    comment lines.
    """
    if token not in src:
        return False
    # Strip `#` comment lines outright.
    non_comment = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    if token not in non_comment:
        return False
    # Strip the module docstring (first triple-quoted block).
    opener = non_comment.find('"""')
    if opener == 0:
        closer = non_comment.find('"""', opener + 3)
        if closer != -1:
            non_comment = non_comment[closer + 3 :]
    return token in non_comment
