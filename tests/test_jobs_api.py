from unittest.mock import AsyncMock
from time import perf_counter

import aiosqlite


async def _create_project(api_client, name: str) -> str:
    response = await api_client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


async def test_post_text_to_image_defaults_model(api_client):
    payload = {
        "type": "text-to-image",
        "prompt": "A glass teapot on a marble table",
        "aspect_ratio": "1:1",
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "text-to-image"
    assert body["model"] == "nano-banana-pro"
    assert body["aspect_ratio"] == "1:1"


async def test_post_text_to_image_keeps_ref_image_path(api_client):
    payload = {
        "type": "text-to-image",
        "prompt": "A product shot of a ceramic mug",
        "model": "imagen-4",
        "ref_image_path": "uploads/reference.png",
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["model"] == "imagen-4"
    assert body["ref_image_path"] == "uploads/reference.png"


async def test_post_ingredients_to_video_round_trips_ingredient_paths(api_client):
    payload = {
        "type": "ingredients-to-video",
        "prompt": "A cinematic cooking reel with fresh herbs and bright produce",
        "model": "veo-3.1-fast",
        "aspect_ratio": "16:9",
        "ingredient_image_paths": ["uploads/a.png", "uploads/b.png"],
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 201
    created = response.json()
    assert created["type"] == "ingredients-to-video"
    assert created["ingredient_image_paths"] == ["uploads/a.png", "uploads/b.png"]

    fetched = await api_client.get(f"/api/jobs/{created['id']}")

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["ingredient_image_paths"] == ["uploads/a.png", "uploads/b.png"]


async def test_post_l1_video_job_round_trips_voice_asset_id(api_client, temp_db_path):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "A narrated launch film",
            "voice_asset_id": "achernar",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["voice_asset_id"] == "achernar"

    fetched = await api_client.get(f"/api/jobs/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["voice_asset_id"] == "achernar"

    async with aiosqlite.connect(temp_db_path) as db:
        cursor = await db.execute(
            "SELECT voice_asset_id FROM jobs WHERE id = ?",
            (created["id"],),
        )
        row = await cursor.fetchone()
    assert row == ("achernar",)


async def test_post_unsupported_job_rejects_voice_asset_id(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-image",
            "prompt": "A portrait",
            "voice_asset_id": "achernar",
        },
    )

    assert response.status_code == 422
    assert "voice_asset_id is only supported" in response.text


async def test_post_job_with_missing_parent_returns_404(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Continue the clip",
            "parent_job_id": "missing-parent",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Parent job missing-parent not found"


async def test_post_l1_job_sets_chain_id_to_id(api_client, temp_db_path):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Create a root job with complete metadata",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["chain_id"] == created["id"]

    async with aiosqlite.connect(temp_db_path) as db:
        cursor = await db.execute(
            "SELECT chain_id FROM jobs WHERE id = ?",
            (created["id"],),
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == created["id"]


async def test_post_child_job_inherits_completed_parent_fields(api_client):
    project_id = await _create_project(api_client, "Parent Project")

    parent_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Create a forest flythrough",
            "profile": "parent-profile",
            "project_id": project_id,
        },
    )
    assert parent_response.status_code == 201
    parent_id = parent_response.json()["id"]

    patch_response = await api_client.put(
        f"/api/worker/jobs/{parent_id}",
        json={
            "status": "completed",
            "project_url": "https://flow.example/project/123",
            "media_id": "media-123",
            "profile": "parent-profile",
        },
    )
    assert patch_response.status_code == 200

    child_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Add a second camera pass",
            "parent_job_id": parent_id,
        },
    )

    assert child_response.status_code == 201
    child = child_response.json()
    assert child["job_level"] == 2
    assert child["chain_id"] == parent_response.json()["chain_id"]
    assert child["profile"] == "parent-profile"
    assert child["project_id"] == project_id
    assert child["project_url"] == "https://flow.example/project/123"
    assert child["media_id"] == "media-123"


async def test_post_l1_job_persists_project_id(api_client):
    project_id = await _create_project(api_client, "L1 Project")

    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Create a branded opener",
            "project_id": project_id,
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["project_id"] == project_id

    fetched = await api_client.get(f"/api/jobs/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["project_id"] == project_id


async def test_post_child_job_discards_explicit_project_id_override(api_client):
    parent_project_id = await _create_project(api_client, "Parent Override Project")
    await _create_project(api_client, "Child Override Project")

    parent_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Create a parent clip",
            "profile": "override-profile",
            "project_id": parent_project_id,
        },
    )
    assert parent_response.status_code == 201
    parent_id = parent_response.json()["id"]

    patch_response = await api_client.put(
        f"/api/worker/jobs/{parent_id}",
        json={
            "status": "completed",
            "project_url": "https://flow.example/project/parent",
            "media_id": "media-parent",
            "profile": "override-profile",
        },
    )
    assert patch_response.status_code == 200

    child_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Explicitly reassign this child",
            "parent_job_id": parent_id,
            "project_id": "project-child-override",
        },
    )

    assert child_response.status_code == 201
    child = child_response.json()
    assert child["project_id"] == parent_project_id


