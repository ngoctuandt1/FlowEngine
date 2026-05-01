from server.models.job import (
    CHAIN_CREATE_PROFILE_MISMATCH_ERROR,
    CHAIN_CREATE_PROFILE_REQUIRED_ERROR,
)


async def test_chain_profile_uses_chain_level_profile_when_present(api_client):
    response = await api_client.post(
        "/api/chains",
        json={
            "profile": "prof-a",
            "jobs": [
                {"type": "text-to-video", "prompt": "Open on a neon skyline"},
                {"type": "extend-video", "prompt": "Push through the haze"},
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert [job["profile"] for job in body["jobs"]] == ["prof-a", "prof-a"]

    chain_response = await api_client.get(f"/api/chains/{body['chain_id']}")
    assert chain_response.status_code == 200
    assert chain_response.json()["profile"] == "prof-a"


async def test_chain_profile_falls_back_to_consistent_step_profile(api_client):
    response = await api_client.post(
        "/api/chains",
        json={
            "jobs": [
                {"type": "text-to-video", "prompt": "Open on a mountain pass", "profile": "prof-b"},
                {"type": "extend-video", "prompt": "Continue the drone move", "profile": "prof-b"},
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert [job["profile"] for job in body["jobs"]] == ["prof-b", "prof-b"]

    chain_response = await api_client.get(f"/api/chains/{body['chain_id']}")
    assert chain_response.status_code == 200
    assert chain_response.json()["profile"] == "prof-b"


async def test_chain_profile_rejects_mismatched_step_profiles(api_client):
    response = await api_client.post(
        "/api/chains",
        json={
            "jobs": [
                {"type": "text-to-video", "prompt": "Open on a city street", "profile": "prof-b"},
                {"type": "extend-video", "prompt": "Tilt up to the skyline", "profile": "prof-c"},
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == CHAIN_CREATE_PROFILE_MISMATCH_ERROR


async def test_chain_profile_requires_at_least_one_profile(api_client):
    response = await api_client.post(
        "/api/chains",
        json={
            "jobs": [
                {"type": "text-to-video", "prompt": "Open on an empty runway"},
                {"type": "extend-video", "prompt": "Follow the taxi lights"},
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == CHAIN_CREATE_PROFILE_REQUIRED_ERROR
