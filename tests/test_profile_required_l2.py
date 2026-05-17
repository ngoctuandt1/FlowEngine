PARENT_PROFILE = "ngoctuandt20"


async def _create_l1_parent(api_client, *, profile: str | None = PARENT_PROFILE):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Parent clip",
            "profile": profile,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _set_parent_status(api_client, parent_id: str, status: str) -> None:
    if status == "pending":
        return
    response = await api_client.put(
        f"/api/worker/jobs/{parent_id}",
        json={
            "status": status,
            "profile": PARENT_PROFILE,
            "project_url": "https://flow.example/project/profile-pin",
            "media_id": "media-profile-pin",
        },
    )
    assert response.status_code == 200, response.text


async def test_l2_submit_inherits_parent_profile_without_explicit_profile(api_client):
    parent = await _create_l1_parent(api_client)
    await _set_parent_status(api_client, parent["id"], "completed")

    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Extend parent clip",
            "parent_job_id": parent["id"],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_level"] == 2
    assert body["profile"] == PARENT_PROFILE


async def test_l2_submit_rejects_explicit_profile_mismatch(api_client):
    parent = await _create_l1_parent(api_client)

    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Wrong account follow-up",
            "parent_job_id": parent["id"],
            "profile": "other",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "INV-B account binding (child profile must match parent profile)"
    )


async def test_l2_submit_accepts_explicit_profile_matching_parent(api_client):
    parent = await _create_l1_parent(api_client)

    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Same account follow-up",
            "parent_job_id": parent["id"],
            "profile": PARENT_PROFILE,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_level"] == 2
    assert body["profile"] == PARENT_PROFILE


async def test_l2_submit_inherits_profile_from_pending_parent(api_client):
    parent = await _create_l1_parent(api_client)

    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Queue follow-up before completion",
            "parent_job_id": parent["id"],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["profile"] == PARENT_PROFILE


async def test_l2_submit_rejects_unpinned_parent_without_explicit_profile(api_client):
    parent = await _create_l1_parent(api_client, profile=None)

    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "No account binding available",
            "parent_job_id": parent["id"],
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "INV-A" in detail
    assert "profile" in detail


async def test_l2_submit_without_parent_or_project_url_is_rejected(api_client):
    # INV-1 + INV-4: L2 op types must NOT downgrade to job_level=1. With
    # neither parent_job_id nor a resolvable project_url, profile cannot be
    # inherited and the dispatcher would skip the project_lock for the
    # contending project.
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Bare L2 must be rejected",
        },
    )

    assert response.status_code == 422, response.text
    assert "L2" in response.json()["detail"]


async def test_l1_submit_without_profile_remains_allowed(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Unpinned L1 remains claim-time bound",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_level"] == 1
    assert body["profile"] is None


async def test_l2_submit_rejects_explicit_profile_when_parent_unpinned(api_client):
    parent = await _create_l1_parent(api_client, profile=None)

    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Explicitly bind unpinned parent chain",
            "parent_job_id": parent["id"],
            "profile": PARENT_PROFILE,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "child cannot specify profile when parent profile is unpinned — "
        "wait for parent to be claimed and pinned, or re-root the chain (INV-B)"
    )


async def test_l2_profile_inheritance_works_before_parent_completion(api_client):
    for status in ("claimed", "running"):
        parent = await _create_l1_parent(api_client)
        await _set_parent_status(api_client, parent["id"], status)

        response = await api_client.post(
            "/api/jobs",
            json={
                "type": "extend-video",
                "prompt": f"Child while parent is {status}",
                "parent_job_id": parent["id"],
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["profile"] == PARENT_PROFILE