async def test_worker_patch_ignores_project_id_reassignment(api_client):
    project_id = await _create_project(api_client, "Stable Project")

    created = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Pinned project assignment",
            "project_id": project_id,
        },
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    patch_response = await api_client.put(
        f"/api/worker/jobs/{job_id}",
        json={
            "status": "completed",
            "project_id": "project-moved",
        },
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["project_id"] == project_id

    fetched = await api_client.get(f"/api/jobs/{job_id}")
    assert fetched.status_code == 200
    assert fetched.json()["project_id"] == project_id


async def test_worker_patch_persists_structured_error_fields(api_client):
    created = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Pinned error fields",
        },
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    patch_response = await api_client.put(
        f"/api/worker/jobs/{job_id}",
        json={
            "status": "failed",
            "error": "paid tier required",
            "error_kind": "paid_tier_required",
            "error_message": "Flow paid plan required for L2 edit",
        },
    )

    assert patch_response.status_code == 200
    patched = patch_response.json()
    assert patched["error"] == "paid tier required"
    assert patched["error_kind"] == "paid_tier_required"
    assert patched["error_message"] == "Flow paid plan required for L2 edit"

    fetched = await api_client.get(f"/api/jobs/{job_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["error_kind"] == "paid_tier_required"
    assert body["error_message"] == "Flow paid plan required for L2 edit"


async def test_post_child_job_inherits_chain_id_from_pending_parent(api_client):
    parent_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Create a parent that is still pending",
            "profile": "chain-profile",
        },
    )
    assert parent_response.status_code == 201
    parent = parent_response.json()

    child_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Queue a follow-up before the parent completes",
            "parent_job_id": parent["id"],
        },
    )

    assert child_response.status_code == 201
    child = child_response.json()
    assert child["job_level"] == 2
    assert child["chain_id"] == parent["chain_id"]
    assert child["profile"] == "chain-profile"


async def test_post_child_job_falls_back_to_parent_id_when_parent_chain_id_is_null(
    api_client, temp_db_path
):
    parent_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Create a legacy-style parent row",
            "profile": "legacy-profile",
        },
    )
    assert parent_response.status_code == 201
    parent = parent_response.json()

    async with aiosqlite.connect(temp_db_path) as db:
        await db.execute(
            "UPDATE jobs SET chain_id = NULL WHERE id = ?",
            (parent["id"],),
        )
        await db.commit()

    child_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Child should inherit the defensive fallback",
            "parent_job_id": parent["id"],
        },
    )

    assert child_response.status_code == 201
    child = child_response.json()
    assert child["chain_id"] == parent["id"]


