"""Flow character automation helpers."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import logging
import re
from typing import Any, Iterable

from flow.navigation import flow_url


logger = logging.getLogger(__name__)

CHARACTER_PROMPT_ENDPOINT = (
    "https://labs.google/fx/api/trpc/flow.generateCharacterPrompt"
)
DEFAULT_CHARACTER_MODEL = "Nano Banana 2"
CHARACTER_EDITOR_SELECTOR = 'div[role="textbox"][contenteditable="true"]'
CHARACTER_TAG_ERROR_COPY = (
    "Unknown character {tag}; create a character first, then reference it with a simple @tag."
)

_TAG_RE = re.compile(r"(?<![\w.-])@([A-Za-z0-9][A-Za-z0-9_-]{0,63})")
_CREATE_ENTITY_URL_RE = re.compile(r"/v1/flow/entities(?:$|[?#:/])", re.IGNORECASE)


class CharacterTagValidationError(ValueError):
    """Raised when a prompt contains unresolved @tag mentions."""

    def __init__(self, unresolved_tags: list[str], errors: list[str]):
        self.unresolved_tags = unresolved_tags
        self.errors = errors
        super().__init__("; ".join(errors))


class CharacterCreateError(RuntimeError):
    """Raised when Flow character UI creation cannot be completed."""


@dataclass(frozen=True)
class CharacterReference:
    """Resolved character reference from an @tag mention."""

    id: str
    name: str
    tag: str
    project_id: str | None = None
    ref_image_url: str | None = None
    voice_id: str | None = None


@dataclass(frozen=True)
class CharacterPromptResolution:
    """Prompt plus resolved and unresolved character mentions."""

    prompt: str
    resolved: list[CharacterReference]
    unresolved_tags: list[str]
    validation_errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.unresolved_tags


def character_project_url(project_id: str, locale: str = "") -> str:
    """Return Flow's character route for a project."""

    clean_project_id = str(project_id or "").strip()
    if not clean_project_id:
        raise ValueError("project_id is required")
    return f"{flow_url(locale)}/project/{clean_project_id}/characters"


def extract_character_tags(prompt: str) -> list[str]:
    """Return unique @tag mentions in first-seen order, without @ prefix."""

    tags: list[str] = []
    seen: set[str] = set()
    for match in _TAG_RE.finditer(prompt or ""):
        raw = match.group(1)
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(raw)
    return tags


def tag_key(value: str) -> str:
    """Normalize a character name/tag for @tag matching."""

    return re.sub(r"[^a-z0-9_-]+", "", str(value or "").strip().lower())


def _character_value(character: Any, key: str) -> Any:
    if isinstance(character, dict):
        return character.get(key)
    return getattr(character, key, None)


def _candidate_keys(character: Any) -> set[str]:
    keys: set[str] = set()
    for field in ("tag", "name", "id"):
        value = _character_value(character, field)
        if value:
            keys.add(tag_key(str(value)))
    name = _character_value(character, "name")
    if name:
        first_token = re.split(r"\s+", str(name).strip(), maxsplit=1)[0]
        slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
        compact = re.sub(r"[^a-z0-9]+", "", str(name).strip().lower())
        if first_token:
            keys.add(tag_key(first_token))
        if slug:
            keys.add(slug)
        if compact:
            keys.add(compact)
    return {key for key in keys if key}


def _to_reference(character: Any, requested_tag: str) -> CharacterReference:
    return CharacterReference(
        id=str(_character_value(character, "id") or ""),
        name=str(_character_value(character, "name") or requested_tag),
        tag=requested_tag,
        project_id=_character_value(character, "project_id"),
        ref_image_url=_character_value(character, "ref_image_url"),
        voice_id=_character_value(character, "voice_id"),
    )


def resolve_character_tags(
    prompt: str,
    characters: Iterable[Any] | None,
) -> CharacterPromptResolution:
    """Resolve @tag mentions to known characters and preserve unresolved tags."""

    known: dict[str, Any] = {}
    for character in characters or []:
        for key in _candidate_keys(character):
            known.setdefault(key, character)

    resolved: list[CharacterReference] = []
    unresolved: list[str] = []
    errors: list[str] = []
    for tag in extract_character_tags(prompt):
        character = known.get(tag_key(tag))
        if character is None:
            visible_tag = f"@{tag}"
            unresolved.append(visible_tag)
            errors.append(CHARACTER_TAG_ERROR_COPY.format(tag=visible_tag))
            continue
        resolved.append(_to_reference(character, tag))

    return CharacterPromptResolution(
        prompt=prompt,
        resolved=resolved,
        unresolved_tags=unresolved,
        validation_errors=errors,
    )


def validate_character_tags(prompt: str, characters: Iterable[Any] | None) -> CharacterPromptResolution:
    """Resolve @tag mentions or raise validation error before submit."""

    resolution = resolve_character_tags(prompt, characters)
    if not resolution.ok:
        raise CharacterTagValidationError(
            resolution.unresolved_tags,
            resolution.validation_errors,
        )
    return resolution


async def generate_character_prompt(client: Any, archetype: str) -> str:
    """Call captured safe preset prompt tRPC endpoint."""

    clean_archetype = str(archetype or "").strip().upper()
    if not clean_archetype:
        raise ValueError("archetype is required")
    request = getattr(getattr(client.page, "context", None), "request", None)
    if request is None or not hasattr(request, "post"):
        raise RuntimeError("Flow page request context is unavailable")

    response = await request.post(
        CHARACTER_PROMPT_ENDPOINT,
        data={"json": {"archetype": clean_archetype}},
        timeout=15_000,
    )
    status = getattr(response, "status", getattr(response, "status_code", 0))
    if status >= 400:
        raise RuntimeError(f"generateCharacterPrompt failed with HTTP {status}")
    data = await response.json()
    return _extract_prompt_text(data)


