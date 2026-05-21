from types import SimpleNamespace
from unittest.mock import AsyncMock
import re

import pytest

from flow import characters as flow_characters
from flow.characters import (
    CHARACTER_EDITOR_SELECTOR,
    CharacterCreateError,
    CharacterTagValidationError,
    create_character_via_ui,
    generate_character_prompt,
    resolve_character_tags,
    validate_character_tags,
)
from flow.operations import generate


class FakeLocator:
    def __init__(
        self,
        *,
        visible=True,
        enabled=True,
        count=1,
        text="",
        on_click=None,
    ):
        self.visible = visible
        self.enabled = enabled
        self._count = count
        self.text = text
        self.on_click = on_click
        self.clicks = 0
        self.waits = 0
        self.filters = []

    @property
    def first(self):
        return self

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return self

    def nth(self, _index):
        return self

    async def wait_for(self, **_kwargs):
        self.waits += 1
        if not self.visible:
            raise TimeoutError("not visible")

    async def is_visible(self, **_kwargs):
        return self.visible

    async def is_enabled(self, **_kwargs):
        return self.enabled

    async def count(self):
        return self._count

    async def click(self, **_kwargs):
        self.clicks += 1
        if self.on_click:
            self.on_click()

    async def inner_text(self, **_kwargs):
        return self.text


class FakeLocatorList:
    def __init__(self, locators):
        self.locators = locators
        self.filters = []

    @property
    def first(self):
        return self.nth(0)

    def filter(self, **kwargs):
        self.filters.append(kwargs)
        return self

    def nth(self, index):
        return self.locators[index]

    async def count(self):
        return len(self.locators)


class FakeKeyboard:
    def __init__(self):
        self.presses = []
        self.typed = []

    async def press(self, key):
        self.presses.append(key)

    async def type(self, text, **_kwargs):
        self.typed.append(text)


class FakePage:
    def __init__(self, client=None, *, create_button=True, split_create_buttons=False):
        self.client = client
        self.keyboard = FakeKeyboard()
        self.gotos = []
        self.new_button = FakeLocator(text="New character")
        self.editor = FakeLocator(text="Describe your character…")
        self.model_opener = FakeLocator(text="🍌 Nano Banana 2 arrow_drop_down")
        self.model_option = FakeLocator(text="Nano Banana 2")
        self.create_button = FakeLocator(
            visible=create_button,
            enabled=create_button,
            count=1 if create_button else 0,
            text="arrow_forward Create",
            on_click=self._record_entity_call,
        )
        self.prompt_create_button = FakeLocator(
            visible=True,
            enabled=True,
            count=1,
            text="add_2 Create",
            on_click=self._record_copy_entity_call,
        )
        self.split_create_buttons = split_create_buttons
        self.missing = FakeLocator(visible=False, count=0)
        self.context = SimpleNamespace(
            request=SimpleNamespace(post=AsyncMock(return_value=FakePromptResponse()))
        )

    async def goto(self, url, **_kwargs):
        self.gotos.append(url)

    def get_by_role(self, role, name=None):
        text = _regex_text(name)
        if role == "button" and re.search(r"New\s+character", text, re.I):
            return self.new_button
        if role == "button" and "Nano Banana 2" in text:
            return self.model_opener
        if role == "menuitem" and "Nano Banana 2" in text:
            return self.model_option
        return self.missing

    def locator(self, selector):
        if selector == CHARACTER_EDITOR_SELECTOR:
            return self.editor
        if selector == "button":
            if self.split_create_buttons:
                return FakeLocatorList([self.prompt_create_button, self.create_button])
            return FakeLocatorList([self.create_button])
        if selector.startswith("button:has"):
            if self.split_create_buttons:
                return self.missing
            return self.create_button
        return self.missing

    def _record_entity_call(self):
        if self.client is not None:
            self.client._calls.append(
                {
                    "url": "https://aisandbox-pa.googleapis.com/v1/flow/entities",
                    "method": "POST",
                    "status": 200,
                    "body": {"entityId": "entity-123"},
                }
            )

    def _record_copy_entity_call(self):
        if self.client is not None:
            self.client._calls.append(
                {
                    "url": "https://aisandbox-pa.googleapis.com/v1/flow/entities:copyEntity",
                    "method": "POST",
                    "status": 200,
                    "body": {"entityId": "wrong-entity"},
                }
            )


class FakePromptResponse:
    status = 200

    async def json(self):
        return {"result": {"data": {"json": {"prompt": "preset prompt"}}}}


def _regex_text(pattern):
    if pattern is None:
        return ""
    raw = getattr(pattern, "pattern", pattern)
    text = str(raw).replace("\\s+", " ")
    return text.replace("\\ ", " ")


def _client(*, create_button=True):
    client = SimpleNamespace(_calls=[])
    page = FakePage(client, create_button=create_button)
    client.page = page
    return client


