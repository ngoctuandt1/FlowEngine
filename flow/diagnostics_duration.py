"""Video duration diagnostics for live chain verification."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def ffprobe_duration(path: Path | str) -> float:
    """Return video duration in seconds via ffprobe.

    Invalid, missing, or unparsable files return ``0.0`` and log a warning.
    """
    video_path = Path(path)
    if not video_path.exists():
        logger.warning("ffprobe duration skipped; file does not exist: %s", video_path)
        return 0.0

    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        logger.warning("ffprobe duration failed for %s: %s", video_path, exc)
        return 0.0

    raw_duration = (proc.stdout or "").strip()
    if proc.returncode != 0:
        logger.warning(
            "ffprobe duration failed for %s: rc=%s stderr=%s",
            video_path,
            proc.returncode,
            (proc.stderr or "").strip(),
        )
        return 0.0

    try:
        return float(raw_duration)
    except (TypeError, ValueError):
        logger.warning(
            "ffprobe duration parse failed for %s: %r",
            video_path,
            raw_duration,
        )
        return 0.0


def assert_chain_duration(
    downloads: list[dict],
    *,
    expected_per_level_sec: float = 8.0,
    tolerance_sec: float = 2.0,
) -> dict:
    """Check downloaded chain videos grow by expected duration per level."""
    rows: list[dict[str, Any]] = []
    for entry in downloads:
        level = int(entry["level"])
        media_id = str(entry.get("media_id") or "")
        expected = level * expected_per_level_sec
        actual = ffprobe_duration(entry["path"])
        delta = actual - expected
        passed = abs(delta) <= tolerance_sec
        rows.append(
            {
                "level": level,
                "media_id": media_id,
                "expected": expected,
                "actual": actual,
                "pass": passed,
                "delta": delta,
            }
        )

    report_markdown = _duration_report_markdown(rows)
    return {
        "rows": rows,
        "all_pass": all(row["pass"] for row in rows),
        "report_markdown": report_markdown,
    }


def write_duration_report(result: dict, out_path: Path) -> None:
    """Write duration assertion markdown report."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(str(result.get("report_markdown") or ""), encoding="utf-8")


def _duration_report_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Level | Expected | Actual | Δ | Pass | Media ID short |",
        "|---:|---:|---:|---:|:---:|---|",
    ]
    for row in rows:
        status = "PASS" if row["pass"] else "FAIL"
        media_id_short = str(row.get("media_id") or "")[:12]
        lines.append(
            "| "
            f"L{row['level']} | "
            f"{row['expected']:.1f}s | "
            f"{row['actual']:.1f}s | "
            f"{row['delta']:+.1f}s | "
            f"{status} | "
            f"{media_id_short} |"
        )
    return "\n".join(lines) + "\n"
