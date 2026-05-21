import logging

import pytest

from flow.share import (
    FlowShareButtonNotFound,
    FlowShareLinkNotFound,
    copy_flow_share_link,
)


class FakeLocator:
    def __init__(self, *, visible=False, text=""):
        self.visible = visible
        self.text = text
        self.clicked = False

    @property
    def first(self):
        return self

    async def is_visible(self, timeout=0):
        return self.visible

    async def click(self, timeout=0):
        self.clicked = True

    async def wait_for(self, state="visible", timeout=0):
        if not self.visible:
            raise TimeoutError("not visible")

    async def inner_text(self, timeout=0):
        return self.text


class FakePage:
    def __init__(self, *, selectors=None, clipboard="", clipboard_after=None):
        self.selectors = selectors or {}
        self.clipboard = clipboard
        self.clipboard_after = clipboard_after
        self.clipboard_reads = 0

    def locator(self, selector):
        return self.selectors.get(selector, FakeLocator())

    async def evaluate(self, script):
        assert script == "navigator.clipboard.readText()"
        self.clipboard_reads += 1
        if self.clipboard_reads > 1 and self.clipboard_after is not None:
            return self.clipboard_after
        return self.clipboard


@pytest.mark.parametrize(
    "clipboard,clipboard_after,modal_text,expected_url",
    [
        (
            "",
            "https://labs.google/fx/tools/flow/project/p/share/secret-token-123",
            "",
            "https://labs.google/fx/tools/flow/project/p/share/secret-token-123",
        ),
        (
            "",
            "",
            "Copy link https://labs.google/fx/tools/flow/project/p/share/applet-456",
            "https://labs.google/fx/tools/flow/project/p/share/applet-456",
        ),
    ],
)
async def test_copy_flow_share_link_extracts_one_https_url(
    clipboard,
    clipboard_after,
    modal_text,
    expected_url,
):
    page = FakePage(
        clipboard=clipboard,
        clipboard_after=clipboard_after,
        selectors={
            "button:has-text('Share')": FakeLocator(visible=True),
            "[role='dialog']:has-text('Copy link')": FakeLocator(visible=True, text=modal_text),
            "button:has-text('Copy link')": FakeLocator(visible=True),
        },
    )

    result = await copy_flow_share_link(page)

    assert result.url == expected_url
    assert result.token in expected_url


async def test_copy_flow_share_link_rejects_stale_clipboard_and_non_share_url():
    page = FakePage(
        clipboard="https://labs.google/fx/tools/flow/project/p/share/stale-token",
        clipboard_after="https://example.com/not-flow-share",
        selectors={
            "button:has-text('Share')": FakeLocator(visible=True),
            "[role='dialog']:has-text('Copy link')": FakeLocator(
                visible=True,
                text="Copy link https://labs.google/fx/tools/flow/project/p/tool/applet-456",
            ),
            "button:has-text('Copy link')": FakeLocator(visible=True),
        },
    )

    with pytest.raises(FlowShareLinkNotFound):
        await copy_flow_share_link(page)


async def test_copy_flow_share_link_falls_back_when_share_button_missing():
    page = FakePage()

    with pytest.raises(FlowShareButtonNotFound):
        await copy_flow_share_link(page)


async def test_copy_flow_share_link_does_not_log_share_url_or_token(caplog):
    secret_url = "https://labs.google/fx/tools/flow/project/p/share/super-secret-token"
    page = FakePage(
        clipboard="",
        clipboard_after=secret_url,
        selectors={
            "button:has-text('Share')": FakeLocator(visible=True),
            "[role='dialog']:has-text('Copy link')": FakeLocator(visible=True),
            "button:has-text('Copy link')": FakeLocator(visible=True),
        },
    )

    caplog.set_level(logging.INFO, logger="flow.share")
    result = await copy_flow_share_link(page)

    assert result.url == secret_url
    logs = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_url not in logs
    assert "super-secret-token" not in logs
    assert "Flow share link captured" in logs
