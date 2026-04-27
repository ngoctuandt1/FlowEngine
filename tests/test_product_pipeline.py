from datetime import UTC, datetime
from pathlib import Path

from server.models.job import Job, JobType


def _fake_job(job_id: str, job_type: JobType) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=job_type,
        prompt="x",
        created_at=now,
        updated_at=now,
    )


async def test_post_product_pipeline_happy_path_builds_expected_chain(api_client, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(tmp_path))

    import server.routes.product_pipeline as product_pipeline

    captured = {}

    async def fake_create_chain_endpoint(req):
        captured["req"] = req
        return {
            "chain_id": "chain-123",
            "jobs": [
                _fake_job("step-1", JobType.FRAMES_TO_VIDEO),
                _fake_job("step-2", JobType.EXTEND_VIDEO),
            ],
        }

    monkeypatch.setattr(
        product_pipeline,
        "create_chain_endpoint",
        fake_create_chain_endpoint,
    )

    image_path = tmp_path / "product.png"
    payload = {
        "product_image_path": str(image_path),
        "brief": "Luxury skincare bottle on reflective glass",
        "profile": "profile-a",
        "aspect_ratio": "9:16",
    }

    response = await api_client.post("/api/product-pipeline/", json=payload)

    assert response.status_code == 201
    assert response.json() == {
        "chain_id": "chain-123",
        "step_ids": ["step-1", "step-2"],
    }

    chain_req = captured["req"]
    assert chain_req.profile == "profile-a"
    assert len(chain_req.jobs) == 2
    assert chain_req.jobs[0].type == JobType.FRAMES_TO_VIDEO
    assert chain_req.jobs[0].prompt == (
        "Luxury skincare bottle on reflective glass, smooth camera dolly-in"
    )
    assert chain_req.jobs[0].start_image_path == str(image_path)
    assert chain_req.jobs[1].type == JobType.EXTEND_VIDEO
    assert chain_req.jobs[1].prompt == (
        "Luxury skincare bottle on reflective glass, dramatic reveal"
    )
    assert all(job.aspect_ratio == "9:16" for job in chain_req.jobs)


async def test_post_product_pipeline_rejects_path_outside_upload_dir(api_client, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(tmp_path))
    outside_path = tmp_path.parent / "escape.png"

    response = await api_client.post(
        "/api/product-pipeline/",
        json={
            "product_image_path": str(outside_path),
            "brief": "A valid brief",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "product_image_path must resolve under FLOW_UPLOAD_DIR"


async def test_post_product_pipeline_rejects_parent_traversal(api_client, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(tmp_path))

    response = await api_client.post(
        "/api/product-pipeline/",
        json={
            "product_image_path": "../../escape.png",
            "brief": "A valid brief",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "product_image_path must resolve under FLOW_UPLOAD_DIR"


async def test_post_product_pipeline_rejects_too_long_brief(api_client, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(tmp_path))
    product_path = tmp_path / "product.png"

    response = await api_client.post(
        "/api/product-pipeline/",
        json={
            "product_image_path": str(product_path),
            "brief": "x" * 501,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "brief must be between 1 and 500 characters"


async def test_post_product_pipeline_rejects_empty_brief(api_client, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(tmp_path))
    product_path = tmp_path / "product.png"

    response = await api_client.post(
        "/api/product-pipeline/",
        json={
            "product_image_path": str(product_path),
            "brief": "   ",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "brief must be between 1 and 500 characters"


async def test_post_product_pipeline_defaults_profile_and_aspect_ratio(api_client, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(tmp_path))

    import server.routes.product_pipeline as product_pipeline

    captured = {}

    async def fake_create_chain_endpoint(req):
        captured["req"] = req
        return {
            "chain_id": "chain-defaults",
            "jobs": [
                _fake_job("step-1", JobType.FRAMES_TO_VIDEO),
                _fake_job("step-2", JobType.EXTEND_VIDEO),
            ],
        }

    monkeypatch.setattr(
        product_pipeline,
        "create_chain_endpoint",
        fake_create_chain_endpoint,
    )

    response = await api_client.post(
        "/api/product-pipeline/",
        json={
            "product_image_path": "uploads/product.png",
            "brief": "Premium coffee bag",
        },
    )

    assert response.status_code == 201
    assert response.json()["chain_id"] == "chain-defaults"

    chain_req = captured["req"]
    assert chain_req.profile is None
    assert [job.type for job in chain_req.jobs] == [
        JobType.FRAMES_TO_VIDEO,
        JobType.EXTEND_VIDEO,
    ]
    assert all(job.aspect_ratio == "16:9" for job in chain_req.jobs)
    assert chain_req.jobs[0].start_image_path == "uploads/product.png"