def _extract_prompt_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""

    candidates = [
        data.get("prompt"),
        data.get("text"),
        data.get("result", {}).get("data", {}).get("json", {}).get("prompt"),
        data.get("result", {}).get("data", {}).get("json", {}).get("text"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


async def open_character_creator(page: Any, project_id: str, *, locale: str = "") -> str:
    """Navigate to Flow character route and click New character."""

    url = character_project_url(project_id, locale)
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await _click_new_character(page)
    return url


async def fill_character_prompt(page: Any, prompt: str) -> None:
    """Fill Flow character contenteditable prompt editor."""

    editor = page.locator(CHARACTER_EDITOR_SELECTOR).first
    await editor.wait_for(state="visible", timeout=10_000)
    await editor.click(timeout=5_000)
    await page.keyboard.press("Control+a")
    await page.keyboard.type(prompt, delay=10)


async def select_character_model(page: Any, model: str = DEFAULT_CHARACTER_MODEL) -> None:
    """Select character model through UI, without reverse-API mutation."""

    model_text = str(model or DEFAULT_CHARACTER_MODEL).strip()
    buttons = [
        page.get_by_role("button", name=re.compile(re.escape(model_text), re.IGNORECASE)),
        page.locator("button").filter(has_text=re.compile(re.escape(model_text), re.IGNORECASE)),
    ]
    opener = None
    for candidate in buttons:
        locator = candidate.first
        try:
            if await locator.is_visible(timeout=1_500):
                opener = locator
                break
        except Exception:
            continue

    if opener is None:
        raise CharacterCreateError(f"Character model picker not found: {model_text}")

    await opener.click(timeout=5_000)

    options = [
        page.get_by_role("menuitem", name=re.compile(re.escape(model_text), re.IGNORECASE)),
        page.get_by_role("option", name=re.compile(re.escape(model_text), re.IGNORECASE)),
        page.locator("[role='menuitem'], [role='option'], button").filter(
            has_text=re.compile(re.escape(model_text), re.IGNORECASE)
        ),
    ]
    for candidate in options:
        locator = candidate.first
        try:
            if await locator.is_visible(timeout=1_000):
                await locator.click(timeout=5_000)
                return
        except Exception:
            continue

    logger.info("Character model picker already showed %s; continuing", model_text)


async def submit_character_create(client: Any, *, timeout_sec: float = 15.0) -> str | None:
    """Click Flow Create button and confirm entity-create network call if observed."""

    page = client.page
    calls_before = len(getattr(client, "_calls", []))
    clicked = await _click_create(page)
    if not clicked:
        raise CharacterCreateError("Character Create button not found")

    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        entity_call = _latest_entity_call(getattr(client, "_calls", [])[calls_before:])
        if entity_call is not None:
            return _extract_entity_id(entity_call.get("body"))
        await asyncio.sleep(0.25)

    raise CharacterCreateError("Character Create submit not confirmed before timeout")


async def create_character_via_ui(
    client: Any,
    *,
    project_id: str,
    prompt: str,
    known_characters: Iterable[Any] | None = None,
    model: str = DEFAULT_CHARACTER_MODEL,
    locale: str = "",
    timeout_sec: float = 15.0,
) -> dict[str, Any]:
    """Create a Flow character via UI-only fallback path."""

    resolution = validate_character_tags(prompt, known_characters)
    page = client.page
    target_url = await open_character_creator(page, project_id, locale=locale)
    await fill_character_prompt(page, prompt)
    await select_character_model(page, model)
    entity_id = await submit_character_create(client, timeout_sec=timeout_sec)
    return {
        "project_id": project_id,
        "character_url": target_url,
        "entity_id": entity_id,
        "resolved_characters": [reference.__dict__ for reference in resolution.resolved],
    }


async def _click_new_character(page: Any) -> None:
    candidates = [
        page.get_by_role("button", name=re.compile(r"New\s+character", re.IGNORECASE)),
        page.locator("button").filter(has_text=re.compile(r"New\s+character", re.IGNORECASE)),
    ]
    for candidate in candidates:
        locator = candidate.first
        try:
            await locator.wait_for(state="visible", timeout=10_000)
            await locator.click(timeout=5_000)
            return
        except Exception:
            continue
    raise CharacterCreateError("New character button not found")


async def _click_create(page: Any) -> bool:
    candidates = [
        page.locator("button:has(i:text-is('arrow_forward'))").filter(
            has_text=re.compile(r"Create", re.IGNORECASE)
        ),
        page.get_by_role("button", name=re.compile(r"Create", re.IGNORECASE)),
        page.locator("button[type='button']").filter(
            has_text=re.compile(r"Create", re.IGNORECASE)
        ),
    ]
    for candidate in candidates:
        count = await candidate.count()
        for index in range(count):
            button = candidate.nth(index)
            try:
                if not await button.is_visible(timeout=500):
                    continue
                if not await button.is_enabled(timeout=300):
                    continue
                await button.click(timeout=5_000, force=True)
                return True
            except Exception:
                continue

    return False


def _latest_entity_call(calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    for call in reversed(calls):
        url = str(call.get("url") or "")
        if _CREATE_ENTITY_URL_RE.search(url):
            return call
    return None


def _extract_entity_id(body: Any) -> str | None:
    if isinstance(body, str):
        return body if body.strip() else None
    if not isinstance(body, dict):
        return None
    for key in ("entityId", "entity_id", "id", "name"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for container in ("entity", "result", "data"):
        nested = body.get(container)
        if isinstance(nested, dict):
            found = _extract_entity_id(nested)
            if found:
                return found
    return None
