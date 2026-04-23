from unittest.mock import AsyncMock

import pytest

from worker import project_session as session_mod


def _job(job_id: str, job_type: str, **extra) -> dict:
    job = {
        "id": job_id,
        "type": job_type,
        "project_url": "https://flow/project/1",
    }
    job.update(extra)
    return job


async def test_submit_many_calls_the_right_submit_per_job_type(monkeypatch):
    client = object()
    submit_extend = AsyncMock(return_value={"project_id": "p1", "locale": "en"})
    submit_insert = AsyncMock(return_value={"project_id": "p2", "locale": "en"})
    submit_remove = AsyncMock(return_value={"project_id": "p3", "locale": "en"})
    submit_camera = AsyncMock(return_value={"project_id": "p4", "locale": "en"})
    sleep_mock = AsyncMock()

    monkeypatch.setitem(session_mod.SUBMIT_MAP, "extend-video", submit_extend)
    monkeypatch.setitem(session_mod.SUBMIT_MAP, "insert-object", submit_insert)
    monkeypatch.setitem(session_mod.SUBMIT_MAP, "remove-object", submit_remove)
    monkeypatch.setitem(session_mod.SUBMIT_MAP, "camera-move", submit_camera)
    monkeypatch.setattr(session_mod.asyncio, "sleep", sleep_mock)

    session = session_mod.ProjectSession("profile-a", "https://flow/project/1")
    session.client = client
    jobs = [
        _job("j1", "extend-video", prompt="extend me", model="m1"),
        _job("j2", "insert-object", prompt="insert me", bbox={"x": 0, "y": 0, "w": 1, "h": 1}),
        _job("j3", "remove-object", bbox={"x": 0, "y": 0, "w": 1, "h": 1}),
        _job("j4", "camera-move", direction="Orbit left"),
    ]

    submitted = await session.submit_many(jobs)

    assert submitted == [
        (jobs[0], {"project_id": "p1", "locale": "en"}),
        (jobs[1], {"project_id": "p2", "locale": "en"}),
        (jobs[2], {"project_id": "p3", "locale": "en"}),
        (jobs[3], {"project_id": "p4", "locale": "en"}),
    ]
    submit_extend.assert_awaited_once_with(
        client, jobs[0], prompt="extend me", model="m1", free_mode=True
    )
    submit_insert.assert_awaited_once_with(
        client, jobs[1], prompt="insert me", bbox=jobs[1]["bbox"]
    )
    submit_remove.assert_awaited_once_with(client, jobs[2], bbox=jobs[2]["bbox"])
    submit_camera.assert_awaited_once_with(
        client, jobs[3], direction="Orbit left"
    )


async def test_submit_many_includes_three_second_spacing(monkeypatch):
    submit_mock = AsyncMock(return_value={"project_id": "p1", "locale": "en"})
    sleep_mock = AsyncMock()

    monkeypatch.setitem(session_mod.SUBMIT_MAP, "camera-move", submit_mock)
    monkeypatch.setattr(session_mod.asyncio, "sleep", sleep_mock)

    session = session_mod.ProjectSession("profile-a", "https://flow/project/1")
    session.client = object()
    jobs = [_job("j1", "camera-move"), _job("j2", "camera-move"), _job("j3", "camera-move")]

    await session.submit_many(jobs)

    assert sleep_mock.await_count == 2
    assert [call.args for call in sleep_mock.await_args_list] == [(3,), (3,)]


async def test_download_all_calls_the_right_download_per_ctx(monkeypatch):
    client = object()
    download_extend = AsyncMock(return_value={"media_id": "m1"})
    download_insert = AsyncMock(return_value={"media_id": "m2"})
    download_remove = AsyncMock(return_value={"media_id": "m3"})
    download_camera = AsyncMock(return_value={"media_id": "m4"})

    monkeypatch.setitem(session_mod.DOWNLOAD_MAP, "extend-video", download_extend)
    monkeypatch.setitem(session_mod.DOWNLOAD_MAP, "insert-object", download_insert)
    monkeypatch.setitem(session_mod.DOWNLOAD_MAP, "remove-object", download_remove)
    monkeypatch.setitem(session_mod.DOWNLOAD_MAP, "camera-move", download_camera)

    session = session_mod.ProjectSession("profile-a", "https://flow/project/1")
    session.client = client
    pairs = [
        (_job("j1", "extend-video"), {"project_id": "p1", "locale": "en"}),
        (_job("j2", "insert-object"), {"project_id": "p2", "locale": "en"}),
        (_job("j3", "remove-object"), {"project_id": "p3", "locale": "en"}),
        (_job("j4", "camera-move"), {"project_id": "p4", "locale": "en"}),
    ]

    results = await session.download_all(pairs)

    assert results == [
        (pairs[0][0], {"media_id": "m1"}),
        (pairs[1][0], {"media_id": "m2"}),
        (pairs[2][0], {"media_id": "m3"}),
        (pairs[3][0], {"media_id": "m4"}),
    ]
    download_extend.assert_awaited_once_with(client, pairs[0][0], pairs[0][1])
    download_insert.assert_awaited_once_with(client, pairs[1][0], pairs[1][1])
    download_remove.assert_awaited_once_with(client, pairs[2][0], pairs[2][1])
    download_camera.assert_awaited_once_with(client, pairs[3][0], pairs[3][1])


async def test_unknown_job_type_raises_value_error():
    session = session_mod.ProjectSession("profile-a", "https://flow/project/1")
    session.client = object()

    with pytest.raises(ValueError, match="Unknown job type"):
        await session.submit_many([_job("j1", "bogus-op")])
