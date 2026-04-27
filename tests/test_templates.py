"""API coverage for workflow templates."""

from server.db.database import get_db


def _template_payload() -> dict:
    return {
        "name": "Starter chain",
        "description": "Reusable workflow",
        "steps": [
            {"type": "text-to-video", "prompt": "Intro {{subject}}"},
            {"type": "extend-video", "prompt": "Continue {{subject}}"},
        ],
    }


async def _create_template(api_client, payload: dict | None = None) -> dict:
    response = await api_client.post("/api/templates", json=payload or _template_payload())
    assert response.status_code == 201
    return response.json()


async def test_create_template(api_client):
    response = await api_client.post("/api/templates", json=_template_payload())
    assert response.status_code == 201

    body = response.json()
    assert body["name"] == "Starter chain"
    assert body["description"] == "Reusable workflow"
    assert body["steps"][0]["prompt"] == "Intro {{subject}}"

    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT name, description, steps_json FROM templates WHERE id = ?",
            (body["id"],),
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row["name"] == "Starter chain"
    assert row["description"] == "Reusable workflow"
    assert "{{subject}}" in row["steps_json"]
    assert "audio_path" not in row["steps_json"]


async def test_list_templates(api_client):
    first = await _create_template(api_client)
    second = await _create_template(
        api_client,
        {
            "name": "Second chain",
            "description": None,
            "steps": [{"type": "text-to-video", "prompt": "Only one"}],
        },
    )

    response = await api_client.get("/api/templates")
    assert response.status_code == 200

    body = response.json()
    assert [item["id"] for item in body] == [second["id"], first["id"]]


async def test_get_template(api_client):
    created = await _create_template(api_client)

    response = await api_client.get(f"/api/templates/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_update_template(api_client):
    created = await _create_template(api_client)

    response = await api_client.put(
        f"/api/templates/{created['id']}",
        json={
            "name": "Updated chain",
            "description": "New description",
            "steps": [{"type": "text-to-video", "prompt": "Updated {{subject}}"}],
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["name"] == "Updated chain"
    assert body["description"] == "New description"
    assert body["steps"] == [{"type": "text-to-video", "prompt": "Updated {{subject}}"}]


async def test_delete_template(api_client):
    created = await _create_template(api_client)

    response = await api_client.delete(f"/api/templates/{created['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": created["id"]}

    missing = await api_client.get(f"/api/templates/{created['id']}")
    assert missing.status_code == 404


async def test_create_template_rejects_invalid_placeholder_name(api_client):
    payload = _template_payload()
    payload["steps"][0]["prompt"] = "Intro {{bad-name}}"

    response = await api_client.post("/api/templates", json=payload)
    assert response.status_code == 422
    assert "Invalid template variable name" in response.text


async def test_instantiate_template_happy_path(api_client):
    created = await _create_template(api_client)

    response = await api_client.post(
        f"/api/templates/{created['id']}/instantiate",
        json={
            "template_id": created["id"],
            "vars": {"subject": "dragons"},
        },
    )
    assert response.status_code == 201

    body = response.json()
    assert body["chain_id"]
    assert [job["prompt"] for job in body["jobs"]] == [
        "Intro dragons",
        "Continue dragons",
    ]
    assert body["jobs"][0]["chain_id"] == body["chain_id"]
    assert body["jobs"][1]["parent_job_id"] == body["jobs"][0]["id"]


async def test_instantiate_template_rejects_missing_var(api_client):
    created = await _create_template(api_client)

    response = await api_client.post(
        f"/api/templates/{created['id']}/instantiate",
        json={
            "template_id": created["id"],
            "vars": {},
        },
    )
    assert response.status_code == 422
    assert "Missing template variables: subject" in response.text


async def test_instantiate_template_forwards_to_chain_creation(monkeypatch, api_client):
    created = await _create_template(api_client)
    captured = {}

    async def fake_create_chain_endpoint(req):
        captured["req"] = req
        return {"chain_id": "mock-chain", "jobs": []}

    import server.routes.jobs

    monkeypatch.setattr(server.routes.jobs, "create_chain_endpoint", fake_create_chain_endpoint)

    response = await api_client.post(
        f"/api/templates/{created['id']}/instantiate",
        json={
            "template_id": created["id"],
            "vars": {"subject": "phoenix"},
        },
    )
    assert response.status_code == 201
    assert response.json()["chain_id"] == "mock-chain"
    assert captured["req"].jobs[0].prompt == "Intro phoenix"
    assert captured["req"].jobs[1].prompt == "Continue phoenix"


async def test_create_template_rejects_empty_steps(api_client):
    response = await api_client.post(
        "/api/templates",
        json={"name": "Empty", "description": None, "steps": []},
    )

    assert response.status_code == 422


async def test_instantiate_template_rejects_invalid_var_name(api_client):
    created = await _create_template(api_client)

    response = await api_client.post(
        f"/api/templates/{created['id']}/instantiate",
        json={
            "template_id": created["id"],
            "vars": {"bad-name": "dragon"},
        },
    )
    assert response.status_code == 422
    assert "Invalid variable name" in response.text