async def test_post_chain_creates_linked_jobs(api_client):
    response = await api_client.post(
        "/api/chains",
        json={
            "profile": "chain-profile",
            "jobs": [
                {"type": "text-to-video", "prompt": "Open with a sunrise shot"},
                {"type": "extend-video", "prompt": "Hold on the skyline"},
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["chain_id"]
    assert len(body["jobs"]) == 2
    assert body["jobs"][0]["job_level"] == 1
    assert body["jobs"][1]["job_level"] == 2
    assert body["jobs"][1]["parent_job_id"] == body["jobs"][0]["id"]
    assert body["jobs"][0]["chain_id"] == body["chain_id"]
    assert body["jobs"][1]["chain_id"] == body["chain_id"]


async def test_post_chain_rejects_empty_jobs(api_client):
    response = await api_client.post("/api/chains", json={"jobs": []})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "jobs"]


async def test_get_chain_returns_404_for_unknown_chain(api_client):
    response = await api_client.get("/api/chains/missing-chain")

    assert response.status_code == 404
    assert response.json()["detail"] == "Chain missing-chain not found"


async def test_list_jobs_applies_filters(api_client):
    first = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Pending skyline shot",
            "profile": "filter-profile",
        },
    )
    second = await api_client.post(
        "/api/jobs",
        json={
            "type": "ingredients-to-video",
            "prompt": "Animate from references",
            "ingredient_image_paths": ["uploads/filter.png"],
            "profile": "other-profile",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201

    filtered = await api_client.get(
        "/api/jobs",
        params={
            "status": "pending",
            "type": "text-to-video",
            "profile": "filter-profile",
        },
    )

    assert filtered.status_code == 200
    jobs = filtered.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["id"] == first.json()["id"]


async def test_list_jobs_q_filters_across_searchable_fields(api_client):
    first = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Search by project URL",
            "profile": "AlphaProfile",
            "project_url": "https://labs.google/fx/tools/flow/projects/SkyCastle",
        },
    )
    second = await api_client.post(
        "/api/jobs",
        json={
            "type": "ingredients-to-video",
            "prompt": "Search by type",
            "ingredient_image_paths": ["uploads/search.png"],
            "profile": "BetaProfile",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201

    by_project = await api_client.get("/api/jobs", params={"q": "skycastle"})
    by_type = await api_client.get("/api/jobs", params={"q": "INGREDIENTS"})
    by_profile = await api_client.get("/api/jobs", params={"q": "alphaprofile"})
    by_id = await api_client.get("/api/jobs", params={"q": first.json()["id"][:8]})

    assert by_project.status_code == 200
    assert [job["id"] for job in by_project.json()["jobs"]] == [first.json()["id"]]
    assert by_type.status_code == 200
    assert [job["id"] for job in by_type.json()["jobs"]] == [second.json()["id"]]
    assert by_profile.status_code == 200
    assert [job["id"] for job in by_profile.json()["jobs"]] == [first.json()["id"]]
    assert by_id.status_code == 200
    assert [job["id"] for job in by_id.json()["jobs"]] == [first.json()["id"]]


async def test_jobs_fts_substring_match(api_client):
    created = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Search substring project URL",
            "profile": "SubstringProfile",
            "project_url": "https://labs.google/fx/tools/flow/projects/SkyCastle",
        },
    )
    assert created.status_code == 201

    response = await api_client.get("/api/jobs", params={"q": "castle"})

    assert response.status_code == 200
    assert [job["id"] for job in response.json()["jobs"]] == [created.json()["id"]]


