import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from worker import dispatcher


@asynccontextmanager
async def _leased_client(client):
    yield client


@pytest.mark.parametrize(
    ("handler_name", "module_name", "operation_name", "job"),
    [
        (
            "handle_frames_to_video",
            "flow.operations.frames_to_video",
            "frames_to_video",
            {
                "id": "job-f2v-1",
                "type": "frames-to-video",
                "profile": "profile-a",
                "start_image_path": "uploads/start.png",
            },
        ),
        (
            "handle_text_to_image",
            "flow.operations.image",
            "text_to_image",
            {
                "id": "job-t2i-1",
                "type": "text-to-image",
                "profile": "profile-b",
            },
        ),
        (
            "handle_ingredients_to_video",
            "flow.operations.ingredients",
            "ingredients_to_video",
            {
                "id": "job-i2v-1",
                "type": "ingredients-to-video",
                "profile": "profile-c",
                "ingredient_image_paths": ["uploads/one.png"],
            },
        ),
    ],
)
async def test_l1_handlers_stamp_job_id_before_operation_call(
    monkeypatch,
    handler_name,
    module_name,
    operation_name,
    job,
):
    module = importlib.import_module(module_name)
    client = SimpleNamespace(profile_name=job["profile"])
    seen_job_ids: list[str | None] = []

    async def fake_operation(operation_client, **_kwargs):
        seen_job_ids.append(getattr(operation_client, "_job_id", None))
        return {"output_files": ["out.bin"], "media_id": "mid-1"}

    monkeypatch.setattr(dispatcher, "_client_lease", lambda profile: _leased_client(client))
    monkeypatch.setattr(module, operation_name, fake_operation)

    result = await getattr(dispatcher, handler_name)(job)

    assert seen_job_ids == [job["id"]]
    assert result["media_id"] == "mid-1"


def _touch(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"img")
    return str(path)


@pytest.mark.parametrize(
    ("module_name", "operation_name", "kwargs_factory"),
    [
        (
            "flow.operations.frames_to_video",
            "frames_to_video",
            lambda tmp_path: {"start_image_path": _touch(tmp_path, "start.png")},
        ),
        (
            "flow.operations.image",
            "text_to_image",
            lambda _tmp_path: {},
        ),
        (
            "flow.operations.ingredients",
            "ingredients_to_video",
            lambda tmp_path: {
                "ingredient_image_paths": [_touch(tmp_path, "ingredient.png")],
            },
        ),
    ],
)
async def test_l1_operations_pass_client_to_login_redirect(
    monkeypatch,
    tmp_path,
    module_name,
    operation_name,
    kwargs_factory,
):
    module = importlib.import_module(module_name)
    page = SimpleNamespace(
        url="https://accounts.google.com/signin/v2/identifier",
        goto=AsyncMock(),
    )
    client = SimpleNamespace(page=page, profile_name="profile-login")
    handle_login_redirect = AsyncMock(return_value=False)

    monkeypatch.setattr(module, "handle_login_redirect", handle_login_redirect)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())

    with pytest.raises(RuntimeError, match="Google login required - profile session expired."):
        await getattr(module, operation_name)(
            client,
            prompt="prompt",
            **kwargs_factory(tmp_path),
        )

    assert handle_login_redirect.await_args.kwargs["client"] is client
