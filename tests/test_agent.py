from __future__ import annotations

import logging
import re

import pytest

from flow import agent


PROJECT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PROJECT_URL = f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}"


class _Locator:
    def __init__(self, *, visible: bool = False, button_texts: tuple[str, ...] = ()) -> None:
        self._visible = visible
        self._button_texts = button_texts

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def filter(self, *, has_text):
        if isinstance(has_text, re.Pattern):
            visible = any(has_text.search(text) for text in self._button_texts)
        else:
            visible = any(str(has_text) in text for text in self._button_texts)
        return _Locator(visible=visible)

    async def is_visible(self, *, timeout: int = 0) -> bool:
        return self._visible


class _ComposerPage:
    def __init__(
        self,
        *,
        url: str = PROJECT_URL,
        text_input_visible: bool = False,
        button_texts: tuple[str, ...] = (),
        submit_visible: bool = False,
    ) -> None:
        self.url = url
        self._text_input_visible = text_input_visible
        self._button_texts = button_texts
        self._submit_visible = submit_visible

    def locator(self, selector: str):
        if selector == "button, [role='button']":
            return _Locator(button_texts=self._button_texts)
        if selector == "button:has(i:text-is('arrow_forward'))":
            return _Locator(visible=self._submit_visible)
        return _Locator(visible=self._text_input_visible)


class _NoopResponseContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _DomFallbackPage:
    url = PROJECT_URL

    def expect_response(self, *_args, **_kwargs):
        return _NoopResponseContext()


class _Button:
    def __init__(self) -> None:
        self.clicks = 0

    async def click(self, *, timeout: int = 0) -> None:
        self.clicks += 1


@pytest.mark.asyncio
async def test_agent_normal_composer_gate_rejects_landing_cta():
    page = _ComposerPage(button_texts=("Create with Google Flow",))

    assert not await agent._composer_visible_once(page, project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_agent_normal_composer_gate_requires_matching_project():
    page = _ComposerPage(
        url="https://labs.google/fx/tools/flow/project/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        text_input_visible=True,
        button_texts=("Video x1",),
    )

    assert not await agent._composer_visible_once(page, project_id=PROJECT_ID)


@pytest.mark.asyncio
async def test_agent_dom_fallback_disables_after_missing_reverse_api(monkeypatch):
    button = _Button()

    async def no_op(*_args, **_kwargs):
        return None

    async def no_token(*_args, **_kwargs):
        return None

    async def agent_on(*_args, **_kwargs):
        return agent._AgentDetection("on")

    async def agent_button(*_args, **_kwargs):
        return button

    async def dom_disabled(*_args, **_kwargs):
        return True

    monkeypatch.setattr(agent, "install_agent_auth_probe", no_op)
    monkeypatch.setattr(agent, "_wait_for_bearer_token", no_token)
    monkeypatch.setattr(agent, "_detect_agent_state", agent_on)
    monkeypatch.setattr(agent, "_agent_button", agent_button)
    monkeypatch.setattr(agent, "_wait_for_dom_disabled", dom_disabled)

    result = await agent.disable_agent_mode_if_active(
        _DomFallbackPage(),
        profile_name="profile-a",
        target_url=PROJECT_URL,
    )

    assert result.status == "toggled_off_dom"
    assert result.previous_detection_state == "on"
    assert result.restoration_token_available
    assert button.clicks == 1


@pytest.mark.asyncio
async def test_agent_restore_mutation_log_includes_required_context(
    monkeypatch,
    caplog,
):
    async def no_op(*_args, **_kwargs):
        return None

    async def bearer(*_args, **_kwargs):
        return "Bearer token"

    async def patch_agent_state(*_args, **_kwargs):
        return {"status": 200, "ok": True, "text": "{}"}

    monkeypatch.setattr(agent, "install_agent_auth_probe", no_op)
    monkeypatch.setattr(agent, "_wait_for_bearer_token", bearer)
    monkeypatch.setattr(agent, "_patch_agent_state", patch_agent_state)
    caplog.set_level(logging.INFO, logger="flow.agent")

    result = await agent.restore_agent_state(
        object(),
        agent.AgentRestoreToken(project_id=PROJECT_ID, previous_state="on"),
        profile_name="profile-a",
    )

    assert result.status == "restored_api"
    assert "Agent restore mutation" in caplog.text
    assert "previous=on" in caplog.text
    assert "restore_token=True" in caplog.text