async def test_jobs_fts_backfill_covers_existing_chained_rows(temp_db_path):
    from server.db.database import init_db

    created_at = "2026-01-01T00:00:00+00:00"
    async with aiosqlite.connect(temp_db_path) as db:
        await db.executescript(
            """
            CREATE TABLE chains (
                id TEXT PRIMARY KEY,
                profile TEXT,
                project_url TEXT,
                media_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                job_level INTEGER NOT NULL DEFAULT 1,
                parent_job_id TEXT,
                chain_id TEXT,
                profile TEXT,
                project_url TEXT,
                media_id TEXT,
                edit_url TEXT,
                project_id TEXT,
                prompt TEXT,
                model TEXT NOT NULL DEFAULT 'veo-3.1-lite',
                aspect_ratio TEXT NOT NULL DEFAULT '16:9',
                bbox_json TEXT,
                direction TEXT,
                start_image_path TEXT,
                end_image_path TEXT,
                ingredient_image_paths_json TEXT,
                ref_image_path TEXT,
                safety_filter TEXT,
                output_files_json TEXT,
                generation_id TEXT,
                worker_id TEXT,
                claimed_at TEXT,
                completed_at TEXT,
                error TEXT,
                error_kind TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE jobs_fts USING fts5(
                id,
                project_url,
                type,
                profile,
                tokenize='unicode61'
            );
            """
        )
        await db.execute(
            """
            INSERT INTO chains (id, profile, project_url, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-root",
                "LegacyProfile",
                "https://labs.google/fx/tools/flow/projects/LegacyCastle",
                "active",
                created_at,
                created_at,
            ),
        )
        await db.executemany(
            """
            INSERT INTO jobs (
                id, type, status, job_level, parent_job_id, chain_id, profile,
                project_url, prompt, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "legacy-root",
                    "text-to-video",
                    "pending",
                    1,
                    None,
                    "legacy-root",
                    "LegacyProfile",
                    "https://labs.google/fx/tools/flow/projects/LegacyCastleRoot",
                    "root",
                    created_at,
                    created_at,
                ),
                (
                    "legacy-child",
                    "extend-video",
                    "pending",
                    2,
                    "legacy-root",
                    "legacy-root",
                    "LegacyProfile",
                    "https://labs.google/fx/tools/flow/projects/LegacyCastleChild",
                    "child",
                    created_at,
                    created_at,
                ),
            ],
        )
        await db.execute(
            """
            INSERT INTO jobs_fts(rowid, id, project_url, type, profile)
            SELECT rowid, id, project_url, type, profile
            FROM jobs
            WHERE id = 'legacy-root'
            """
        )
        await db.commit()

    await init_db()

    async with aiosqlite.connect(temp_db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM jobs_fts WHERE jobs_fts MATCH ? ORDER BY id",
            ("castlechild",),
        )
        rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs_fts'"
        )
        fts_sql = (await cursor.fetchone())[0]

    assert [row[0] for row in rows] == ["legacy-child"]
    assert "trigram" in fts_sql


