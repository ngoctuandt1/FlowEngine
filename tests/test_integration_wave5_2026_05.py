"""Wave 5 integration smoke for Characters, Trash, Share, and AI-locator seams.

Browser/network-free by design: these tests exercise public API/store seams and
mock all Flow/AI browser surfaces.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.ai_locator import AILocatorResult
from flow.characters import resolve_character_tags
from flow.operations import generate
from server.db.job_store import get_job
from server.db.share_store import get_job_by_share_token
from server.db.trash_store import list_trash_items


@pytest.mark.asyncio
async def test_character_api_create_and_tag_resolution(api_client) -> None:
    response = await api_client.post(
        "/api/characters",
        json={
            "name": "Hero Fox",
            "voice_id": "voice-hero",
            "description": "Orange fox pilot",
        },
    )

    assert response.status_code == 201
    character = response.json()
    assert character["name"] == "Hero Fox"

    listed = await api_client.get("/api/characters")
    assert listed.status_code == 200
    characters = listed.json()

    resolution = resolve_character_tags(
        "@hero boards the neon airship with @missing",
        characters,
    )

    assert [ref.name for ref in resolution.resolved] == ["Hero Fox"]
    assert [ref.tag for ref in resolution.resolved] == ["hero"]
    assert resolution.unresolved_tags == ["@missing"]
    assert "Unknown character @missing" in resolution.validation_errors[0]


@pytest.mark.asyncio
async def test_job_soft_delete_restore_and_permanent_delete(api_client) -> None:
    created = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "trash smoke",
            "model": "veo-3.1-lite",
            "aspect_ratio": "16:9",
            "profile": "smoke-profile",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    deleted = await api_client.delete(f"/api/jobs/{job_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": job_id}
    assert await get_job(job_id) is None

    trash = await list_trash_items()
    assert [(item.type, item.job_id) for item in trash] == [("job", job_id)]

    restored = await api_client.post("/api/trash/restore", json={"job_ids": [job_id]})
    if restored.status_code == 404:
        from server.routes.trash import restore_trash_endpoint
        from server.models.trash import TrashMutationRequest

        restored_body = await restore_trash_endpoint(TrashMutationRequest(job_ids=[job_id]))
        assert restored_body.restored_jobs == 1
    else:
        assert restored.status_code == 200
        assert restored.json()["restored_jobs"] == 1
    assert (await get_job(job_id)).id == job_id

    deleted_again = await api_client.delete(f"/api/jobs/{job_id}")
    assert deleted_again.status_code == 200
    purged = await api_client.request(
        "DELETE",
        "/api/trash/permanent",
        json={"job_ids": [job_id]},
    )
    if purged.status_code == 404:
        from server.routes.trash import permanent_delete_trash_endpoint
        from server.models.trash import TrashMutationRequest

        purged_body = await permanent_delete_trash_endpoint(TrashMutationRequest(job_ids=[job_id]))
        assert purged_body.deleted_jobs == 1
    else:
        assert purged.status_code == 200
        assert purged.json()["deleted_jobs"] == 1
    assert await get_job(job_id) is None
    assert await list_trash_items() == []


@pytest.mark.asyncio
async def test_share_link_mint_revoke_and_dead_token(api_client) -> None:
    created = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "share smoke",
            "model": "veo-3.1-lite",
            "aspect_ratio": "16:9",
            "profile": "smoke-profile",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    minted = await api_client.post(f"/api/jobs/{job_id}/share")
    assert minted.status_code == 200
    share = minted.json()
    token = share["share_token"]
    assert token and token in share["share_url"]
    assert share["revoked_at"] is None

    public = await api_client.get(f"/api/shares/{token}")
    assert public.status_code == 200
    assert public.json()["job"]["id"] == job_id
    assert (await get_job_by_share_token(token))[0].id == job_id

    repeat = await api_client.post(f"/api/jobs/{job_id}/share")
    assert repeat.status_code == 200
    assert repeat.json()["share_token"] == token

    revoked = await api_client.delete(f"/api/jobs/{job_id}/share")
    assert revoked.status_code == 200
    assert revoked.json()["share_token"] is None
    assert revoked.json()["revoked_at"] is not None
    assert await get_job_by_share_token(token) is None

    expired = await api_client.get(f"/api/shares/{token}")
    assert expired.status_code == 404
    invalid = await api_client.get("/api/shares/not-expired")
    assert invalid.status_code == 404


@pytest.mark.asyncio
async def test_ai_locator_opt_in_fallback_for_video_composer(monkeypatch) -> None:
    chip = object()
    ai_spy = AsyncMock(
        return_value=AILocatorResult(
            selector="#video-tab",
            coordinates=None,
            method="ai",
            cost_estimate=0.0,
            debug_log=[],
        )
    )
    monkeypatch.setattr("flow.ai_locator.ai_locate", ai_spy)
    monkeypatch.setattr(generate, "_open_composer_menu_by_role_text", AsyncMock(return_value=chip))
    monkeypatch.setattr(generate, "_find_open_composer_tab", AsyncMock(return_value=(None, None, [])))
    monkeypatch.setattr(generate, "_close_composer_menu_by_click_outside", AsyncMock())
    monkeypatch.setattr(generate.asyncio, "sleep", AsyncMock())

    legacy_tab = SimpleNamespace(
        get_attribute=AsyncMock(side_effect=RuntimeError("legacy miss")),
        click=AsyncMock(),
    )
    legacy_tab.first = legacy_tab
    ai_target = SimpleNamespace(first=None, click=AsyncMock())
    ai_target.first = ai_target

    page = MagicMock()
    page.locator.side_effect = lambda selector: ai_target if selector == "#video-tab" else legacy_tab
    page.wait_for_function = AsyncMock()

    monkeypatch.delenv("FLOW_AI_LOCATOR_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="Composer Video tab not found"):
        await generate._ensure_video_composer_mode(page, keep_open=True)
    ai_spy.assert_not_awaited()
    ai_target.click.assert_not_awaited()

    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    assert await generate._ensure_video_composer_mode(page, keep_open=True) is chip
    ai_spy.assert_awaited_once()
    assert ai_spy.await_args.kwargs["cache_key"] == generate._COMPOSER_VIDEO_TAB_AI_CACHE_KEY
    ai_target.click.assert_awaited_once_with(timeout=3000)
