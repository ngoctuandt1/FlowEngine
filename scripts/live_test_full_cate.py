"""Live full-category smoke test for FlowEngine.

Submits a comprehensive sweep of jobs against a running FlowEngine server and
worker, then polls until all jobs reach a terminal state.

Topology submitted:

  Always-on L1 (7 jobs total):
    - text-to-video        x 5  (1 standalone baseline + 4 chain heads)
    - text-to-image        x 2

  Optional L1 (up to 4 jobs total, asset-dependent):
    - frames-to-video      x 2  (requires FLOW_TEST_FRAMES_DIR with images;
                                 otherwise SKIPPED with a warning)
    - ingredients-to-video x 2  (requires FLOW_TEST_INGREDIENTS_DIR with
                                 images; otherwise SKIPPED with a warning)

  L2+ sweep (12 jobs total, split across 4 independent Flow projects):
    - extend chain:  text-to-video -> extend x5 (L2→L3→L4→L5)
    - camera chain:  text-to-video -> camera-move -> camera-move
    - insert chain:  text-to-video -> insert-object -> insert-object
    - remove chain:  text-to-video -> remove-object -> remove-object

Design note (Debian live deploy, 2026-04-30):
  The previous harness built one deep linear chain:

      text-to-video -> extend -> extend -> camera -> camera
      -> insert -> insert -> remove -> remove

  Flow disables Camera when the current media is the result of extend-video
  ("extend-child lockout"; `docs/FLOW_BUTTON_EXACT.md` §5.1). The live failure
  surfaced from `flow/operations/_base.py::click_action_button` as:

      RuntimeError: Mode button "Camera" disabled — extend-child lockout

  Retrying camera-move with `parent_job_id` pointing directly to the original
  L1 text-to-video succeeded. This harness therefore isolates each L2 category
  on its own L1-rooted Flow project and submits all 4 text-to-video chain heads
  before any L2 request. That also matches memory
  `feedback_l1_siblings_only.md`: no L2 sibling fan-out on one project; each
  variant gets its own child project chain.

Credit note (rough estimates only):
  - Old linear sweep: ~90 credits (1 text-to-video head + 8 L2 ops).
  - New split sweep: ~120 credits (4 text-to-video heads + 8 L2 ops).
  - Full run with the always-on L1 set plus optional frames/ingredients is
    roughly ~165-185 credits, depending on account tier and upscale settings.

This script is a pure HTTP client. It NEVER touches worker/server core code,
NEVER drives Playwright directly, and NEVER tries to start the worker. Run a
worker against the same server first.

Usage:

    # Defaults: server=http://localhost:8080, no auth, 60-min timeout
    python scripts/live_test_full_cate.py

    # Override server + skip the asset-dependent jobs
    python scripts/live_test_full_cate.py \\
        --server http://debian-worker.local:8080 \\
        --skip-frames --skip-ingredients

    # Print the planned topology and estimated credit tally without HTTP
    python scripts/live_test_full_cate.py --dry-run --skip-frames --skip-ingredients

    # Optional bearer auth
    FLOW_API_KEY=xxx python scripts/live_test_full_cate.py

Environment variables:
    SERVER_URL                  Base URL of the FlowEngine server (default
                                http://localhost:8080). --server overrides.
    FLOW_API_KEY                Optional bearer token; sent as
                                ``Authorization: Bearer <key>`` if present.
    FLOW_TEST_FRAMES_DIR        Directory containing at least one image file
                                used as start_image_path for frames-to-video.
                                Missing/empty -> frames-to-video is SKIPPED.
    FLOW_TEST_INGREDIENTS_DIR   Directory with one or more image files used as
                                ingredient_image_paths. Missing/empty ->
                                ingredients-to-video is SKIPPED.

When FLOW_TEST_FRAMES_DIR / FLOW_TEST_INGREDIENTS_DIR are set, this harness
stages the discovered images into ``FLOW_UPLOAD_DIR/livetest_<ts>/`` and
submits worker-safe relative paths only. The staging dir must therefore be
visible to the worker process. If ``FLOW_UPLOAD_DIR`` is unset, the script uses
the same repo-root-aware default resolution as ``worker/dispatcher.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SERVER_URL = "http://localhost:8080"
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
POLL_INTERVAL_S = 30

# Camera presets validated server-side (server.models.job.CAMERA_PRESETS).
CAMERA_DIRECTIONS: tuple[str, str] = ("Dolly in", "Orbit right")

# Per-category indicative credit cost. These are rough reporting defaults only
# for the live harness output; adjust if Flow pricing changes.
CREDIT_ESTIMATE_PER_TYPE: dict[str, int] = {
    "text-to-video": 10,
    "text-to-image": 2,
    "frames-to-video": 10,
    "ingredients-to-video": 10,
    "extend-video": 10,
    "camera-move": 10,
    "insert-object": 10,
    "remove-object": 10,
}

# Image-supported extensions for asset discovery.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


logger = logging.getLogger("live_test_full_cate")


# ---------------------------------------------------------------------------
# HTTP client (stdlib only; the script must run with no extra deps)
# ---------------------------------------------------------------------------


class HttpError(RuntimeError):
    """Raised on non-2xx response from the FlowEngine server."""


@dataclass
class ApiClient:
    base_url: str
    api_key: Optional[str] = None
    timeout_s: float = 30.0

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self.base_url.rstrip('/')}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url=url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise HttpError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise HttpError(f"{method} {path} -> network error: {exc.reason}") from exc

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def get(self, path: str) -> Any:
        return self._request("GET", path)


# ---------------------------------------------------------------------------
# Job definitions
# ---------------------------------------------------------------------------


@dataclass
class SubmittedJob:
    job_id: str
    job_type: str
    level: int
    chain_name: str = "standalone"
    plan_ref: Optional[str] = None
    chain_id: Optional[str] = None
    parent_job_id: Optional[str] = None
    last_status: str = "pending"
    media_id: Optional[str] = None
    output_files: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class SubmissionPlan:
    jobs: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    staging_reports: list["StagingReport"] = field(default_factory=list)


@dataclass
class StagingReport:
    label: str
    source_dir: Path
    source_images: list[Path]
    upload_dir: Path
    staging_rel_dir: str
    staging_abs_dir: Path
    relative_paths: list[str]
    dry_run: bool = False


def _l1_payloads() -> list[dict[str, Any]]:
    """Always-on standalone L1 payloads: text-to-video x1, text-to-image x2."""
    return [
        {
            "type": "text-to-video",
            "prompt": "Macro shot of dew drops on a spider web at sunrise",
            "aspect_ratio": "16:9",
        },
        {
            "type": "text-to-image",
            "prompt": "A watercolor painting of a quiet harbour at golden hour",
            "aspect_ratio": "16:9",
        },
        {
            "type": "text-to-image",
            "prompt": "Photo-realistic portrait of an astronaut feeding a stray cat",
            "aspect_ratio": "16:9",
        },
    ]


def _frames_payloads(staged_paths: list[str]) -> list[dict[str, Any]]:
    """Two frames-to-video jobs using the first 1-2 staged upload paths."""
    if not staged_paths:
        return []
    start_a = staged_paths[0]
    end_a = staged_paths[1] if len(staged_paths) >= 2 else None
    return [
        {
            "type": "frames-to-video",
            "prompt": "Smooth dolly-in animating between the two frames",
            "aspect_ratio": "16:9",
            "start_image_path": start_a,
            **({"end_image_path": end_a} if end_a else {}),
        },
        {
            "type": "frames-to-video",
            "prompt": "Slow parallax pan over the scene with subtle wind",
            "aspect_ratio": "16:9",
            "start_image_path": start_a,
        },
    ]


def _ingredients_payloads(staged_paths: list[str]) -> list[dict[str, Any]]:
    """Two ingredients-to-video jobs using up to 3 staged upload paths."""
    if not staged_paths:
        return []
    refs = staged_paths[:3]
    return [
        {
            "type": "ingredients-to-video",
            "prompt": "Compose the reference subjects into one cohesive scene",
            "aspect_ratio": "16:9",
            "ingredient_image_paths": refs,
        },
        {
            "type": "ingredients-to-video",
            "prompt": "Dramatic action shot blending all referenced ingredients",
            "aspect_ratio": "16:9",
            "ingredient_image_paths": refs,
        },
    ]


def _plan_step(
    ref: str,
    chain_name: str,
    job_level: int,
    payload: dict[str, Any],
    *,
    parent_job_id: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "ref": ref,
        "chain_name": chain_name,
        "job_level": job_level,
        "parent_job_id": parent_job_id,
        "payload": dict(payload),
    }


def _l2_chain_payload() -> list[dict[str, Any]]:
    """Return the split L2 sweep blueprint, including the 4 L1 chain heads.

    The helper name is kept for diff stability. The old harness returned one
    linear L2 step list; the new harness returns a full blueprint with 4
    text-to-video chain roots (`job_level=1`, `parent_job_id=None`) followed by
    8 category-local L2 steps. `parent_job_id` is symbolic here and resolved to
    real job ids during submission.
    """
    bbox_a = {"x": 0.40, "y": 0.40, "w": 0.20, "h": 0.20}
    bbox_b = {"x": 0.55, "y": 0.30, "w": 0.18, "h": 0.22}
    return [
        _plan_step(
            "l1_t2v_extend",
            "extend",
            1,
            {
                "type": "text-to-video",
                "prompt": "A cinematic drone shot over neon-lit Tokyo streets at dusk",
                "aspect_ratio": "16:9",
            },
        ),
        _plan_step(
            "l2_extend_1",
            "extend",
            2,
            {
                "type": "extend-video",
                "prompt": "Continue the scene with the camera holding still",
            },
            parent_job_id="l1_t2v_extend",
        ),
        _plan_step(
            "l3_extend_2",
            "extend",
            3,
            {
                "type": "extend-video",
                "prompt": "Continue the scene as a slow push-in develops",
            },
            parent_job_id="l2_extend_1",
        ),
        _plan_step(
            "l4_extend_3",
            "extend",
            4,
            {
                "type": "extend-video",
                "prompt": "Hold on a tight close-up of the neon reflections",
            },
            parent_job_id="l3_extend_2",
        ),
        _plan_step(
            "l5_extend_4",
            "extend",
            5,
            {
                "type": "extend-video",
                "prompt": "Fade slowly to black as the camera drifts upward",
            },
            parent_job_id="l4_extend_3",
        ),
        _plan_step(
            "l1_t2v_camera",
            "camera",
            1,
            {
                "type": "text-to-video",
                "prompt": "Static wide shot of an art deco lobby with strong depth lines",
                "aspect_ratio": "16:9",
            },
        ),
        _plan_step(
            "l2_camera_1",
            "camera",
            2,
            {
                "type": "camera-move",
                "direction": CAMERA_DIRECTIONS[0],
            },
            parent_job_id="l1_t2v_camera",
        ),
        _plan_step(
            "l3_camera_2",
            "camera",
            3,
            {
                "type": "camera-move",
                "direction": CAMERA_DIRECTIONS[1],
            },
            parent_job_id="l2_camera_1",
        ),
        _plan_step(
            "l1_t2v_insert",
            "insert",
            1,
            {
                "type": "text-to-video",
                "prompt": "A minimalist loft interior with empty space near the center and upper-right",
                "aspect_ratio": "16:9",
            },
        ),
        _plan_step(
            "l2_insert_1",
            "insert",
            2,
            {
                "type": "insert-object",
                "prompt": "a small red origami crane",
                "bbox": bbox_a,
            },
            parent_job_id="l1_t2v_insert",
        ),
        _plan_step(
            "l3_insert_2",
            "insert",
            3,
            {
                "type": "insert-object",
                "prompt": "a glowing lantern hanging in the air",
                "bbox": bbox_b,
            },
            parent_job_id="l2_insert_1",
        ),
        _plan_step(
            "l1_t2v_remove",
            "remove",
            1,
            {
                "type": "text-to-video",
                "prompt": "A tabletop scene with a red mug at center and a paper lantern in the upper-right",
                "aspect_ratio": "16:9",
            },
        ),
        _plan_step(
            "l2_remove_1",
            "remove",
            2,
            {
                "type": "remove-object",
                "prompt": "remove the object in the box",
                "bbox": bbox_a,
            },
            parent_job_id="l1_t2v_remove",
        ),
        _plan_step(
            "l3_remove_2",
            "remove",
            3,
            {
                "type": "remove-object",
                "prompt": "remove the object in the box",
                "bbox": bbox_b,
            },
            parent_job_id="l2_remove_1",
        ),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_images(directory: Path) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        return []
    files: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
            files.append(entry.resolve())
    return files


def _env_dir(name: str) -> Optional[Path]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _resolve_repo_root() -> Path:
    """Return the shared repo root, including when running from a git worktree."""
    for base in Path(__file__).resolve().parents:
        git_marker = base / ".git"
        if git_marker.is_dir():
            return base
        if not git_marker.is_file():
            continue

        raw = git_marker.read_text(encoding="utf-8").strip()
        if not raw.startswith("gitdir:"):
            continue

        git_dir = Path(raw[7:].strip())
        if not git_dir.is_absolute():
            git_dir = (base / git_dir).resolve()

        if git_dir.parent.name == "worktrees":
            return git_dir.parents[2]
        return base

    return Path.cwd().resolve()


def _resolve_upload_dir() -> Path:
    """Match the worker's FLOW_UPLOAD_DIR resolution contract."""
    raw_value = (os.environ.get("FLOW_UPLOAD_DIR") or "").strip()
    if raw_value:
        return Path(raw_value).expanduser().resolve()
    return (_resolve_repo_root() / "uploads").resolve()


