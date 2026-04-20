"""Unit tests for the URL-state helpers in :mod:`flow.login`.

``is_login_page`` and ``is_gmail_inbox`` partition the URLs that
``warm_profile.py`` waits on: a logged-in Gmail session lands on one,
an anonymous session redirects to the other, and the pre-redirect
``mail.google.com/`` matches neither. The script uses that distinction
to decide whether to drive the auto-login flow — if a URL silently
matches the wrong predicate (or both, or neither), the script either
burns credit on a pointless login drive or declares success on an
unauthenticated profile.

The concrete URL shapes below come from live Gmail traffic captured on
2026-04-20 while diagnosing the "warm logs 'Already signed in' but the
profile holds zero auth cookies" regression.
"""

from flow.login import is_gmail_inbox, is_login_page

# Canonical signed-in Gmail URL (user-captured 2026-04-20).
GMAIL_INBOX_URL = "https://mail.google.com/mail/u/0/#inbox"

# Canonical anonymous-redirect target (user-captured 2026-04-20).
GMAIL_SIGNIN_URL = (
    "https://accounts.google.com/v3/signin/identifier"
    "?continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F"
    "&service=mail&flowName=GlifWebSignIn&flowEntry=ServiceLogin"
)

# Pre-redirect URL (what `page.goto` first sees before Gmail decides
# which way to send an anonymous session).
GMAIL_ROOT_URL = "https://mail.google.com/"


class TestIsGmailInbox:
    def test_matches_inbox_url(self) -> None:
        assert is_gmail_inbox(GMAIL_INBOX_URL)

    def test_matches_other_user_slots(self) -> None:
        # Gmail supports multi-account slots: /mail/u/1/, /mail/u/2/, ...
        assert is_gmail_inbox("https://mail.google.com/mail/u/1/#inbox")
        assert is_gmail_inbox("https://mail.google.com/mail/u/7/#label/Starred")

    def test_rejects_pre_redirect_root(self) -> None:
        # `mail.google.com/` is where `page.goto` lands before Gmail's
        # server-side redirect picks inbox or sign-in. Treating it as
        # signed-in is the 2026-04-20 regression.
        assert not is_gmail_inbox(GMAIL_ROOT_URL)
        assert not is_gmail_inbox("https://mail.google.com/mail/")

    def test_rejects_signin_redirect(self) -> None:
        assert not is_gmail_inbox(GMAIL_SIGNIN_URL)

    def test_rejects_workspace_marketing(self) -> None:
        # Anonymous sessions under some locales land here; must not be
        # mistaken for inbox.
        assert not is_gmail_inbox("https://workspace.google.com/gmail/about/")


class TestIsLoginPage:
    def test_matches_v3_identifier_redirect(self) -> None:
        assert is_login_page(GMAIL_SIGNIN_URL)

    def test_matches_account_chooser(self) -> None:
        assert is_login_page("https://accounts.google.com/AccountChooser?continue=...")

    def test_matches_consent_screen(self) -> None:
        assert is_login_page("https://accounts.google.com/signin/oauth/consent?...")

    def test_rejects_gmail_inbox(self) -> None:
        assert not is_login_page(GMAIL_INBOX_URL)

    def test_rejects_flow(self) -> None:
        assert not is_login_page("https://labs.google/fx/tools/flow/project/abc")


class TestPartitioning:
    """The two predicates must never both match the same URL — the
    landing-resolution loop in ``warm_profile.py`` treats them as a
    mutually exclusive partition.
    """

    def test_inbox_is_not_signin(self) -> None:
        assert is_gmail_inbox(GMAIL_INBOX_URL)
        assert not is_login_page(GMAIL_INBOX_URL)

    def test_signin_is_not_inbox(self) -> None:
        assert is_login_page(GMAIL_SIGNIN_URL)
        assert not is_gmail_inbox(GMAIL_SIGNIN_URL)

    def test_root_matches_neither(self) -> None:
        # The resolver must keep polling when it sees the pre-redirect
        # root — any false match here silently aborts the wait.
        assert not is_login_page(GMAIL_ROOT_URL)
        assert not is_gmail_inbox(GMAIL_ROOT_URL)
