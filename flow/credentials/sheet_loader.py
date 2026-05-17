"""Load Flow profile credentials from a Google Sheet into profiles_ultra.txt."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import gspread
from google.auth.exceptions import TransportError
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound
from requests.exceptions import RequestException

from profile_list import configured_profile_list_file

DEFAULT_SHEET_ID = "13rK2cKcDuykNA2SOiDUF4NJkA7fDK8JIFl7RACuRHDA"
DEFAULT_SHEET_TAB = "flowengine"
DEFAULT_SERVICE_ACCOUNT_PATH = "secrets/service_account.json"
DEFAULT_SERVICE_ACCOUNT_EMAIL = (
    "ai-585@gen-lang-client-0319523808.iam.gserviceaccount.com"
)
SOURCE_FILE = "file"
SOURCE_SHEET = "sheet"
NETWORK_RETRY_DELAY_SEC = 2

logger = logging.getLogger(__name__)


class SheetLoaderError(RuntimeError):
    """Base exception for sheet credential loading failures."""


class MalformedSheetError(SheetLoaderError):
    """Raised when sheet content cannot safely replace the local cache."""


@dataclass(frozen=True)
class SheetProfileRecord:
    """One credentials row mapped to profiles_ultra.txt fields."""

    profile: str
    email: str
    password: str
    totp_secret: str
    recovery: str = ""

    def to_profile_line(self) -> str:
        return "|".join(
            [self.profile, self.email, self.password, self.totp_secret, self.recovery]
        )


@dataclass(frozen=True)
class SyncResult:
    """Result of syncing Google Sheet credentials to the local cache file."""

    loaded: int
    profiles: list[str]
    output_path: Path


def accounts_source() -> str:
    """Return configured credentials source."""

    return (os.environ.get("FLOW_ACCOUNTS_SOURCE") or SOURCE_FILE).strip().lower()


def sheet_mode_enabled() -> bool:
    """Return true when credentials should sync from Google Sheets."""

    return accounts_source() == SOURCE_SHEET


def configured_sheet_id() -> str:
    return (os.environ.get("FLOW_ACCOUNTS_SHEET_ID") or DEFAULT_SHEET_ID).strip()


def configured_sheet_tab() -> str:
    return (os.environ.get("FLOW_ACCOUNTS_SHEET_TAB") or DEFAULT_SHEET_TAB).strip()


def configured_service_account_path() -> Path:
    raw_path = (
        os.environ.get("FLOW_ACCOUNTS_SA_PATH") or DEFAULT_SERVICE_ACCOUNT_PATH
    ).strip()
    return Path(raw_path).expanduser().resolve()


def _load_service_account_info(sa_path: Path) -> dict:
    try:
        with sa_path.open("r", encoding="utf-8") as handle:
            info = json.load(handle)
    except FileNotFoundError as exc:
        raise SheetLoaderError(f"Service account JSON not found: {sa_path}") from exc
    except json.JSONDecodeError as exc:
        raise SheetLoaderError(f"Service account JSON is invalid: {sa_path}") from exc

    if not isinstance(info, dict):
        raise SheetLoaderError(f"Service account JSON must be an object: {sa_path}")
    return info


def _service_account_email(info: dict) -> str:
    value = str(info.get("client_email") or "").strip()
    return value or DEFAULT_SERVICE_ACCOUNT_EMAIL


def _format_share_error(exc: Exception, sheet_id: str, tab_name: str, sa_email: str) -> str:
    return (
        "Could not read Google Sheet credentials "
        f"sheet_id={sheet_id!r} tab={tab_name!r} with service account {sa_email}. "
        "Confirm the sheet exists and is shared with that service account as Viewer. "
        f"Original error: {exc}"
    )


def _api_status_code(exc: APIError) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    return None


def _fetch_sheet_rows_once(
    *,
    sheet_id: str,
    tab_name: str,
    sa_path: Path,
) -> list[list[str]]:
    info = _load_service_account_info(sa_path)
    sa_email = _service_account_email(info)
    try:
        client = gspread.service_account_from_dict(info)
        spreadsheet = client.open_by_key(sheet_id)
        worksheet = spreadsheet.worksheet(tab_name)
        rows = worksheet.get("B:D")
    except (SpreadsheetNotFound, WorksheetNotFound) as exc:
        message = _format_share_error(exc, sheet_id, tab_name, sa_email)
        logger.error(message)
        raise SheetLoaderError(message) from exc
    except APIError as exc:
        if _api_status_code(exc) in {403, 404}:
            message = _format_share_error(exc, sheet_id, tab_name, sa_email)
            logger.error(message)
            raise SheetLoaderError(message) from exc
        logger.exception(
            "Google Sheets API error reading Flow credentials sheet_id=%r tab=%r",
            sheet_id,
            tab_name,
        )
        raise SheetLoaderError(f"Google Sheets API error reading credentials: {exc}") from exc

    return [list(row) for row in rows]


def fetch_sheet_rows(
    *,
    sheet_id: str | None = None,
    tab_name: str | None = None,
    sa_path: Path | None = None,
) -> list[list[str]]:
    """Fetch raw B:D values from the configured Google Sheet."""

    resolved_sheet_id = sheet_id or configured_sheet_id()
    resolved_tab_name = tab_name or configured_sheet_tab()
    resolved_sa_path = sa_path or configured_service_account_path()
    network_errors = (RequestException, TransportError, TimeoutError, ConnectionError)

    for attempt in range(2):
        try:
            return _fetch_sheet_rows_once(
                sheet_id=resolved_sheet_id,
                tab_name=resolved_tab_name,
                sa_path=resolved_sa_path,
            )
        except network_errors as exc:
            if attempt == 0:
                logger.warning(
                    "Network error reading Google Sheet credentials; retrying once in %ss: %s",
                    NETWORK_RETRY_DELAY_SEC,
                    exc,
                )
                time.sleep(NETWORK_RETRY_DELAY_SEC)
                continue
            logger.exception("Network error reading Google Sheet credentials after retry")
            raise

    raise AssertionError("unreachable retry state")


def _cell(row: Sequence[object], index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _normalise_header_cell(value: str) -> str:
    return "".join(ch for ch in value.strip().lower() if ch.isalnum())


def _looks_like_header(row: Sequence[object]) -> bool:
    normalised = [_normalise_header_cell(_cell(row, index)) for index in range(3)]
    has_email = any("email" in value or "googleaccount" in value for value in normalised)
    has_password = any(
        "password" in value or value in {"pass", "pwd"} for value in normalised
    )
    return has_email and has_password


def _totp_secret(value: str) -> str:
    return "".join(value.split())


def _profile_name(email: str) -> str:
    return email.split("@", 1)[0].strip()


def _has_pipe(record: SheetProfileRecord) -> bool:
    return any(
        "|" in value
        for value in [
            record.profile,
            record.email,
            record.password,
            record.totp_secret,
            record.recovery,
        ]
    )


def rows_to_profile_records(rows: Sequence[Sequence[object]]) -> list[SheetProfileRecord]:
    """Convert sheet B:D rows to profiles_ultra records."""

    if not rows:
        raise MalformedSheetError("Google Sheet credential tab is empty")

    data_rows = rows[1:] if _looks_like_header(rows[0]) else rows
    first_row_number = 2 if data_rows is not rows else 1
    records: list[SheetProfileRecord] = []

    for offset, row in enumerate(data_rows):
        row_number = first_row_number + offset
        email = _cell(row, 0)
        password = _cell(row, 1)
        totp_secret = _totp_secret(_cell(row, 2))

        if not any([email, password, totp_secret]):
            continue
        if not email or not password:
            logger.warning(
                "Skipping Google Sheet credential row %s: missing email or password",
                row_number,
            )
            continue

        profile = _profile_name(email)
        if not profile:
            logger.warning(
                "Skipping Google Sheet credential row %s: email has empty profile prefix",
                row_number,
            )
            continue

        record = SheetProfileRecord(
            profile=profile,
            email=email,
            password=password,
            totp_secret=totp_secret,
        )
        if _has_pipe(record):
            logger.warning(
                "Skipping Google Sheet credential row %s: pipe character is not supported",
                row_number,
            )
            continue
        records.append(record)

    if not records:
        raise MalformedSheetError("Google Sheet credential tab has no valid credential rows")

    return records


def write_profiles_cache(records: Sequence[SheetProfileRecord], output_path: Path) -> None:
    """Overwrite profiles_ultra.txt cache with records."""

    if not records:
        raise MalformedSheetError("Refusing to write empty profiles cache")


    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(record.to_profile_line() for record in records) + "\n"
    temp_suffix = f".{secrets.token_hex(8)}.tmp"
    temp_path = output_path.with_name(f"{output_path.name}{temp_suffix}")
    try:
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def sync_profiles_from_sheet(
    *,
    output_path: Path | None = None,
    sheet_id: str | None = None,
    tab_name: str | None = None,
    sa_path: Path | None = None,
) -> SyncResult:
    """Fetch Google Sheet credentials and overwrite the local profiles cache."""

    resolved_output_path = output_path or configured_profile_list_file()
    rows = fetch_sheet_rows(sheet_id=sheet_id, tab_name=tab_name, sa_path=sa_path)
    try:
        records = rows_to_profile_records(rows)
    except MalformedSheetError:
        logger.exception(
            "Malformed Google Sheet credentials; leaving existing profiles cache untouched: %s",
            resolved_output_path,
        )
        raise

    write_profiles_cache(records, resolved_output_path)
    profiles = [record.profile for record in records]
    logger.info(
        "Loaded %s Flow profile credentials from Google Sheet into %s",
        len(records),
        resolved_output_path,
    )
    return SyncResult(
        loaded=len(records),
        profiles=profiles,
        output_path=resolved_output_path,
    )
