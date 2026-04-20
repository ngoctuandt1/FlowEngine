"""Source-level trip-wires for `scripts/warm_profile.py`.

The approved flow is auto-login via `flow.login.handle_login_redirect`
with `mail.google.com` as the entry URL. Two revisions have been
rejected by the user (see memory `feedback_warm_profile_manual_gmail.md`
— which now documents the auto-login-at-Gmail rule despite its
historical filename):

  1. Hard-coded `accounts.google.com/ServiceLogin?service=googlefx`
     URL (2026-04-20: "sao tao có bảo login bằng url này đâu").
  2. Manual sign-in via `ctx.wait_for_event("close", timeout=0)` with no
     auto-drive (2026-04-20: "tao bảo mày là tao login bằng tay bao giờ").

These tests read the script source with ``Path.read_text`` and assert
the contract via substring checks — no Playwright runtime, no Chrome
launch. They fail CI immediately if a future edit reintroduces either
of the rejected paths.
"""

from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "warm_profile.py"


def _read_source() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_warm_profile_navigates_to_gmail() -> None:
    """Approved entry URL is `mail.google.com`."""
    src = _read_source()
    assert "mail.google.com" in src, (
        "warm_profile.py must navigate to mail.google.com — the approved "
        "entry URL (see feedback_warm_profile_manual_gmail.md)."
    )


def test_warm_profile_auto_logs_in_via_flow_login() -> None:
    """Auto-login via `flow.login.handle_login_redirect`."""
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
    """Hard-coded `accounts.google.com/ServiceLogin` is unauthorized."""
    src = _read_source()
    banned = ["ServiceLogin", "service=googlefx", "passive=true"]
    hits = [t for t in banned if _live_occurrence(src, t)]
    assert not hits, (
        f"warm_profile.py must NOT hard-code a Google ServiceLogin URL. "
        f"Banned tokens found outside comments/docstrings: {hits}. See "
        f"memory feedback_warm_profile_manual_gmail.md."
    )


def test_warm_profile_does_not_wait_for_manual_close() -> None:
    """Manual sign-in via `wait_for_event('close')` was rejected."""
    src = _read_source()
    banned = ['wait_for_event("close"', "wait_for_event('close'"]
    hits = [t for t in banned if _live_occurrence(src, t)]
    assert not hits, (
        f"warm_profile.py must not block on manual window close — the user "
        f"rejected a manual-only flow. Use handle_login_redirect instead. "
        f"Banned tokens outside comments: {hits}."
    )


def test_warm_profile_positively_confirms_inbox() -> None:
    """Signed-in state must be confirmed by a Gmail-inbox URL match.

    The earlier revision treated "URL does not match sign-in patterns"
    as "signed in" and silently succeeded on anonymous profiles whose
    initial `mail.google.com/` URL hadn't redirected yet (user-reported
    2026-04-20: warm logged "Already signed in" but the profile held
    zero auth cookies). Enforce that the script uses `is_gmail_inbox`
    — the positive signal — so the false-positive cannot recur.
    """
    src = _read_source()
    assert "is_gmail_inbox" in src, (
        "warm_profile.py must use `is_gmail_inbox` from flow.login to "
        "positively confirm the inbox landing URL before declaring the "
        "profile signed in. Checking only for the absence of a sign-in "
        "URL mis-labels anonymous sessions whose redirect has not yet "
        "completed."
    )


def _live_occurrence(src: str, token: str) -> bool:
    """True if `token` appears in code (not just in comments / module docstring).

    The module docstring + `#` comments intentionally mention the banned
    names to document why they are banned. We only fail on a token that
    survives past the module docstring and outside `#` comment lines.
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
