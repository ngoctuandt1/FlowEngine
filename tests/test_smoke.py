"""B9 smoke tests — confirm conftest fixtures actually work.

If these two pass, every later test can trust:
- `db` gives a fresh schema pointing at a throwaway temp file
- `api_client` routes requests through the real FastAPI app in-process
"""


async def test_fixture_db_works(db):
    """B9: the db fixture inits schema and the jobs table is queryable / empty."""
    from server.db.job_store import get_job_counts

    counts = await get_job_counts()
    assert counts == {
        "pending": 0,
        "claimed": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
    }


async def test_fixture_api_client_works(api_client):
    """B9: the api_client fixture reaches the FastAPI app via ASGITransport."""
    r = await api_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
