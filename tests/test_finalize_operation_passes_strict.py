from types import SimpleNamespace
from unittest.mock import AsyncMock

from flow.operations import _base


PARENT_SLUG = "a" * 32
NEW_SLUG = "b" * 32
PROJECT_ID = "d" * 32
PROJECT_URL = f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}"


def _edit(slug: str) -> str:
    return f"{PROJECT_URL}/edit/{slug}"


def _client():
    return SimpleNamespace(
        page=SimpleNamespace(url=_edit(PARENT_SLUG)),
        _gen_id="gen-1",
        profile_name="profile-a",
    )


async def test_finalize_operation_passes_strict_true_when_parent_media_id_set(monkeypatch):
    client = _client()
    resolve_final_media_id = AsyncMock(return_value=NEW_SLUG)
    download_video = AsyncMock(return_value=["out.mp4"])
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": [PARENT_SLUG]}),
    )
    monkeypatch.setattr(_base, "resolve_final_media_id", resolve_final_media_id)
    monkeypatch.setattr(_base, "download_video", download_video)

    result = await _base.finalize_operation(
        client,
        {"media_id": PARENT_SLUG, "project_url": PROJECT_URL},
        "extend-video",
        PROJECT_ID,
        "",
        "ext",
    )

    resolve_final_media_id.assert_awaited_once_with(
        client.page,
        fallback=PARENT_SLUG,
        parent_media_id=PARENT_SLUG,
        download_media_ids=[PARENT_SLUG],
        strict=True,
    )
    download_video.assert_awaited_once_with(
        client,
        media_ids=[PARENT_SLUG],
        prefix="ext",
    )
    assert result == {
        "project_url": PROJECT_URL,
        "media_id": NEW_SLUG,
        "edit_url": _edit(NEW_SLUG),
        "output_files": ["out.mp4"],
        "generation_id": "gen-1",
        "profile": "profile-a",
    }


async def test_finalize_operation_passes_strict_false_when_parent_media_id_missing(monkeypatch):
    client = _client()
    resolve_final_media_id = AsyncMock(return_value=NEW_SLUG)
    download_video = AsyncMock(return_value=["out.mp4"])
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": [NEW_SLUG]}),
    )
    monkeypatch.setattr(_base, "resolve_final_media_id", resolve_final_media_id)
    monkeypatch.setattr(_base, "download_video", download_video)

    result = await _base.finalize_operation(
        client,
        {"media_id": None},
        "text-to-video",
        PROJECT_ID,
        "",
        "gen",
    )

    resolve_final_media_id.assert_awaited_once_with(
        client.page,
        fallback=None,
        parent_media_id=None,
        download_media_ids=[NEW_SLUG],
        strict=False,
    )
    download_video.assert_awaited_once_with(
        client,
        media_ids=[NEW_SLUG],
        prefix="gen",
    )
    assert result == {
        "project_url": PROJECT_URL,
        "media_id": NEW_SLUG,
        "edit_url": _edit(NEW_SLUG),
        "output_files": ["out.mp4"],
        "generation_id": "gen-1",
        "profile": "profile-a",
    }


async def test_finalize_operation_uses_resolved_media_id_for_download_without_media_events(
    monkeypatch,
):
    client = _client()
    resolve_final_media_id = AsyncMock(return_value=NEW_SLUG)
    download_video = AsyncMock(return_value=["out.mp4"])
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": []}),
    )
    monkeypatch.setattr(_base, "resolve_final_media_id", resolve_final_media_id)
    monkeypatch.setattr(_base, "download_video", download_video)

    result = await _base.finalize_operation(
        client,
        {"media_id": None},
        "text-to-video",
        PROJECT_ID,
        "",
        "gen",
    )

    resolve_final_media_id.assert_awaited_once_with(
        client.page,
        fallback=None,
        parent_media_id=None,
        download_media_ids=[],
        strict=False,
    )
    download_video.assert_awaited_once_with(
        client,
        media_ids=[NEW_SLUG],
        prefix="gen",
    )
    assert result["output_files"] == ["out.mp4"]


async def test_finalize_operation_builds_project_url_when_job_missing_one(monkeypatch):
    client = _client()
    resolve_final_media_id = AsyncMock(return_value=NEW_SLUG)
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": [NEW_SLUG]}),
    )
    monkeypatch.setattr(_base, "resolve_final_media_id", resolve_final_media_id)
    monkeypatch.setattr(_base, "download_video", AsyncMock(return_value=["out.mp4"]))

    result = await _base.finalize_operation(
        client,
        {},
        "text-to-video",
        PROJECT_ID,
        "",
        "gen",
    )

    assert resolve_final_media_id.await_args.kwargs["strict"] is False
    assert result["project_url"] == PROJECT_URL
    assert result["edit_url"] == _edit(NEW_SLUG)
