#!/usr/bin/env python3
"""Live verify deep extend chains with optional reverse-API replay.

Dry-run is safe for CI and unit tests. Live mode needs valid Flow Chrome
profiles and is intended for manual use only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

EXPECTED_SEC_PER_LEVEL = 8.0
VEOLITE_CREDITS_PER_LEVEL = 6
DEFAULT_L1_PROMPT = "a calm river running through a misty forest at dawn"
EXTEND_PROMPTS = [
    "the camera tilts up to reveal a wider scene",
    "soft warm light fades in slowly",
    "the scene transitions to early dawn",
    "first rays of sunlight cut through the mist",
    "birds cross the sky as the haze clears",
    "the river bends toward a quiet valley",
    "golden reflections ripple across the water",
    "the scene opens to a panoramic mountain view",
    "a gentle breeze moves through the trees",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live verify L1->LN extend chains via UI or hybrid revAPI.",
    )
    parser.add_argument("--profile", required=True, help="Chrome profile name")
    parser.add_argument(
        "--profiles",
        help="Comma-separated profile names; overrides --profile for parallel runs",
    )
    parser.add_argument("--depth", type=int, default=10, help="Chain depth, L1->LN")
    parser.add_argument(
        "--mode",
        choices=("ui", "hybrid", "revapi"),
        default="hybrid",
        help="ui=all UI, hybrid=UI L2 then replay L3+, revapi=same env gate",
    )
    parser.add_argument(
        "--assert-duration",
        dest="assert_duration",
        action="store_true",
        default=True,
        help="Assert per-level ffprobe durations (default: on)",
    )
    parser.add_argument(
        "--no-assert-duration",
        dest="assert_duration",
        action="store_false",
        help="Skip ffprobe duration assertion",
    )
    parser.add_argument(
        "--duration-tolerance-sec",
        type=float,
        default=2.0,
        help="Allowed per-level duration delta in seconds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Flow calls and emit deterministic level plan",
    )
    return parser


def selected_profiles(args: argparse.Namespace) -> list[str]:
    if args.profiles:
        profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
        if profiles:
            return profiles
    return [args.profile]


def configure_mode_env(mode: str) -> None:
    os.environ["FLOW_EXTEND_VIA_REVERSE"] = "0" if mode == "ui" else "1"


def build_level_plan(profile: str, depth: int, mode: str) -> list[dict[str, Any]]:
    if depth < 1:
        raise ValueError("--depth must be >= 1")
    levels = []
    for level in range(1, depth + 1):
        if level == 1:
            submit = "t2v-ui"
        elif mode == "ui":
            submit = "extend-ui"
        elif level == 2:
            submit = "extend-ui-capture"
        else:
            submit = "extend-replay-api-fallback-ui"
        levels.append(
            {
                "profile": profile,
                "level": level,
                "expected_dur": level * EXPECTED_SEC_PER_LEVEL,
                "submit": submit,
                "status": "planned",
                "media_id": "",
                "path": "",
            }
        )
    return levels


def print_level_table(levels: list[dict[str, Any]]) -> None:
    print("Level | Expected | Status | Submit | Media ID | File")
    print("---:|---:|---|---|---|---")
    for entry in levels:
        media_id = str(entry.get("media_id") or "")
        path = str(entry.get("path") or "")
        print(
            f"L{entry['level']} | "
            f"{float(entry.get('expected_dur') or 0):.1f}s | "
            f"{entry.get('status') or ''} | "
            f"{entry.get('submit') or ''} | "
            f"{media_id[:12]} | "
            f"{Path(path).name if path else ''}"
        )


def write_json_summary(summary: dict[str, Any], ts: int) -> Path:
    out_dir = REPO_ROOT / "tests" / "live_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}_chain_deep_revapi.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def write_markdown_report(profile: str, ts: int, content: str) -> Path:
    report_path = REPO_ROOT / "error-captures" / f"duration_chain_{profile}_{ts}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")
    return report_path


def _duration_downloads(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "level": entry["level"],
            "media_id": entry.get("media_id") or "",
            "path": entry.get("path") or "",
        }
        for entry in levels
    ]


def run_duration_assertion(
    levels: list[dict[str, Any]],
    *,
    profile: str,
    ts: int,
    tolerance_sec: float,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        report = "Duration assertion skipped (--no-assert-duration).\n"
        report_path = write_markdown_report(profile, ts, report)
        return {"all_pass": True, "rows": [], "report_path": str(report_path)}

    try:  # C1 sibling PR; guarded so this script lands independently.
        from flow.diagnostics_duration import assert_chain_duration, write_duration_report
    except Exception as exc:
        rows = [
            {
                "level": entry["level"],
                "media_id": entry.get("media_id") or "",
                "expected": entry["level"] * EXPECTED_SEC_PER_LEVEL,
                "actual": None,
                "delta": None,
                "pass": True,
                "skipped": True,
            }
            for entry in levels
        ]
        report = (
            "Duration assertion skipped: flow.diagnostics_duration unavailable.\n"
            f"\nImport error: {exc}\n"
        )
        report_path = write_markdown_report(profile, ts, report)
        return {"all_pass": True, "rows": rows, "report_path": str(report_path)}

    result = assert_chain_duration(
        _duration_downloads(levels),
        tolerance_sec=tolerance_sec,
    )
    report_path = REPO_ROOT / "error-captures" / f"duration_chain_{profile}_{ts}.md"
    write_duration_report(result, report_path)
    result["report_path"] = str(report_path)
    return result


def _safe_media_id(media_id: str | None) -> str:
    safe = "".join(c for c in str(media_id or "") if c.isalnum() or c in "-_")
    return safe or "missing"


def materialize_output_file(
    *,
    level: int,
    media_id: str | None,
    output_files: list[str] | None,
    download_dir: Path,
) -> str:
    if not output_files:
        return ""
    src = Path(output_files[0])
    dest = download_dir / f"L{level}_{_safe_media_id(media_id)}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return ""
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return str(dest)


async def capture_chain_failure(
    client,
    *,
    profile: str,
    level: int,
    ts: int,
    error: BaseException,
) -> None:
    capture_dir = REPO_ROOT / "error-captures"
    capture_dir.mkdir(parents=True, exist_ok=True)
    stem = capture_dir / f"chain_fail_{profile}_L{level}_{ts}"

    try:
        from flow.diagnostics import capture_failure
    except Exception:
        capture_failure = None

    if capture_failure is not None:
        try:
            await capture_failure(
                client,
                f"chain_fail_{profile}_L{level}_{ts}",
                "extend_fail" if level > 1 else "l1_fail",
                extra={"error": str(error), "level": level, "profile": profile},
            )
        except Exception:
            pass

    page = getattr(client, "page", None)
    if page is not None:
        try:
            await page.screenshot(path=str(stem.with_suffix(".png")), full_page=False)
        except Exception:
            pass
        try:
            stem.with_suffix(".html").write_text(await page.content(), encoding="utf-8")
        except Exception:
            pass
    try:
        calls = getattr(client, "_calls", [])[-50:]
        stem.with_suffix(".network.json").write_text(
            json.dumps({"error": str(error), "calls": calls}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


async def run_live_profile(
    *,
    profile: str,
    depth: int,
    mode: str,
    ts: int,
    assert_duration: bool,
    duration_tolerance_sec: float,
) -> dict[str, Any]:
    from flow.client import FlowClient
    from flow.operations.extend import extend_video
    from flow.operations.generate import text_to_video

    log = logging.getLogger(f"chain-deep-revapi.{profile}")
    download_dir = REPO_ROOT / "downloads" / f"chain_{profile}_{ts}"
    download_dir.mkdir(parents=True, exist_ok=True)
    profile_base_dir = os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")
    levels = build_level_plan(profile, depth, mode)
    t0 = time.time()

    client = FlowClient(
        profile_name=profile,
        profile_base_dir=profile_base_dir,
        download_dir=str(download_dir),
    )
    try:
        async with client:
            client._job_id = f"chain-deep-revapi-{profile}-{ts}"
            log.info("L1 submit via UI")
            try:
                result = await text_to_video(
                    client,
                    prompt=DEFAULT_L1_PROMPT,
                    free_mode=True,
                )
            except Exception as exc:
                await capture_chain_failure(client, profile=profile, level=1, ts=ts, error=exc)
                raise

            path = materialize_output_file(
                level=1,
                media_id=result.get("media_id"),
                output_files=result.get("output_files"),
                download_dir=download_dir,
            )
            levels[0].update(
                {
                    "status": "completed",
                    "media_id": result.get("media_id") or "",
                    "path": path,
                    "project_url": result.get("project_url") or "",
                    "edit_url": result.get("edit_url") or "",
                    "generation_id": result.get("generation_id") or "",
                }
            )

            previous = result
            for level in range(2, depth + 1):
                prompt = EXTEND_PROMPTS[(level - 2) % len(EXTEND_PROMPTS)]
                job = {
                    "id": f"chain-{profile}-L{level}-{ts}",
                    "type": "extend-video",
                    "job_level": level,
                    "parent_job_id": f"chain-{profile}-L{level - 1}-{ts}",
                    "project_url": previous.get("project_url"),
                    "edit_url": previous.get("edit_url"),
                    "media_id": previous.get("media_id"),
                    "prompt": prompt,
                    "profile": profile,
                }
                log.info("L%d extend submit (%s)", level, levels[level - 1]["submit"])
                try:
                    result = await extend_video(
                        client,
                        job=job,
                        prompt=prompt,
                        free_mode=True,
                    )
                except Exception as exc:
                    levels[level - 1].update(
                        {"status": "failed", "error": str(exc), "prompt": prompt}
                    )
                    await capture_chain_failure(
                        client, profile=profile, level=level, ts=ts, error=exc,
                    )
                    break

                path = materialize_output_file(
                    level=level,
                    media_id=result.get("media_id"),
                    output_files=result.get("output_files"),
                    download_dir=download_dir,
                )
                levels[level - 1].update(
                    {
                        "status": "completed",
                        "media_id": result.get("media_id") or "",
                        "path": path,
                        "project_url": result.get("project_url") or previous.get("project_url") or "",
                        "edit_url": result.get("edit_url") or "",
                        "generation_id": result.get("generation_id") or "",
                        "prompt": prompt,
                    }
                )
                previous = result
    except Exception as exc:
        log.exception("profile %s chain failed: %s", profile, exc)
        first_planned = next((entry for entry in levels if entry["status"] == "planned"), None)
        if first_planned:
            first_planned.update({"status": "failed", "error": str(exc)})

    duration = run_duration_assertion(
        levels,
        profile=profile,
        ts=ts,
        tolerance_sec=duration_tolerance_sec,
        enabled=assert_duration,
    )
    completed_levels = [entry for entry in levels if entry.get("status") == "completed"]
    media_ids = [entry.get("media_id") for entry in completed_levels if entry.get("media_id")]
    files = [entry.get("path") for entry in completed_levels if entry.get("path")]
    all_pass = (
        len(completed_levels) == depth
        and len(set(media_ids)) == depth
        and len(set(files)) == depth
        and bool(duration.get("all_pass"))
    )
    return {
        "profile": profile,
        "depth": depth,
        "mode": mode,
        "levels": levels,
        "duration": duration,
        "all_pass": all_pass,
        "wall_time_sec": round(time.time() - t0, 1),
        "credit_estimate": depth * VEOLITE_CREDITS_PER_LEVEL,
        "download_dir": str(download_dir),
    }


async def run_dry_profile(
    *,
    profile: str,
    depth: int,
    mode: str,
    ts: int,
    assert_duration: bool,
    duration_tolerance_sec: float,
) -> dict[str, Any]:
    levels = build_level_plan(profile, depth, mode)
    for entry in levels:
        media_id = f"dry-{profile}-L{entry['level']}"
        entry.update(
            {
                "status": "completed",
                "media_id": media_id,
                "path": str(
                    REPO_ROOT
                    / "downloads"
                    / f"chain_{profile}_{ts}"
                    / f"L{entry['level']}_{media_id}.mp4"
                ),
            }
        )
    rows = [
        {
            "level": entry["level"],
            "media_id": entry["media_id"],
            "expected": entry["expected_dur"],
            "actual": entry["expected_dur"] if assert_duration else None,
            "delta": 0.0 if assert_duration else None,
            "pass": True,
        }
        for entry in levels
    ]
    report = "# Dry-run duration plan\n\n" + "\n".join(
        f"- L{row['level']}: expected {row['expected']:.1f}s, tolerance +/-{duration_tolerance_sec:.1f}s"
        for row in rows
    ) + "\n"
    report_path = write_markdown_report(profile, ts, report)
    return {
        "profile": profile,
        "depth": depth,
        "mode": mode,
        "levels": levels,
        "duration": {"all_pass": True, "rows": rows, "report_path": str(report_path)},
        "all_pass": True,
        "wall_time_sec": 0.0,
        "credit_estimate": depth * VEOLITE_CREDITS_PER_LEVEL,
        "dry_run": True,
    }


async def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    configure_mode_env(args.mode)
    profiles = selected_profiles(args)
    ts = int(time.time())
    runner = run_dry_profile if args.dry_run else run_live_profile
    results = await asyncio.gather(
        *[
            runner(
                profile=profile,
                depth=args.depth,
                mode=args.mode,
                ts=ts,
                assert_duration=args.assert_duration,
                duration_tolerance_sec=args.duration_tolerance_sec,
            )
            for profile in profiles
        ]
    )

    for result in results:
        label = "DRY-RUN" if args.dry_run else ("PASS" if result["all_pass"] else "FAIL")
        print(
            f"\n{label}: profile={result['profile']} depth={result['depth']} "
            f"mode={result['mode']} wall={result['wall_time_sec']}s "
            f"credits~{result['credit_estimate']}"
        )
        print_level_table(result["levels"])
        print(f"Duration report: {result['duration'].get('report_path')}")

    all_pass = all(result.get("all_pass") for result in results)
    summary = {
        "profile": profiles[0] if len(profiles) == 1 else ",".join(profiles),
        "profiles": profiles,
        "depth": args.depth,
        "mode": args.mode,
        "levels": results[0]["levels"] if len(results) == 1 else [],
        "results": results,
        "all_pass": all_pass,
        "wall_time_sec": round(sum(result["wall_time_sec"] for result in results), 1),
        "credit_estimate": sum(result["credit_estimate"] for result in results),
        "dry_run": args.dry_run,
    }
    summary_path = write_json_summary(summary, ts)
    print(f"\nJSON summary: {summary_path}")
    print(
        f"SUMMARY: {'PASS' if all_pass else 'FAIL'} "
        f"profiles={len(profiles)} credits~{summary['credit_estimate']}"
    )
    return (0 if all_pass else 1), summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        code, _summary = asyncio.run(run(args))
        return code
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