def _new_stage_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")


def _stage_test_images(
    source_dir: Path,
    *,
    label: str,
    upload_dir: Path,
    run_id: str,
    seen_relative_paths: set[str],
    dry_run: bool,
) -> Optional[StagingReport]:
    images = _list_images(source_dir)
    if not images:
        return None

    staging_rel_dir = f"livetest_{run_id}"
    staging_abs_dir = upload_dir / staging_rel_dir
    relative_paths: list[str] = []

    for image in images:
        relative_path = f"{staging_rel_dir}/{image.name}"
        relative_key = relative_path.lower()
        if relative_key in seen_relative_paths:
            raise RuntimeError(
                f"Duplicate staged upload target detected for {image.name!r}; "
                "ensure FLOW_TEST_* directories do not reuse basenames."
            )
        seen_relative_paths.add(relative_key)
        relative_paths.append(relative_path)

    if not dry_run:
        staging_abs_dir.mkdir(parents=True, exist_ok=True)
        for image in images:
            shutil.copy2(image, staging_abs_dir / image.name)

    return StagingReport(
        label=label,
        source_dir=source_dir,
        source_images=images,
        upload_dir=upload_dir,
        staging_rel_dir=staging_rel_dir,
        staging_abs_dir=staging_abs_dir,
        relative_paths=relative_paths,
        dry_run=dry_run,
    )


