"""PR-1 — contract tests for the submit/download split on L2 ops.

Each of extend/camera/insert/remove now exposes three entry points:

  * ``submit_X``   — navigate → configure → submit, returns submit ctx.
                     Must NOT call ``finalize_operation``.
  * ``download_X`` — wait + download using the ctx. Must forward
                     ``project_id``/``locale`` from the ctx into
                     ``finalize_operation``.
  * ``run_X``      — back-compat wrapper: submit then download.

These tests mock every collaborator so the only thing under test is the
split's plumbing. They guard against:

  1. Silent regression back to a single-phase body (submit and download
     would couple again, breaking batch-mode dispatch).
  2. Wrong ctx plumbing (e.g. ``download_X`` reading a stale module-level
     project_id instead of ``submit_ctx["project_id"]``).
  3. Ctx-shape drift (``project_id`` / ``locale`` keys are the public
     contract consumed by the future ``ProjectSession`` dispatcher).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import camera as camera_mod
from flow.operations import extend as extend_mod
from flow.operations import insert as insert_mod
from flow.operations import remove as remove_mod


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Stub ``asyncio.sleep`` globally so the per-op small waits don't
    inflate runtime.  The ops import ``asyncio`` as a module and call
    ``asyncio.sleep(...)`` directly — patching the top-level attribute
    covers every callsite."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


def _mock_page():
    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/pid/edit/mid"

    def _loc(_selector):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=0)
        loc.first = MagicMock()
        loc.first.is_visible = AsyncMock(return_value=False)
        loc.first.is_enabled = AsyncMock(return_value=True)
        loc.first.click = AsyncMock()
        return loc

    page.locator = MagicMock(side_effect=_loc)
    page.get_by_text = MagicMock(
        return_value=MagicMock(
            first=MagicMock(
                is_visible=AsyncMock(return_value=True),
                click=AsyncMock(),
            )
        )
    )
    page.evaluate = AsyncMock(return_value=True)
    return page


def _mock_client(page):
    client = MagicMock()
    client.page = page
    client.clear_captures = MagicMock()
    return client


def _patch_l2_commons(monkeypatch, op_mod, *, submit_ok: bool = True):
    """Stub the helpers every L2 op invokes so submit_* is observable."""
    common = {
        "navigate_to_edit": AsyncMock(return_value=("edit_url", "pid-xyz", "en")),
        "wait_for_video_loaded": AsyncMock(),
        "click_action_button": AsyncMock(return_value=True),
        "count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=submit_ok),
        "finalize_operation": AsyncMock(
            return_value={"media_id": "new-mid", "output_files": ["out.mp4"]},
        ),
    }
    for name, mock in common.items():
        monkeypatch.setattr(op_mod, name, mock, raising=False)
    return common


# ---------------------------------------------------------------------------
# extend
# ---------------------------------------------------------------------------


def _patch_extend_specifics(monkeypatch):
    monkeypatch.setattr(extend_mod, "_verify_extend_panel", AsyncMock(return_value=True))
    monkeypatch.setattr(extend_mod, "_type_extend_prompt", AsyncMock())
    monkeypatch.setattr(extend_mod, "select_model", AsyncMock())


async def test_submit_extend_returns_ctx_without_calling_finalize(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, extend_mod)
    _patch_extend_specifics(monkeypatch)

    ctx = await extend_mod.submit_extend_video(
        client, {"media_id": "mid", "edit_url": "u"}, prompt="hi",
    )

    assert ctx == {"project_id": "pid-xyz", "locale": "en"}
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_not_awaited()


async def test_download_extend_forwards_ctx_to_finalize(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, extend_mod)

    result = await extend_mod.download_extend_video(
        client,
        {"media_id": "mid"},
        {"project_id": "pid-xyz", "locale": "en"},
    )

    assert result == {"media_id": "new-mid", "output_files": ["out.mp4"]}
    mocks["finalize_operation"].assert_awaited_once()
    _, kwargs = mocks["finalize_operation"].call_args
    assert kwargs["project_id"] == "pid-xyz"
    assert kwargs["locale"] == "en"
    assert kwargs["job_type"] == "extend-video"
    assert kwargs["download_prefix"] == "ext"


async def test_extend_video_wrapper_chains_submit_then_download(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, extend_mod)
    _patch_extend_specifics(monkeypatch)

    result = await extend_mod.extend_video(
        client, {"media_id": "mid", "edit_url": "u"}, prompt="hi",
    )

    assert result == {"media_id": "new-mid", "output_files": ["out.mp4"]}
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------


def _patch_camera_specifics(monkeypatch):
    monkeypatch.setattr(camera_mod, "_click_preset", AsyncMock(return_value=True))


async def test_submit_camera_returns_ctx_without_calling_finalize(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, camera_mod)
    _patch_camera_specifics(monkeypatch)

    ctx = await camera_mod.submit_camera_move(
        client, {"media_id": "mid"}, direction="Dolly in",
    )

    assert ctx == {"project_id": "pid-xyz", "locale": "en"}
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_not_awaited()


async def test_download_camera_forwards_ctx_to_finalize(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, camera_mod)

    await camera_mod.download_camera_move(
        client, {"media_id": "mid"}, {"project_id": "pid-xyz", "locale": "en"},
    )

    mocks["finalize_operation"].assert_awaited_once()
    _, kwargs = mocks["finalize_operation"].call_args
    assert kwargs["project_id"] == "pid-xyz"
    assert kwargs["locale"] == "en"
    assert kwargs["job_type"] == "camera-move"
    assert kwargs["download_prefix"] == "cam"


async def test_camera_move_wrapper_chains_submit_then_download(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, camera_mod)
    _patch_camera_specifics(monkeypatch)

    result = await camera_mod.camera_move(
        client, {"media_id": "mid"}, direction="Dolly in",
    )

    assert result == {"media_id": "new-mid", "output_files": ["out.mp4"]}
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


def _patch_insert_specifics(monkeypatch):
    monkeypatch.setattr(insert_mod, "draw_bbox_on_video", AsyncMock(return_value=True))
    monkeypatch.setattr(insert_mod, "_type_insert_prompt", AsyncMock())


async def test_submit_insert_returns_ctx_without_calling_finalize(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, insert_mod)
    _patch_insert_specifics(monkeypatch)

    ctx = await insert_mod.submit_insert_object(
        client, {"media_id": "mid"},
        prompt="a bird", bbox={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
    )

    assert ctx == {"project_id": "pid-xyz", "locale": "en"}
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_not_awaited()


async def test_download_insert_forwards_ctx_to_finalize(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, insert_mod)

    await insert_mod.download_insert_object(
        client, {"media_id": "mid"}, {"project_id": "pid-xyz", "locale": "en"},
    )

    mocks["finalize_operation"].assert_awaited_once()
    _, kwargs = mocks["finalize_operation"].call_args
    assert kwargs["project_id"] == "pid-xyz"
    assert kwargs["locale"] == "en"
    assert kwargs["job_type"] == "insert-object"
    assert kwargs["download_prefix"] == "ins"


async def test_insert_object_wrapper_chains_submit_then_download(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, insert_mod)
    _patch_insert_specifics(monkeypatch)

    result = await insert_mod.insert_object(
        client, {"media_id": "mid"}, prompt="a bird",
    )

    assert result == {"media_id": "new-mid", "output_files": ["out.mp4"]}
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def _patch_remove_specifics(monkeypatch):
    monkeypatch.setattr(remove_mod, "draw_bbox_on_video", AsyncMock(return_value=True))


async def test_submit_remove_returns_ctx_without_calling_finalize(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, remove_mod)
    _patch_remove_specifics(monkeypatch)

    ctx = await remove_mod.submit_remove_object(
        client, {"media_id": "mid"},
        bbox={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
    )

    assert ctx == {"project_id": "pid-xyz", "locale": "en"}
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_not_awaited()


async def test_download_remove_forwards_ctx_to_finalize(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, remove_mod)

    await remove_mod.download_remove_object(
        client, {"media_id": "mid"}, {"project_id": "pid-xyz", "locale": "en"},
    )

    mocks["finalize_operation"].assert_awaited_once()
    _, kwargs = mocks["finalize_operation"].call_args
    assert kwargs["project_id"] == "pid-xyz"
    assert kwargs["locale"] == "en"
    assert kwargs["job_type"] == "remove-object"
    assert kwargs["download_prefix"] == "rm"


async def test_remove_object_wrapper_chains_submit_then_download(monkeypatch):
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_l2_commons(monkeypatch, remove_mod)
    _patch_remove_specifics(monkeypatch)

    result = await remove_mod.remove_object(
        client, {"media_id": "mid"},
        bbox={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5},
    )

    assert result == {"media_id": "new-mid", "output_files": ["out.mp4"]}
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()


# ---------------------------------------------------------------------------
# Ctx contract — shape drift guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op_mod,submit_fn,patcher,submit_kwargs",
    [
        pytest.param(
            extend_mod, "submit_extend_video", _patch_extend_specifics,
            {"prompt": "hi"}, id="extend",
        ),
        pytest.param(
            camera_mod, "submit_camera_move", _patch_camera_specifics,
            {"direction": "Dolly in"}, id="camera",
        ),
        pytest.param(
            insert_mod, "submit_insert_object", _patch_insert_specifics,
            {"prompt": "a bird"}, id="insert",
        ),
        pytest.param(
            remove_mod, "submit_remove_object", _patch_remove_specifics,
            {"bbox": {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}}, id="remove",
        ),
    ],
)
async def test_submit_ctx_has_exactly_the_public_keys(
    op_mod, submit_fn, patcher, submit_kwargs, monkeypatch,
):
    """The submit ctx is the public hand-off contract between submit and
    download. Any new key must be deliberate — this guards against
    accidentally leaking internal state (e.g. a page reference) that
    would tie download to the in-memory session it was submitted in."""
    page = _mock_page()
    client = _mock_client(page)
    _patch_l2_commons(monkeypatch, op_mod)
    patcher(monkeypatch)

    ctx = await getattr(op_mod, submit_fn)(
        client, {"media_id": "mid"}, **submit_kwargs,
    )

    assert set(ctx.keys()) == {"project_id", "locale"}, (
        f"{submit_fn} ctx keys drifted: {sorted(ctx.keys())} — if adding a "
        f"new key is intentional, update this contract test too."
    )
