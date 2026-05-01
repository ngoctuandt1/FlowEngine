"""Lint profiles_ultra.txt and report Chrome-profile health.

Usage:
    python scripts/check_profiles_ultra.py
    python scripts/check_profiles_ultra.py --json
    python scripts/check_profiles_ultra.py --profiles-file /opt/flowengine/profiles_ultra.txt
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from profile_list import DEFAULT_PROFILE_LIST_FILE, configured_profile_list_file, resolve_profile_list_file

DEFAULT_PROFILE_BASE_DIR = Path("./chrome-profiles")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
STATUS_ORDER = [
    "warmed",
    "warming-failed",
    "burned",
    "unwarmed",
    "malformed",
]


@dataclass(frozen=True)
class ProfileReportRow:
    line_number: int
    profile_name: str
    email: str
    status: str
    status_display: str
    warmed_at: str | None
    totp: str
    notes: str
    malformed: bool


@dataclass(frozen=True)
class ReportResult:
    profiles_file: str
    profile_base_dir: str
    rows: list[ProfileReportRow]
    summary: dict[str, int]
    parse_error_count: int
    todo: list[str]


def _default_profiles_file() -> Path:
    return configured_profile_list_file(default=DEFAULT_PROFILE_LIST_FILE)


def _default_profile_base_dir() -> Path:
    configured = (os.environ.get("CHROME_USER_DATA_DIR") or "").strip()
    if configured:
        return Path(configured)
    return DEFAULT_PROFILE_BASE_DIR


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lint profiles_ultra.txt and report Chrome-profile health.",
    )
    parser.add_argument(
        "--profiles-file",
        default=str(_default_profiles_file()),
        help="Path to profiles_ultra.txt (default: FLOW_PROFILE_LIST_FILE or repo-local profiles_ultra.txt).",
    )
    parser.add_argument(
        "--profile-base-dir",
        default=str(_default_profile_base_dir()),
        help="Chrome profile base dir (default: CHROME_USER_DATA_DIR or ./chrome-profiles).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a text table.",
    )
    return parser.parse_args(argv)


def _candidate_profile_names(profile_path: str, email: str) -> list[str]:
    candidates: list[str] = []
    for candidate in (Path(profile_path).name.strip(), email.split("@", 1)[0].strip()):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates or ["unknown"]


def _validate_totp(secret: str) -> str:
    normalized = re.sub(r"\s+", "", secret).upper()
    if not normalized:
        return "missing"

    padding = "=" * (-len(normalized) % 8)
    try:
        base64.b32decode(normalized + padding, casefold=True)
    except (binascii.Error, ValueError):
        return "TOTP malformed"
    return "TOTP ok"


def _cookies_candidates(profile_dir: Path) -> Iterable[Path]:
    yield profile_dir / "Default" / "Cookies"
    yield profile_dir / "Default" / "Network" / "Cookies"


def _format_timestamp(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).astimezone()
    return stamp.isoformat(timespec="seconds")


def _resolve_profile_status(
    profile_base_dir: Path,
    candidates: list[str],
) -> tuple[str, str, str | None, str]:
    for candidate in candidates:
        profile_dir = profile_base_dir / candidate
        if not profile_dir.exists():
            continue

        for cookies_path in _cookies_candidates(profile_dir):
            if cookies_path.exists():
                return candidate, "warmed", _format_timestamp(cookies_path), ""

        return (
            candidate,
            "warming-failed",
            None,
            "profile directory exists but no Cookies file was found",
        )

    for candidate in candidates:
        matches = sorted(profile_base_dir.glob(f"{candidate}.burned-*"))
        if not matches:
            continue
        burned_path = matches[-1]
        suffix = burned_path.name.removeprefix(f"{candidate}.burned-")
        return candidate, "burned", None, f"archive={burned_path.name} suffix={suffix}"

    return candidates[0], "unwarmed", None, ""


def _parse_line(
    line_number: int,
    raw_line: str,
    profile_base_dir: Path,
) -> ProfileReportRow:
    parts = [part.strip() for part in raw_line.split("|")]
    profile_path = parts[0] if parts else ""
    email = parts[1] if len(parts) > 1 else ""
    password = parts[2] if len(parts) > 2 else ""
    totp_secret = parts[3] if len(parts) > 3 else ""

    candidates = _candidate_profile_names(profile_path, email)
    profile_name = candidates[0]
    issues: list[str] = []

    # flow.login._load_credentials currently tolerates missing trailing fields
    # by filling them with "", but its documented contract is the canonical
    # 5-field row below. Enforce that schema here so incomplete credentials fail
    # lint/CI instead of being misreported as merely "unwarmed".
    if len(parts) != 5:
        issues.append(
            "expected exactly 5 pipe-delimited fields "
            "(path|email|password|2fa_secret|recovery_email)"
        )
    if not email or not EMAIL_RE.match(email):
        issues.append("invalid email")
    if not password:
        issues.append("empty password")

    totp_status = _validate_totp(totp_secret)
    if issues:
        if not profile_path and email:
            profile_name = candidates[0]
        notes = "; ".join(issues)
        return ProfileReportRow(
            line_number=line_number,
            profile_name=profile_name,
            email=email,
            status="malformed",
            status_display="malformed",
            warmed_at=None,
            totp=totp_status,
            notes=notes,
            malformed=True,
        )

    resolved_name, status, warmed_at, notes = _resolve_profile_status(
        profile_base_dir=profile_base_dir,
        candidates=candidates,
    )
    profile_name = resolved_name
    status_display = status
    if status == "burned":
        suffix = notes.split("suffix=", 1)[1] if "suffix=" in notes else "unknown"
        status_display = f"burned ({suffix})"

    return ProfileReportRow(
        line_number=line_number,
        profile_name=profile_name,
        email=email,
        status=status,
        status_display=status_display,
        warmed_at=warmed_at,
        totp=totp_status,
        notes=notes,
        malformed=False,
    )


def build_report(profiles_file: Path, profile_base_dir: Path) -> ReportResult:
    profiles_file = resolve_profile_list_file(profiles_file)
    profile_base_dir = profile_base_dir.expanduser()
    # TODO: Detect ServiceNotAllowed and iap-stuck "dead" accounts via
    # marker files / warm-profile log heuristics once those signals are
    # standardized in the repo.

    rows: list[ProfileReportRow] = []
    for line_number, raw_line in enumerate(
        profiles_file.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(_parse_line(line_number, line, profile_base_dir))

    counts = Counter(row.status for row in rows)
    summary = {status: counts.get(status, 0) for status in STATUS_ORDER}
    summary["total"] = len(rows)
    return ReportResult(
        profiles_file=str(profiles_file),
        profile_base_dir=str(profile_base_dir),
        rows=rows,
        summary=summary,
        parse_error_count=summary["malformed"],
        todo=[
            "Dead-account detection (ServiceNotAllowed/iap-stuck) is not implemented in v1.",
        ],
    )


def _table_lines(rows: list[ProfileReportRow]) -> list[str]:
    headers = [
        "profile_name",
        "email",
        "status",
        "warmed_at",
        "totp",
        "notes",
    ]
    data_rows = [
        [
            row.profile_name,
            row.email,
            row.status_display,
            row.warmed_at or "",
            row.totp,
            row.notes,
        ]
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(values[index]) for values in data_rows))
        if data_rows
        else len(headers[index])
        for index in range(len(headers))
    ]

    def format_row(values: list[str]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        )

    lines = [format_row(headers), "-+-".join("-" * width for width in widths)]
    lines.extend(format_row(values) for values in data_rows)
    return lines


def format_table(report: ReportResult) -> str:
    lines = [
        f"profiles_file: {report.profiles_file}",
        f"profile_base_dir: {report.profile_base_dir}",
        "",
        *_table_lines(report.rows),
        "",
        "Summary: "
        + ", ".join(
            f"{status}={report.summary[status]}" for status in [*STATUS_ORDER, "total"]
        ),
    ]
    if report.parse_error_count:
        lines.append(f"Parse errors: {report.parse_error_count}")
    if report.todo:
        lines.append(f"TODO: {report.todo[0]}")
    return "\n".join(lines)


def format_json(report: ReportResult) -> str:
    payload = {
        "profiles_file": report.profiles_file,
        "profile_base_dir": report.profile_base_dir,
        "entries": [asdict(row) for row in report.rows],
        "summary": report.summary,
        "parse_error_count": report.parse_error_count,
        "todo": report.todo,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(
            profiles_file=Path(args.profiles_file),
            profile_base_dir=Path(args.profile_base_dir),
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(format_json(report))
    else:
        print(format_table(report))

    return 1 if report.parse_error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
