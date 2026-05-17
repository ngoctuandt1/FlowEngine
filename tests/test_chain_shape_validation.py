import pytest

from server.models.job import JobCreate
from server.routes.jobs import _validate_chain_shape


def _bbox() -> dict[str, float]:
    return {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3}


def _job(job_type: str, **extra) -> JobCreate:
    payload = {"type": job_type, "prompt": f"prompt for {job_type}"}
    payload.update(extra)
    if job_type in {"insert-object", "remove-object"}:
        payload.setdefault("bbox", _bbox())
    if job_type == "camera-move":
        payload.setdefault("direction", "Dolly in")
    return JobCreate(**payload)


def _invalid_error(jobs: list[JobCreate]) -> str:
    err = _validate_chain_shape(jobs)
    assert err is not None
    return err


def test_validate_chain_shape_rejects_extend_then_remove():
    err = _invalid_error([
        _job("text-to-video"),
        _job("extend-video"),
        _job("remove-object"),
    ])

    assert "chain shape invalid" in err
    assert "job[2] type=remove-object" in err
    assert "job[1]" in err
    assert "feedback_extend_terminal_op.md" in err


def test_validate_chain_shape_rejects_extend_then_camera():
    err = _invalid_error([
        _job("text-to-video"),
        _job("extend-video"),
        _job("camera-move"),
    ])

    assert "job[2] type=camera-move" in err


def test_validate_chain_shape_rejects_extend_then_insert():
    err = _invalid_error([
        _job("text-to-video"),
        _job("extend-video"),
        _job("insert-object"),
    ])

    assert "job[2] type=insert-object" in err


def test_validate_chain_shape_allows_repeated_extend():
    assert _validate_chain_shape([
        _job("text-to-video"),
        _job("extend-video"),
        _job("extend-video"),
        _job("extend-video"),
    ]) is None


def test_validate_chain_shape_allows_remove_without_extend_ancestor():
    assert _validate_chain_shape([
        _job("text-to-video"),
        _job("remove-object"),
    ]) is None


def test_validate_chain_shape_allows_camera_before_extend():
    assert _validate_chain_shape([
        _job("text-to-video"),
        _job("camera-move"),
        _job("extend-video"),
    ]) is None


def test_validate_chain_shape_rejects_transitive_extend_ancestor():
    err = _invalid_error([
        _job("text-to-video"),
        _job("extend-video"),
        _job("extend-video"),
        _job("remove-object"),
    ])

    assert "job[3] type=remove-object" in err
    assert "job[2]" in err


def test_validate_chain_shape_allows_empty_jobs():
    assert _validate_chain_shape([]) is None


@pytest.mark.parametrize("blocked_type", ["remove-object", "camera-move", "insert-object"])
async def test_post_chain_rejects_extend_child_lockout(api_client, blocked_type):
    blocked_step = {"type": blocked_type, "prompt": "blocked op"}
    if blocked_type in {"remove-object", "insert-object"}:
        blocked_step["bbox"] = _bbox()
    if blocked_type == "camera-move":
        blocked_step["direction"] = "Dolly in"

    response = await api_client.post(
        "/api/chains",
        json={
            "profile": "chain-profile",
            "jobs": [
                {"type": "text-to-video", "prompt": "Root clip"},
                {"type": "extend-video", "prompt": "Extend clip"},
                blocked_step,
            ],
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "chain shape invalid" in detail
    assert f"type={blocked_type}" in detail
    assert "camera/insert/remove BEFORE extend" in detail


async def test_post_chain_allows_camera_before_extend(api_client):
    response = await api_client.post(
        "/api/chains",
        json={
            "profile": "chain-profile",
            "jobs": [
                {"type": "text-to-video", "prompt": "Root clip"},
                {"type": "camera-move", "prompt": "Camera first", "direction": "Dolly in"},
                {"type": "extend-video", "prompt": "Extend after camera"},
            ],
        },
    )

    assert response.status_code == 201


async def test_post_job_rejects_existing_extend_parent_lineage(api_client):
    root = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Root clip",
            "profile": "chain-profile",
        },
    )
    assert root.status_code == 201

    extend = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Extend clip",
            "parent_job_id": root.json()["id"],
        },
    )
    assert extend.status_code == 201

    blocked = await api_client.post(
        "/api/jobs",
        json={
            "type": "remove-object",
            "prompt": "Remove object",
            "parent_job_id": extend.json()["id"],
            "bbox": _bbox(),
        },
    )

    assert blocked.status_code == 400
    assert "extend-video ancestor" in blocked.json()["detail"]
