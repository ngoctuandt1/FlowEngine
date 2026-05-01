def _profile_payload(name: str, *, status: str = "available") -> dict:
    return {
        "name": name,
        "google_account": f"{name}@example.com",
        "locale": "en",
        "tier": "ultra",
        "status": status,
    }


async def test_get_profile_jobs_returns_only_matching_profile_jobs(api_client):
    created_profile = await api_client.post(
        "/api/profiles",
        json=_profile_payload("profile-a"),
    )
    other_profile = await api_client.post(
        "/api/profiles",
        json=_profile_payload("profile-b"),
    )
    assert created_profile.status_code == 201
    assert other_profile.status_code == 201

    matching_job = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Job for profile A",
            "profile": "profile-a",
        },
    )
    other_job = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Job for profile B",
            "profile": "profile-b",
        },
    )
    assert matching_job.status_code == 201
    assert other_job.status_code == 201

    response = await api_client.get("/api/profiles/profile-a/jobs")

    assert response.status_code == 200
    jobs = response.json()
    assert [job["id"] for job in jobs] == [matching_job.json()["id"]]


async def test_post_quarantine_sets_status_and_persists(api_client):
    created = await api_client.post(
        "/api/profiles",
        json=_profile_payload("quarantine-me"),
    )
    assert created.status_code == 201

    response = await api_client.post(
        "/api/profiles/quarantine-me/quarantine",
        json={"reason": "manual review"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "quarantined"

    fetched = await api_client.get("/api/profiles/quarantine-me")

    assert fetched.status_code == 200
    assert fetched.json()["status"] == "quarantined"


async def test_post_activate_sets_status_and_persists(api_client):
    created = await api_client.post(
        "/api/profiles",
        json=_profile_payload("activate-me", status="quarantined"),
    )
    assert created.status_code == 201

    response = await api_client.post("/api/profiles/activate-me/activate")

    assert response.status_code == 200
    assert response.json()["status"] == "available"

    fetched = await api_client.get("/api/profiles/activate-me")

    assert fetched.status_code == 200
    assert fetched.json()["status"] == "available"


async def test_profile_status_round_trip_quarantine_then_activate(api_client):
    created = await api_client.post(
        "/api/profiles",
        json=_profile_payload("round-trip"),
    )
    assert created.status_code == 201

    quarantine = await api_client.post("/api/profiles/round-trip/quarantine")
    activate = await api_client.post("/api/profiles/round-trip/activate")
    fetched = await api_client.get("/api/profiles/round-trip")

    assert quarantine.status_code == 200
    assert quarantine.json()["status"] == "quarantined"
    assert activate.status_code == 200
    assert activate.json()["status"] == "available"
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "available"
