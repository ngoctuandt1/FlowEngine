"""Unit tests for ``flow.operations.extend._finalize_replay_result``.

These tests pin the contract between the reverse-API extend replay and
Flow's status / direct-download endpoints (the path that lets L3+ extend
chains finalize without touching the SPA at all). Every browser-time
side effect is mocked; only the helper's branching + result-shape is
under test.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.operations.extend as extend_mod


def _client(tmp_path):
    """Build a fake FlowClient that satisfies the helper's contract."""
    client = MagicMock()
    client.page = MagicMock()
    client.page.url = "https://labs.google/fx/tools/flow/project/p/edit/parent-mid"
    client.profile_name = "profile-x"
    client._gen_id = "gen-x"
    client._media_id_events = []
    client._record_media_id = lambda mid, source="", url="": client._media_id_events.append(
        {"mid": mid, "source": source, "url": url}
    )
    client.download_dir = str(tmp_path)
    return client


async def test_finalize_polls_status_downloads_url_and_builds_result(
    monkeypatch, tmp_path
):
    client = _client(tmp_path)
    saved = str(tmp_path / "out.mp4")
    poll = AsyncMock(
        return_value={
            "child-mid": {
                "status": "completed",
                "media_id": "child-mid",
                "media_url": "https://storage.googleapis.com/clip.mp4",
            }
        }
    )
    download = AsyncMock(return_value=saved)
    monkeypatch.setattr(extend_mod, "poll_status_via_api", poll)
    monkeypatch.setattr(extend_mod, "download_via_url", download)

    result = await extend_mod._finalize_replay_result(
        client,
        {"media_id": "parent-mid", "project_url": "https://flow/project/p"},
        project_id="proj-id",
        locale="en",
        replay_media_id="child-mid",
    )

    # poll_status_via_api receives the new media id as the only gen to wait on.
    poll.assert_awaited_once()
    poll_call = poll.await_args
    assert poll_call.args[0] is client
    assert poll_call.kwargs.get("gen_ids") == ["child-mid"]
    assert poll_call.kwargs.get("project_id") == "proj-id"

    # download_via_url receives the URL extracted from the status response
    # and writes under the client's configured download_dir.
    download.assert_awaited_once()
    dl_call = download.await_args
    assert dl_call.args[0] is client
    assert dl_call.kwargs.get("url") == "https://storage.googleapis.com/clip.mp4"
    out_path = dl_call.kwargs.get("out_path")
    assert isinstance(out_path, str)
    assert out_path.endswith(".mp4")
    assert str(tmp_path) in out_path

    # Result shape mirrors finalize_operation: project_url honored from job,
    # edit_url built from project_id + new media id, output_files holds the
    # downloaded path, generation_id and profile pulled from the client.
    assert result == {
        "project_url": "https://flow/project/p",
        "media_id": "child-mid",
        "edit_url": "https://labs.google/fx/en/tools/flow/project/proj-id/edit/child-mid",
        "output_files": [saved],
        "generation_id": "gen-x",
        "profile": "profile-x",
    }

    # Media id was recorded onto the client's network-event ledger so
    # downstream code (`finalize_operation`'s resolve_final_media_id, the
    # job-update PATCH) sees the replay output as a first-class event.
    assert {event["mid"] for event in client._media_id_events} == {"child-mid"}
    assert {event["source"] for event in client._media_id_events} == {"extend_replay"}


async def test_finalize_builds_project_url_when_job_missing_it(monkeypatch, tmp_path):
    """When ``job.project_url`` is empty the helper must reconstruct it
    from ``project_id`` + ``locale`` so the result still carries a usable
    URL for downstream chain tracking."""
    client = _client(tmp_path)
    poll = AsyncMock(
        return_value={
            "child-mid": {
                "status": "completed",
                "media_id": "child-mid",
                "media_url": "https://flow/clip.mp4",
            }
        }
    )
    download = AsyncMock(return_value=str(tmp_path / "x.mp4"))
    monkeypatch.setattr(extend_mod, "poll_status_via_api", poll)
    monkeypatch.setattr(extend_mod, "download_via_url", download)

    result = await extend_mod._finalize_replay_result(
        client,
        {"media_id": "parent-mid"},  # no project_url
        project_id="proj-id",
        locale="",  # no locale prefix
        replay_media_id="child-mid",
    )

    assert result["project_url"] == "https://labs.google/fx/tools/flow/project/proj-id"
    assert (
        result["edit_url"]
        == "https://labs.google/fx/tools/flow/project/proj-id/edit/child-mid"
    )


async def test_finalize_raises_when_status_slot_missing(monkeypatch, tmp_path):
    """If the status response carries no entry for our gen id the helper
    raises so the caller falls back to the UI submit path."""
    client = _client(tmp_path)
    monkeypatch.setattr(extend_mod, "poll_status_via_api", AsyncMock(return_value={}))
    download = AsyncMock()
    monkeypatch.setattr(extend_mod, "download_via_url", download)

    with pytest.raises(RuntimeError, match="extend-video replay"):
        await extend_mod._finalize_replay_result(
            client,
            {"media_id": "parent-mid"},
            project_id="proj-id",
            locale="en",
            replay_media_id="child-mid",
        )
    download.assert_not_awaited()


