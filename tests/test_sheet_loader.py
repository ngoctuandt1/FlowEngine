from __future__ import annotations

import importlib

import pytest


class FakeWorksheet:
    def __init__(self, rows):
        self.rows = rows

    def get(self, range_name):
        assert range_name == "B:D"
        return self.rows


class FakeSpreadsheet:
    def __init__(self, rows):
        self.rows = rows

    def worksheet(self, tab_name):
        assert tab_name == "flowengine"
        return FakeWorksheet(self.rows)


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def open_by_key(self, sheet_id):
        assert sheet_id == "sheet-123"
        return FakeSpreadsheet(self.rows)


def _service_account_file(tmp_path):
    path = tmp_path / "secrets" / "service_account.json"
    path.parent.mkdir()
    path.write_text(
        '{"client_email": "ai-585@gen-lang-client-0319523808.iam.gserviceaccount.com"}',
        encoding="utf-8",
    )
    return path


def _patch_fake_sheet(monkeypatch, rows):
    import flow.credentials.sheet_loader as sheet_loader

    def fake_service_account_from_dict(info):
        assert info["client_email"] == (
            "ai-585@gen-lang-client-0319523808.iam.gserviceaccount.com"
        )
        return FakeClient(rows)

    monkeypatch.setattr(
        sheet_loader.gspread,
        "service_account_from_dict",
        fake_service_account_from_dict,
    )


def test_monkeypatched_gspread_rows_write_profiles_ultra_format(monkeypatch, tmp_path):
    import flow.credentials.sheet_loader as sheet_loader

    rows = [
        ["email", "password", "2fa"],
        ["ngoctuandt20@gmail.com", "Ngoctuan1", "typb zr27 oxku"],
    ]
    _patch_fake_sheet(monkeypatch, rows)
    output_path = tmp_path / "profiles_ultra.txt"

    result = sheet_loader.sync_profiles_from_sheet(
        output_path=output_path,
        sheet_id="sheet-123",
        tab_name="flowengine",
        sa_path=_service_account_file(tmp_path),
    )

    assert result.loaded == 1
    assert result.profiles == ["ngoctuandt20"]
    assert output_path.read_text(encoding="utf-8") == (
        "ngoctuandt20|ngoctuandt20@gmail.com|Ngoctuan1|typbzr27oxku|\n"
    )


def test_profile_name_derived_from_email_prefix():
    from flow.credentials.sheet_loader import rows_to_profile_records

    records = rows_to_profile_records([["ngoctuandt20@gmail.com", "pw", "SECRET"]])

    assert records[0].profile == "ngoctuandt20"


def test_totp_column_whitespace_normalized_lowercase_preserved():
    from flow.credentials.sheet_loader import rows_to_profile_records

    records = rows_to_profile_records(
        [["user@example.com", "pw", "typb zr27\toxku\n73o4"]]
    )

    assert records[0].totp_secret == "typbzr27oxku73o4"


def test_empty_missing_rows_skipped():
    from flow.credentials.sheet_loader import rows_to_profile_records

    records = rows_to_profile_records(
        [
            ["email", "password", "2fa"],
            [],
            ["", "", ""],
            ["user@example.com", "pw", "SECRET"],
        ]
    )

    assert [record.profile for record in records] == ["user"]


def test_missing_password_or_email_skipped_and_warns(caplog):
    from flow.credentials.sheet_loader import rows_to_profile_records

    records = rows_to_profile_records(
        [
            ["email", "password", "2fa"],
            ["missing-password@example.com", "", "SECRET"],
            ["", "pw", "SECRET"],
            ["ok@example.com", "pw", "SECRET"],
        ]
    )

    assert [record.profile for record in records] == ["ok"]
    assert caplog.text.count("missing email or password") == 2


def test_writer_creates_parent_dir(tmp_path):
    from flow.credentials.sheet_loader import SheetProfileRecord, write_profiles_cache
    output_path = tmp_path / "missing" / "profiles_ultra.txt"

    write_profiles_cache(
        [SheetProfileRecord("user", "user@example.com", "pw", "SECRET")],
        output_path,
    )

    assert output_path.exists()

    assert output_path.read_text(encoding="utf-8") == "user|user@example.com|pw|SECRET|\n"


def test_writer_idempotent_overwrite(tmp_path):
    from flow.credentials.sheet_loader import SheetProfileRecord, write_profiles_cache
    output_path = tmp_path / "profiles_ultra.txt"
    records = [SheetProfileRecord("user", "user@example.com", "pw", "SECRET")]

    write_profiles_cache(records, output_path)
    first = output_path.read_text(encoding="utf-8")
    write_profiles_cache(records, output_path)
    second = output_path.read_text(encoding="utf-8")

    assert second == first


def _reload_app(monkeypatch, *, api_key: str, source: str):
    monkeypatch.setenv("API_KEY", api_key)
    monkeypatch.setenv("FLOW_ACCOUNTS_SOURCE", source)

    import server.config
    import server.routes.profiles
    import server.app

    importlib.reload(server.config)
    importlib.reload(server.routes.profiles)
    importlib.reload(server.app)
    return server.app.app, server.routes.profiles


@pytest.mark.asyncio
async def test_reload_endpoint_correct_key_returns_count(monkeypatch, db):
    from httpx import ASGITransport, AsyncClient

    app, profiles_route = _reload_app(
        monkeypatch,
        api_key="sheet-test-key",
        source="sheet",
    )

    class Result:
        loaded = 2
        profiles = ["alpha", "beta"]

    monkeypatch.setattr(profiles_route, "sync_profiles_from_sheet", lambda: Result())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/profiles/reload",
            headers={"X-Worker-API-Key": "sheet-test-key"},
        )

    assert response.status_code == 200
    assert response.json() == {"loaded": 2, "profiles": ["alpha", "beta"]}


@pytest.mark.asyncio
async def test_reload_endpoint_without_key_rejects_when_api_key_set(monkeypatch, db):
    from httpx import ASGITransport, AsyncClient

    app, profiles_route = _reload_app(
        monkeypatch,
        api_key="sheet-test-key",
        source="sheet",
    )
    monkeypatch.setattr(profiles_route, "sync_profiles_from_sheet", lambda: None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/profiles/reload")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_reload_endpoint_file_source_returns_409(monkeypatch, db):
    from httpx import ASGITransport, AsyncClient

    app, profiles_route = _reload_app(
        monkeypatch,
        api_key="sheet-test-key",
        source="file",
    )
    monkeypatch.setattr(profiles_route, "sync_profiles_from_sheet", lambda: None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/profiles/reload",
            headers={"X-Worker-API-Key": "sheet-test-key"},
        )

    assert response.status_code == 409