def _client_with_split_create_buttons():
    client = SimpleNamespace(_calls=[])
    page = FakePage(client, split_create_buttons=True)
    client.page = page
    return client


def test_resolve_character_tags_matches_known_characters():
    result = resolve_character_tags(
        "Frame @Ivy and @Bo-Lam near unresolved @Ghost.",
        [
            {"id": "c1", "name": "Ivy Tran", "project_id": "p1"},
            {"id": "c2", "name": "Bo Lam", "tag": "Bo-Lam"},
        ],
    )

    assert [item.id for item in result.resolved] == ["c1", "c2"]
    assert result.unresolved_tags == ["@Ghost"]
    assert "simple @tag" in result.validation_errors[0]


def test_validate_character_tags_leaves_unresolved_visible():
    with pytest.raises(CharacterTagValidationError) as exc:
        validate_character_tags("Use @Missing", [])

    assert exc.value.unresolved_tags == ["@Missing"]
    assert "@Missing" in str(exc.value)


@pytest.mark.asyncio
async def test_create_character_via_ui_uses_nano_banana_path():
    client = _client()

    result = await create_character_via_ui(
        client,
        project_id="d254e570-f789-4afd-a0df-457682534809",
        prompt="Describe @Ivy",
        known_characters=[{"id": "ivy", "name": "Ivy"}],
    )

    assert client.page.gotos == [
        "https://labs.google/fx/tools/flow/project/d254e570-f789-4afd-a0df-457682534809/characters"
    ]
    assert client.page.new_button.clicks == 1
    assert client.page.editor.waits == 1
    assert client.page.keyboard.presses == ["Control+a"]
    assert client.page.keyboard.typed == ["Describe @Ivy"]
    assert client.page.model_opener.clicks == 1
    assert client.page.model_option.clicks == 1
    assert client.page.create_button.clicks == 1
    assert result["entity_id"] == "entity-123"


@pytest.mark.asyncio
async def test_create_character_via_ui_clicks_arrow_forward_create_only():
    client = _client_with_split_create_buttons()

    result = await create_character_via_ui(
        client,
        project_id="d254e570-f789-4afd-a0df-457682534809",
        prompt="Describe character",
    )

    assert client.page.prompt_create_button.clicks == 0
    assert client.page.create_button.clicks == 1
    assert result["entity_id"] == "entity-123"


@pytest.mark.asyncio
async def test_create_character_via_ui_timeout_when_entity_call_not_observed(monkeypatch):
    client = _client()
    client.page.create_button.on_click = None

    with pytest.raises(CharacterCreateError, match="not confirmed"):
        await create_character_via_ui(
            client,
            project_id="d254e570-f789-4afd-a0df-457682534809",
            prompt="Describe character",
            timeout_sec=0.01,
        )


@pytest.mark.asyncio
async def test_create_character_via_ui_ignores_copy_entity_confirmation():
    client = _client()
    client.page.create_button.on_click = client.page._record_copy_entity_call

    with pytest.raises(CharacterCreateError, match="not confirmed"):
        await create_character_via_ui(
            client,
            project_id="d254e570-f789-4afd-a0df-457682534809",
            prompt="Describe character",
            timeout_sec=0.01,
        )


@pytest.mark.asyncio
async def test_create_character_via_ui_reports_missing_create_button():
    client = _client(create_button=False)

    with pytest.raises(CharacterCreateError, match="Create button not found"):
        await create_character_via_ui(
            client,
            project_id="d254e570-f789-4afd-a0df-457682534809",
            prompt="Describe character",
            timeout_sec=0.01,
        )


@pytest.mark.asyncio
async def test_generate_character_prompt_uses_captured_trpc_shape():
    client = _client()

    result = await generate_character_prompt(client, "THE_FAMILIAR")

    assert result == "preset prompt"
    client.page.context.request.post.assert_awaited_once_with(
        flow_characters.CHARACTER_PROMPT_ENDPOINT,
        data={"json": {"archetype": "THE_FAMILIAR"}},
        timeout=15_000,
    )


@pytest.mark.asyncio
async def test_text_to_video_blocks_unresolved_character_tag_before_submit(monkeypatch):
    type_prompt = AsyncMock()
    submit = AsyncMock()
    monkeypatch.setattr(generate, "_type_prompt", type_prompt)
    monkeypatch.setattr(generate, "submit_with_confirmation", submit)

    page = SimpleNamespace(goto=AsyncMock())
    client = SimpleNamespace(page=page, profile_name="profile-a")

    with pytest.raises(CharacterTagValidationError, match="@Missing"):
        await generate.text_to_video(client, "Use @Missing")

    page.goto.assert_not_awaited()
    type_prompt.assert_not_awaited()
    submit.assert_not_awaited()
