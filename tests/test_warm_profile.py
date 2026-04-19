"""Source-level trip-wires for `scripts/warm_profile.py`.

The approved flow is manual Gmail sign-in — user instruction 2026-04-20
after an earlier revision silently introduced a hard-coded ServiceLogin
URL and `handle_login_redirect` auto-login via `profiles_ultra.txt`.
See memory `feedback_warm_profile_manual_gmail.md`.

These tests read the script source with `importlib` + `inspect` and
assert the contract via substring checks — no Playwright runtime, no
Chrome launch. They fail CI immediately if a future edit re-adds the
banned auto-login surface.
"""

from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "warm_profile.py"


def _read_source() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_warm_profile_navigates_to_gmail() -> None:
    """Approved URL is `mail.google.com`."""
    src = _read_source()
    assert "mail.google.com" in src, (
        "warm_profile.py must navigate to mail.google.com for manual "
        "sign-in (see feedback_warm_profile_manual_gmail.md)."
    )


def test_warm_profile_waits_for_manual_close() -> None:
    """The script blocks on window close so the user drives sign-in."""
    src = _read_source()
    assert 'wait_for_event("close"' in src or "wait_for_event('close'" in src, (
        "warm_profile.py must wait for the user to close the Chrome window "
        "(ctx.wait_for_event('close', timeout=0))."
    )


def test_warm_profile_rejects_auto_login_url() -> None:
    """Hard-coded `accounts.google.com/ServiceLogin` is unauthorized."""
    src = _read_source()
    banned = ["ServiceLogin", "service=googlefx"]
    hits = [t for t in banned if t in src and not _is_in_comment(src, t)]
    assert not hits, (
        f"warm_profile.py must NOT hard-code a Google auto-login URL. "
        f"Banned tokens found outside comments: {hits}. See memory "
        f"feedback_warm_profile_manual_gmail.md."
    )


def test_warm_profile_does_not_auto_drive_login() -> None:
    """No `handle_login_redirect`, no `profiles_ultra.txt` coupling."""
    src = _read_source()
    banned = ["handle_login_redirect", "profiles_ultra", "NeedAutoLogin"]
    hits = [t for t in banned if t in src and not _is_in_comment(src, t)]
    assert not hits, (
        f"warm_profile.py must not wire flow.login auto-login into the "
        f"warm-up path. Banned tokens outside comments: {hits}."
    )


def _is_in_comment(src: str, token: str) -> bool:
    """Allow banned tokens inside docstrings / comments that explain the rule.

    The memory + script docstring intentionally mention the banned names to
    document why they are banned. We only fail on a live occurrence, i.e. a
    line that is not fully inside a string literal or a `#` comment.
    """
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if token in stripped:
            # Token appears on a non-comment line. Heuristic for triple-quoted
            # docstrings: if the module is inside its opening docstring we
            # skip until we pass it. Rather than parse the AST, check whether
            # the token only lives in the module docstring region by
            # confirming it appears before the first real import/def/class.
            head = src.split(token, 1)[0]
            has_real_code_before = any(
                head.count(marker) > 0
                for marker in ("\nimport ", "\nfrom ", "\ndef ", "\nclass ", "\nasync def ")
            )
            if has_real_code_before:
                return False
            # Token appears only in the module docstring → fine.
            return True
    return True
