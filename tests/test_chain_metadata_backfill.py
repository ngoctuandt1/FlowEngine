import aiosqlite


async def test_init_db_backfills_chain_id_for_legacy_job_tree(temp_db_path):
    from server.db.database import init_db

    await init_db()

    root_id = "legacy-root"
    child_id = "legacy-child"
    grandchild_id = "legacy-grandchild"

    async with aiosqlite.connect(temp_db_path) as db:
        await db.executemany(
            """
            INSERT INTO jobs (
                id, type, status, job_level, parent_job_id, chain_id,
                model, aspect_ratio, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?, ?, 'veo-3.1-lite-lp', '16:9', ?, ?)
            """,
            [
                (root_id, "text-to-video", 1, None, None, "2026-05-02T00:00:00+00:00", "2026-05-02T00:00:00+00:00"),
                (child_id, "extend-video", 2, root_id, None, "2026-05-02T00:01:00+00:00", "2026-05-02T00:01:00+00:00"),
                (grandchild_id, "extend-video", 3, child_id, None, "2026-05-02T00:02:00+00:00", "2026-05-02T00:02:00+00:00"),
            ],
        )
        await db.commit()

    await init_db()

    async with aiosqlite.connect(temp_db_path) as db:
        cursor = await db.execute(
            "SELECT id, chain_id FROM jobs WHERE id IN (?, ?, ?) ORDER BY created_at ASC",
            (root_id, child_id, grandchild_id),
        )
        rows = await cursor.fetchall()

    assert rows == [
        (root_id, root_id),
        (child_id, root_id),
        (grandchild_id, root_id),
    ]
