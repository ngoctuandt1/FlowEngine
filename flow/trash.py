"""Google Flow trash UI automation helpers."""

from __future__ import annotations

import re
from urllib.parse import quote

from flow.navigation import FLOW_BASE, extract_project_id


STATIC_TRASH_ENDPOINT_HINTS: tuple[str, ...] = (
    "https://aisandbox-pa.googleapis.com/v1/flow:batchDeleteAssets",
    "https://aisandbox-pa.googleapis.com/v1/flowMedia/{media_id}",
    "https://aisandbox-pa.googleapis.com/v1/flowWorkflows/{workflow_id}",
)

TRASH_HEADER_RE = re.compile(r"^\s*Trash\s*$", re.IGNORECASE)


class TrashActionError(RuntimeError):
    """Raised when Flow trash UI cannot satisfy requested safe action."""


def build_trash_url(project_id_or_url: str, *, locale: str = "") -> str:
    """Build `/fx/tools/flow/project/{project_id}/trash` URL."""

    project_id = _project_id(project_id_or_url)
    locale_part = f"/{locale.strip('/')}" if locale else ""
    return f"{FLOW_BASE}{locale_part}/tools/flow/project/{quote(project_id, safe='')}/trash"


async def open_trash(page, project_id_or_url: str, *, locale: str = "") -> str:
    """Open trash route and verify Trash page loaded."""

    target_url = build_trash_url(project_id_or_url, locale=locale)
    try:
        await page.goto(target_url, wait_until="domcontentloaded")
    except TypeError:
        await page.goto(target_url)
    await verify_trash_page(page)
    return target_url


async def restore_all(page, project_id_or_url: str, *, confirm: bool = False) -> str:
    """Click Restore All only when caller explicitly confirms intent."""

    if not confirm:
        raise TrashActionError("Restore All requires confirm=True")
    target_url = await open_trash(page, project_id_or_url)
    await _click_button(page, "Restore All")
    return target_url


async def delete_all(page, project_id_or_url: str, *, confirm: bool = False) -> str:
    """Click Delete All only when caller explicitly confirms intent."""

    if not confirm:
        raise TrashActionError("Delete All requires confirm=True")
    target_url = await open_trash(page, project_id_or_url)
    await _click_button(page, "Delete All")
    return target_url


async def verify_trash_page(page, *, timeout_ms: int = 10_000) -> None:
    """Verify visible Trash header/label before any mutation click."""

    errors: list[Exception] = []
    for locator_factory in (
        lambda: page.get_by_role("heading", name=TRASH_HEADER_RE).first,
        lambda: page.get_by_text("Trash", exact=True).first,
    ):
        try:
            locator = locator_factory()
            await locator.wait_for(state="visible", timeout=timeout_ms)
            return
        except Exception as exc:
            errors.append(exc)

    try:
        visible_text = await page.locator("body").inner_text(timeout=timeout_ms)
    except Exception as exc:
        errors.append(exc)
        visible_text = ""

    if "Trash" in visible_text:
        return
    raise TrashActionError("Trash page header not visible") from (errors[-1] if errors else None)


async def _click_button(page, label: str, *, timeout_ms: int = 5_000) -> None:
    button_re = re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE)
    errors: list[Exception] = []
    for locator_factory in (
        lambda: page.get_by_role("button", name=button_re).first,
        lambda: page.get_by_text(label, exact=True).first,
    ):
        try:
            await locator_factory().click(timeout=timeout_ms)
            return
        except Exception as exc:
            errors.append(exc)
    raise TrashActionError(f"{label} button not clickable") from (errors[-1] if errors else None)


def _project_id(project_id_or_url: str) -> str:
    raw = str(project_id_or_url or "").strip()
    project_id = extract_project_id(raw) or raw.rstrip("/").split("/")[-1]
    if not project_id:
        raise TrashActionError("project_id is required")
    return project_id