async def test_finalize_raises_when_status_failed(monkeypatch, tmp_path):
    client = _client(tmp_path)
    monkeypatch.setattr(
        extend_mod,
        "poll_status_via_api",
        AsyncMock(
            return_value={
                "child-mid": {
                    "status": "failed",
                    "media_id": "child-mid",
                    "media_url": None,
                    "error": "backend_failure",
                }
            }
        ),
    )
    download = AsyncMock()
    monkeypatch.setattr(extend_mod, "download_via_url", download)

    with pytest.raises(RuntimeError, match="reports failed"):
        await extend_mod._finalize_replay_result(
            client,
            {"media_id": "parent-mid"},
            project_id="proj-id",
            locale="en",
            replay_media_id="child-mid",
        )
    download.assert_not_awaited()


async def test_finalize_raises_when_status_timed_out(monkeypatch, tmp_path):
    """``poll_status_via_api`` sets ``status=timeout`` when its hard
    deadline elapses without a terminal state; the helper must surface
    that as a RuntimeError instead of attempting a download."""
    client = _client(tmp_path)
    monkeypatch.setattr(
        extend_mod,
        "poll_status_via_api",
        AsyncMock(
            return_value={
                "child-mid": {
                    "status": "timeout",
                    "media_id": None,
                    "media_url": None,
                }
            }
        ),
    )
    download = AsyncMock()
    monkeypatch.setattr(extend_mod, "download_via_url", download)

    with pytest.raises(RuntimeError, match="did not reach completed"):
        await extend_mod._finalize_replay_result(
            client,
            {"media_id": "parent-mid"},
            project_id="proj-id",
            locale="en",
            replay_media_id="child-mid",
        )
    download.assert_not_awaited()


async def test_finalize_raises_when_completed_but_no_media_url(monkeypatch, tmp_path):
    client = _client(tmp_path)
    monkeypatch.setattr(
        extend_mod,
        "poll_status_via_api",
        AsyncMock(
            return_value={
                "child-mid": {
                    "status": "completed",
                    "media_id": "child-mid",
                    "media_url": None,
                }
            }
        ),
    )
    download = AsyncMock()
    monkeypatch.setattr(extend_mod, "download_via_url", download)

    with pytest.raises(RuntimeError, match="no media URL"):
        await extend_mod._finalize_replay_result(
            client,
            {"media_id": "parent-mid"},
            project_id="proj-id",
            locale="en",
            replay_media_id="child-mid",
        )
    download.assert_not_awaited()


async def test_finalize_raises_when_download_returns_empty(monkeypatch, tmp_path):
    client = _client(tmp_path)
    monkeypatch.setattr(
        extend_mod,
        "poll_status_via_api",
        AsyncMock(
            return_value={
                "child-mid": {
                    "status": "completed",
                    "media_id": "child-mid",
                    "media_url": "https://flow/clip.mp4",
                }
            }
        ),
    )
    monkeypatch.setattr(extend_mod, "download_via_url", AsyncMock(return_value=None))

    with pytest.raises(RuntimeError, match="empty path"):
        await extend_mod._finalize_replay_result(
            client,
            {"media_id": "parent-mid"},
            project_id="proj-id",
            locale="en",
            replay_media_id="child-mid",
        )


async def test_finalize_passes_project_id_none_when_missing(monkeypatch, tmp_path):
    """The helper should still poll Flow's status API even when no
    ``project_id`` is known; it forwards ``None`` so the poll helper's
    fallback path takes over template harvesting."""
    client = _client(tmp_path)
    poll = AsyncMock(
        return_value={
            "child-mid": {
                "status": "completed",
                "media_id": "child-mid",
                "media_url": "https://flow/clip.mp4",
            }
        }
    )
    download = AsyncMock(return_value=str(tmp_path / "z.mp4"))
    monkeypatch.setattr(extend_mod, "poll_status_via_api", poll)
    monkeypatch.setattr(extend_mod, "download_via_url", download)

    await extend_mod._finalize_replay_result(
        client,
        {"media_id": "parent-mid"},
        project_id="",
        locale="en",
        replay_media_id="child-mid",
    )

    poll.assert_awaited_once()
    assert poll.await_args.kwargs.get("project_id") is None


async def test_finalize_download_prefix_applied_to_filename(monkeypatch, tmp_path):
    """The ``download_prefix`` argument must reach the destination
    filename so different op variants keep distinct download artifacts."""
    client = _client(tmp_path)
    monkeypatch.setattr(
        extend_mod,
        "poll_status_via_api",
        AsyncMock(
            return_value={
                "child-mid": {
                    "status": "completed",
                    "media_id": "child-mid",
                    "media_url": "https://flow/clip.mp4",
                }
            }
        ),
    )
    download = AsyncMock(return_value=str(tmp_path / "z.mp4"))
    monkeypatch.setattr(extend_mod, "download_via_url", download)

    await extend_mod._finalize_replay_result(
        client,
        {"media_id": "parent-mid"},
        project_id="proj-id",
        locale="en",
        replay_media_id="child-mid",
        download_prefix="cam",
    )

    out_path = download.await_args.kwargs.get("out_path")
    assert isinstance(out_path, str)
    # cam_replay_<mid-prefix>_<ts>.mp4
    filename = out_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    assert filename.startswith("cam_replay_")
    assert filename.endswith(".mp4")