def _job_type_counts_from_plan(plan_jobs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in plan_jobs:
        job_type = step["payload"]["type"]
        counts[job_type] = counts.get(job_type, 0) + 1
    return counts


def _job_type_counts_from_completed(submitted: list[SubmittedJob]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in submitted:
        if job.last_status == "completed":
            counts[job.job_type] = counts.get(job.job_type, 0) + 1
    return counts


def _print_credit_tally(counts: dict[str, int], heading: str) -> None:
    grand = 0
    sys.stdout.write(f"\n=== {heading} ===\n")
    sys.stdout.write(f"  {'type':<22}  {'count':<5}  {'unit':<5}  est_total\n")
    for job_type, count in sorted(counts.items()):
        unit = CREDIT_ESTIMATE_PER_TYPE.get(job_type, 0)
        total = unit * count
        grand += total
        sys.stdout.write(f"  {job_type:<22}  {count:<5}  {unit:<5}  {total}\n")
    sys.stdout.write(f"  {'TOTAL':<22}  {'':<5}  {'':<5}  {grand}\n")
    sys.stdout.write(
        "  notes: L2 sweep now uses 4 separate text-to-video roots "
        "(extend/camera/insert/remove). Estimates are indicative only.\n"
    )
    sys.stdout.flush()


def _print_plan_table(plan_jobs: list[dict[str, Any]]) -> None:
    sys.stdout.write("\nDry-run submission plan:\n")
    sys.stdout.write(f"  {'level':<5}  {'chain':<10}  {'type':<22}  {'ref':<16}  parent\n")
    for step in plan_jobs:
        parent = step["parent_job_id"] or "-"
        sys.stdout.write(
            f"  L{step['job_level']:<4}  {step['chain_name']:<10}  "
            f"{step['payload']['type']:<22}  {step['ref']:<16}  {parent}\n"
        )
    sys.stdout.flush()


def _print_staging_plan(staging_reports: list[StagingReport]) -> None:
    if not staging_reports:
        return

    sys.stdout.write("\nDry-run staging plan:\n")
    for report in staging_reports:
        sys.stdout.write(
            f"  {report.label:<12} {len(report.relative_paths)} image(s) -> "
            f"{report.staging_abs_dir}{os.sep}\n"
        )
        for source, relative_path in zip(report.source_images, report.relative_paths, strict=True):
            sys.stdout.write(f"    {source.name} -> {relative_path}\n")
    sys.stdout.flush()


def _print_job_table(jobs: list[SubmittedJob]) -> None:
    sys.stdout.write("\nSubmitted jobs:\n")
    sys.stdout.write(f"  {'level':<5}  {'chain':<10}  {'type':<22}  {'job_id':<36}  parent\n")
    for job in jobs:
        parent = job.parent_job_id or "-"
        sys.stdout.write(
            f"  L{job.level:<4}  {job.chain_name:<10}  {job.job_type:<22}  "
            f"{job.job_id:<36}  {parent}\n"
        )
    sys.stdout.flush()


def build_submission_plan(
    *,
    skip_frames: bool,
    skip_ingredients: bool,
    dry_run: bool = False,
) -> SubmissionPlan:
    """Build the full submission plan with symbolic parent references.

    All four text-to-video chain heads are emitted before any L2 step so the
    worker can queue them as early as possible.
    """
    warnings: list[str] = []
    plan_jobs: list[dict[str, Any]] = []
    staging_reports: list[StagingReport] = []
    sweep = _l2_chain_payload()
    run_id = _new_stage_run_id()
    upload_dir = _resolve_upload_dir()
    seen_relative_paths: set[str] = set()

    # Pre-flight: submit all four L1 t2v chain heads before any L2 step.
    plan_jobs.extend(step for step in sweep if step["job_level"] == 1)

    standalone_payloads = _l1_payloads()
    standalone_refs = ("l1_t2v_baseline", "l1_t2i_a", "l1_t2i_b")
    for ref, payload in zip(standalone_refs, standalone_payloads, strict=True):
        plan_jobs.append(_plan_step(ref, "standalone", 1, payload))

    if skip_frames:
        warnings.append("Skipping frames-to-video jobs (--skip-frames).")
    else:
        frames_dir = _env_dir("FLOW_TEST_FRAMES_DIR")
        if not frames_dir:
            warnings.append(
                "FLOW_TEST_FRAMES_DIR not set; SKIPPING 2x frames-to-video. "
                "Set it to a directory with image files to include them."
            )
        else:
            staged = _stage_test_images(
                frames_dir,
                label="frames",
                upload_dir=upload_dir,
                run_id=run_id,
                seen_relative_paths=seen_relative_paths,
                dry_run=dry_run,
            )
            payloads = _frames_payloads(staged.relative_paths) if staged else []
            if not payloads:
                warnings.append(
                    f"FLOW_TEST_FRAMES_DIR={frames_dir} has no image files; "
                    "SKIPPING frames-to-video."
                )
            else:
                staging_reports.append(staged)
                for idx, payload in enumerate(payloads, start=1):
                    plan_jobs.append(_plan_step(f"l1_frames_{idx}", "frames", 1, payload))

    if skip_ingredients:
        warnings.append("Skipping ingredients-to-video jobs (--skip-ingredients).")
    else:
        ingredients_dir = _env_dir("FLOW_TEST_INGREDIENTS_DIR")
        if not ingredients_dir:
            warnings.append(
                "FLOW_TEST_INGREDIENTS_DIR not set; SKIPPING 2x ingredients-to-video."
            )
        else:
            staged = _stage_test_images(
                ingredients_dir,
                label="ingredients",
                upload_dir=upload_dir,
                run_id=run_id,
                seen_relative_paths=seen_relative_paths,
                dry_run=dry_run,
            )
            payloads = _ingredients_payloads(staged.relative_paths) if staged else []
            if not payloads:
                warnings.append(
                    f"FLOW_TEST_INGREDIENTS_DIR={ingredients_dir} has no image files; SKIPPING."
                )
            else:
                staging_reports.append(staged)
                for idx, payload in enumerate(payloads, start=1):
                    plan_jobs.append(
                        _plan_step(f"l1_ingredients_{idx}", "ingredients", 1, payload)
                    )

    plan_jobs.extend(step for step in sweep if step["job_level"] > 1)
    return SubmissionPlan(jobs=plan_jobs, warnings=warnings, staging_reports=staging_reports)


def _log_plan_warnings(plan: SubmissionPlan) -> None:
    for warning in plan.warnings:
        logger.warning(warning)


def _log_staging_reports(plan: SubmissionPlan) -> None:
    for report in plan.staging_reports:
        if report.dry_run:
            logger.info(
                "Dry run: would stage %d test images to %s/",
                len(report.relative_paths),
                report.staging_abs_dir,
            )
        else:
            logger.info(
                "Staged %d test images to %s/",
                len(report.relative_paths),
                report.staging_abs_dir,
            )


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def _submit_plan(api: ApiClient, plan: SubmissionPlan) -> list[SubmittedJob]:
    submitted: list[SubmittedJob] = []
    actual_ids: dict[str, str] = {}

    for step in plan.jobs:
        payload = dict(step["payload"])
        symbolic_parent = step["parent_job_id"]
        if symbolic_parent is not None:
            payload["parent_job_id"] = actual_ids[symbolic_parent]

        resp = api.post("/api/jobs", payload)
        job_id = resp["id"]
        actual_ids[step["ref"]] = job_id

        submitted.append(
            SubmittedJob(
                job_id=job_id,
                job_type=resp["type"],
                level=step["job_level"],
                chain_name=step["chain_name"],
                plan_ref=step["ref"],
                chain_id=resp.get("chain_id"),
                parent_job_id=payload.get("parent_job_id"),
            )
        )

        if payload.get("parent_job_id"):
            logger.info(
                "Submitted L%d %s [%s] -> %s (parent=%s)",
                step["job_level"],
                resp["type"],
                step["chain_name"],
                job_id,
                payload["parent_job_id"],
            )
        else:
            logger.info(
                "Submitted L1 %s [%s] -> %s",
                resp["type"],
                step["chain_name"],
                job_id,
            )

    return submitted


def submit_all(
    api: ApiClient,
    *,
    skip_frames: bool,
    skip_ingredients: bool,
) -> list[SubmittedJob]:
    """Submit the full L1+L2 sweep. Returns SubmittedJob list in submission order."""
    plan = build_submission_plan(
        skip_frames=skip_frames,
        skip_ingredients=skip_ingredients,
    )
    _log_plan_warnings(plan)
    _log_staging_reports(plan)
    return _submit_plan(api, plan)


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def poll_until_done(
    api: ApiClient,
    submitted: list[SubmittedJob],
    *,
    timeout_min: int,
) -> None:
    """Poll /api/jobs/<id> every POLL_INTERVAL_S until all terminal or timeout."""
    deadline = time.monotonic() + timeout_min * 60
    pending_ids = {job.job_id for job in submitted}
    by_id = {job.job_id: job for job in submitted}

    while pending_ids and time.monotonic() < deadline:
        for job_id in list(pending_ids):
            try:
                resp = api.get(f"/api/jobs/{job_id}")
            except HttpError as exc:
                logger.error("poll %s failed: %s", job_id, exc)
                continue

            status = resp.get("status", "unknown")
            local = by_id[job_id]
            local.last_status = status
            local.media_id = resp.get("media_id") or local.media_id
            local.output_files = resp.get("output_files") or local.output_files
            local.error = resp.get("error") or local.error

            if status in TERMINAL_STATUSES:
                pending_ids.discard(job_id)
                logger.info(
                    "TERMINAL %s [%s/%s] -> %s media_id=%s",
                    job_id,
                    local.chain_name,
                    local.job_type,
                    status,
                    local.media_id,
                )

        if not pending_ids:
            break

        counts: dict[str, int] = {}
        for job in submitted:
            counts[job.last_status] = counts.get(job.last_status, 0) + 1
        summary = " ".join(f"{status}={count}" for status, count in sorted(counts.items()))
        logger.info("poll | pending=%d | %s", len(pending_ids), summary)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(POLL_INTERVAL_S, remaining))

    if pending_ids:
        logger.warning(
            "Timeout reached after %d min; %d job(s) still non-terminal.",
            timeout_min,
            len(pending_ids),
        )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_final_summary(submitted: list[SubmittedJob]) -> None:
    sys.stdout.write("\n=== Final job summary ===\n")
    sys.stdout.write(
        f"{'level':<5}  {'chain':<10}  {'type':<22}  {'status':<10}  "
        f"{'media_id':<36}  files  error\n"
    )
    for job in submitted:
        files_count = len(job.output_files)
        err = (job.error or "")[:50]
        sys.stdout.write(
            f"L{job.level:<4}  {job.chain_name:<10}  {job.job_type:<22}  "
            f"{job.last_status:<10}  {(job.media_id or '-'): <36}  "
            f"{files_count:<5}  {err}\n"
        )

    _print_credit_tally(
        _job_type_counts_from_completed(submitted),
        "Estimated credit tally",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="live_test_full_cate",
        description="Submit FlowEngine full-category live test sweep and poll to completion.",
    )
    parser.add_argument(
        "--server",
        default=os.environ.get("SERVER_URL", DEFAULT_SERVER_URL),
        help=f"FlowEngine server base URL (default: $SERVER_URL or {DEFAULT_SERVER_URL})",
    )
    parser.add_argument(
        "--skip-frames",
        action="store_true",
        help="Skip frames-to-video jobs even if FLOW_TEST_FRAMES_DIR is set.",
    )
    parser.add_argument(
        "--skip-ingredients",
        action="store_true",
        help="Skip ingredients-to-video jobs even if FLOW_TEST_INGREDIENTS_DIR is set.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned chain shape and credit tally without submitting jobs.",
    )
    parser.add_argument(
        "--timeout-min",
        type=int,
        default=60,
        help="Polling timeout in minutes (default: 60).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity for the polling loop (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("Server: %s", args.server)

    plan = build_submission_plan(
        skip_frames=args.skip_frames,
        skip_ingredients=args.skip_ingredients,
        dry_run=args.dry_run,
    )
    _log_plan_warnings(plan)
    _log_staging_reports(plan)

    if args.dry_run:
        logger.info("Dry run enabled; no HTTP requests will be sent.")
        _print_staging_plan(plan.staging_reports)
        _print_plan_table(plan.jobs)
        _print_credit_tally(
            _job_type_counts_from_plan(plan.jobs),
            "Estimated credit tally (planned jobs)",
        )
        return 0

    api = ApiClient(
        base_url=args.server,
        api_key=os.environ.get("FLOW_API_KEY") or None,
    )

    try:
        submitted = _submit_plan(api, plan)
    except HttpError as exc:
        logger.error("Submission failed: %s", exc)
        return 2

    _print_job_table(submitted)

    try:
        poll_until_done(api, submitted, timeout_min=args.timeout_min)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user; printing partial summary.")

    print_final_summary(submitted)

    failures = sum(1 for job in submitted if job.last_status == "failed")
    non_terminal = sum(1 for job in submitted if job.last_status not in TERMINAL_STATUSES)
    if failures or non_terminal:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