async def test_list_jobs_q_fts_prefix_scale(api_client, temp_db_path):
    created_at = "2026-01-01T00:00:00+00:00"
    rows = [
        (
            f"fts-scale-{index:04d}",
            "text-to-video",
            "pending",
            1,
            f"scale-profile-{index:04d}",
            f"https://labs.google/fx/tools/flow/projects/ScaleProject{index:04d}",
            "perf seed",
            "veo-3.1-fast",
            "16:9",
            created_at,
            created_at,
        )
        for index in range(5000)
    ]
    async with aiosqlite.connect(temp_db_path) as db:
        await db.executemany(
            """
            INSERT INTO jobs (
                id, type, status, job_level, profile, project_url, prompt,
                model, aspect_ratio, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await db.commit()

    start = perf_counter()
    response = await api_client.get("/api/jobs", params={"q": "sc", "limit": 25})
    elapsed_ms = (perf_counter() - start) * 1000

    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 25
    assert elapsed_ms < 200


async def test_list_jobs_q_special_chars_falls_back_to_like(api_client):
    created = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Search special chars",
            "profile": "unsafe*profile",
            "project_url": "https://labs.google/fx/tools/flow/projects/Quoted:Project",
        },
    )
    assert created.status_code == 201

    for query in ["unsafe*", '"profile"', "quoted:"]:
        response = await api_client.get("/api/jobs", params={"q": query})
        assert response.status_code == 200

    matched = await api_client.get("/api/jobs", params={"q": "unsafe*"})
    assert [job["id"] for job in matched.json()["jobs"]] == [created.json()["id"]]


async def test_list_jobs_q_rejects_too_short(api_client):
    resp = await api_client.get("/api/jobs", params={"q": "a"})
    assert resp.status_code == 422


async def test_list_jobs_q_rejects_too_long(api_client):
    resp = await api_client.get("/api/jobs", params={"q": "x" * 200})
    assert resp.status_code == 422


async def test_list_jobs_has_more_uses_extra_row(api_client):
    for index in range(3):
        created = await api_client.post(
            "/api/jobs",
            json={
                "type": "text-to-video",
                "prompt": f"Paged search {index}",
                "profile": "paged-profile",
            },
        )
        assert created.status_code == 201

    first_page = await api_client.get("/api/jobs", params={"q": "paged-profile", "limit": 2})
    second_page = await api_client.get("/api/jobs", params={"q": "paged-profile", "limit": 2, "offset": 2})

    assert first_page.status_code == 200
    assert len(first_page.json()["jobs"]) == 2
    assert first_page.json()["has_more"] is True
    assert second_page.status_code == 200
    assert len(second_page.json()["jobs"]) == 1
    assert second_page.json()["has_more"] is False


async def test_get_job_counts_returns_pending_totals(api_client):
    first = await api_client.post(
        "/api/jobs",
        json={"type": "text-to-video", "prompt": "Count me in"},
    )
    second = await api_client.post(
        "/api/jobs",
        json={"type": "text-to-video", "prompt": "And me too"},
    )
    assert first.status_code == 201
    assert second.status_code == 201

    response = await api_client.get("/api/jobs/counts")

    assert response.status_code == 200
    assert response.json()["pending"] >= 2


async def test_get_single_job_returns_404_for_missing_job(api_client):
    response = await api_client.get("/api/jobs/missing-job")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job missing-job not found"


async def test_get_job_children_returns_404_for_missing_parent(api_client):
    response = await api_client.get("/api/jobs/missing-parent/children")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job missing-parent not found"


async def test_delete_job_broadcasts_cancelled_update(api_client, monkeypatch):
    import server.routes.jobs as jobs_route

    created = await api_client.post(
        "/api/jobs",
        json={"type": "text-to-video", "prompt": "Delete this job"},
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    broadcast = AsyncMock()
    monkeypatch.setattr(jobs_route, "broadcast_job_update", broadcast)

    response = await api_client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": job_id}
    assert broadcast.await_count == 1
    cancelled_job = broadcast.await_args.args[0]
    assert cancelled_job.id == job_id
    assert cancelled_job.status.value == "cancelled"


async def test_recover_jobs_broadcasts_each_recovered_job(api_client, monkeypatch):
    import server.routes.jobs as jobs_route
    from server.models.job import Job, JobStatus, JobType

    recovered_jobs = [
        Job(type=JobType.TEXT_TO_VIDEO, prompt="Recovered 1", status=JobStatus.PENDING),
        Job(type=JobType.EXTEND_VIDEO, prompt="Recovered 2", status=JobStatus.PENDING),
    ]
    broadcast = AsyncMock()

    async def fake_recover_stale_jobs():
        return recovered_jobs

    monkeypatch.setattr(jobs_route, "recover_stale_jobs", fake_recover_stale_jobs)
    monkeypatch.setattr(jobs_route, "broadcast_job_update", broadcast)

    response = await api_client.post("/api/jobs/recover")

    assert response.status_code == 200
    assert response.json()["recovered"] == 2
    assert [job["id"] for job in response.json()["jobs"]] == [job.id for job in recovered_jobs]
    assert broadcast.await_count == 2
    assert [call.args[0].id for call in broadcast.await_args_list] == [job.id for job in recovered_jobs]
