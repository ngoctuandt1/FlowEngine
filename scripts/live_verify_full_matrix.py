#!/usr/bin/env python3
"""Live verify cate x mode timing/success matrix.

Dry-run is safe for CI and unit tests. Live mode needs a valid Google Flow
Chrome profile. The harness writes archive JSON to ``tests/live_runs`` and
copies downloaded outputs under ``downloads/matrix_<ts>/<cate>/<mode>/``.

Reference-image cates (``i2i``, ``f2v``, ``i2v``) use
``tests/fixtures/i2i_ref.png``. Live mode creates a 1x1 PNG placeholder there
when the file is missing so the repo does not need to carry binary fixtures.

Mode env semantics:
- ``ui`` sets all ``FLOW_<CATE>_VIA_REVERSE`` and ``FLOW_<CATE>_FORCE_REVERSE``
  gates to ``0``.
- ``hybrid`` sets only target ``FLOW_<CATE>_VIA_REVERSE=1``; engine may fall
  back to UI on reverse-API failure.
- ``revapi`` also sets target ``FLOW_<CATE>_FORCE_REVERSE=1``. Wave 2 engine
  work will honor this as hard-fail-on-UI-fallback; until then it behaves like
  hybrid for cates that ignore the force env.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ALL_CATES = ("t2v", "t2i", "i2i", "f2v", "i2v", "extend", "camera", "insert", "remove")
ALL_MODES = ("ui", "hybrid", "revapi")
L2_CATES = frozenset(("extend", "camera", "insert", "remove"))
REF_IMAGE_REL = Path("tests") / "fixtures" / "i2i_ref.png"
DEFAULT_PROMPT = "a calm river running through a misty forest at dawn"
DEFAULT_BBOX = {"x": 0.35, "y": 0.35, "w": 0.3, "h": 0.3}
ONE_BY_ONE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


Operation = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class OperationSpec:
    module: str
    function: str
    job_type: str
    l2: bool = False


OPERATION_SPECS: dict[str, OperationSpec] = {
    "t2v": OperationSpec("flow.operations.generate", "text_to_video", "text-to-video"),
    "t2i": OperationSpec("flow.operations.image", "text_to_image", "text-to-image"),
    "i2i": OperationSpec("flow.operations.image", "text_to_image", "image-to-image"),
    "f2v": OperationSpec("flow.operations.frames_to_video", "frames_to_video", "frames-to-video"),
    "i2v": OperationSpec("flow.operations.ingredients", "ingredients_to_video", "ingredients-to-video"),
    "extend": OperationSpec("flow.operations.extend", "extend_video", "extend-video", l2=True),
    "camera": OperationSpec("flow.operations.camera", "camera_move", "camera-move", l2=True),
    "insert": OperationSpec("flow.operations.insert", "insert_object", "insert-object", l2=True),
    "remove": OperationSpec("flow.operations.remove", "remove_object", "remove-object", l2=True),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a 9-cate x 3-mode Flow timing/success matrix.",
    )
    parser.add_argument("--profile", required=True, help="Chrome profile name")
    parser.add_argument("--depth", type=int, default=5, help="L2+ chain depth including L1 seed")
    parser.add_argument(
        "--cates",
        default=",".join(ALL_CATES),
        help="Comma-separated cates: " + ",".join(ALL_CATES),
    )
    parser.add_argument(
        "--modes",
        default=",".join(ALL_MODES),
        help="Comma-separated modes: " + ",".join(ALL_MODES),
    )
    parser.add_argument(
        "--cooldown-sec",
        type=float,
        default=30.0,
        help="Sleep between live chains; skipped in dry-run",
    )
    parser.add_argument(
        "--stitch",
        action="store_true",
        help="Concat completed per-level clips into _chain_stitched.mp4 after each live chain",
    )
    parser.add_argument("--dry-run", action="store_true", help="Plan matrix without Flow calls")
    return parser


def parse_csv(value: str, allowed: tuple[str, ...], *, label: str) -> list[str]:
    items = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError(f"--{label} must not be empty")
    unknown = [item for item in items if item not in allowed]
    if unknown:
        raise ValueError(f"unknown {label}: {', '.join(unknown)}")
    return items


def validate_depth(depth: int) -> None:
    if depth < 1:
        raise ValueError("--depth must be >= 1")


def env_names(cate: str) -> tuple[str, str]:
    upper = cate.upper()
    return f"FLOW_{upper}_VIA_REVERSE", f"FLOW_{upper}_FORCE_REVERSE"


def configure_mode_env(cate: str, mode: str) -> dict[str, str]:
    if cate not in OPERATION_SPECS:
        raise ValueError(f"unknown cate: {cate}")
    if mode not in ALL_MODES:
        raise ValueError(f"unknown mode: {mode}")

    for known_cate in ALL_CATES:
        via_key, force_key = env_names(known_cate)
        os.environ[via_key] = "0"
        os.environ[force_key] = "0"

    via_key, force_key = env_names(cate)
    if mode in ("hybrid", "revapi"):
        os.environ[via_key] = "1"
    if mode == "revapi":
        os.environ[force_key] = "1"

    return {
        "via_key": via_key,
        "via_value": os.environ[via_key],
        "force_key": force_key,
        "force_value": os.environ[force_key],
    }


def load_operation(cate: str) -> Operation:
    spec = OPERATION_SPECS[cate]
    module = importlib.import_module(spec.module)
    return getattr(module, spec.function)


def get_flow_client_class():
    from flow.client import FlowClient

    return FlowClient


def ensure_reference_image() -> Path:
    path = REPO_ROOT / REF_IMAGE_REL
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(ONE_BY_ONE_PNG))
    return path


def level_count_for(cate: str, depth: int) -> int:
    return depth if cate in L2_CATES else 1


def build_op_plan(cate: str, depth: int) -> list[dict[str, Any]]:
    validate_depth(depth)
    total = level_count_for(cate, depth)
    ops: list[dict[str, Any]] = []
    for index in range(total):
        level = index + 1
        op_cate = "t2v" if cate in L2_CATES and level == 1 else cate
        spec = OPERATION_SPECS[op_cate]
        ops.append(
            {
                "level": level,
                "cate": op_cate,
                "job_type": spec.job_type,
                "status": "planned",
                "wall_sec": 0.0,
                "media_id": "",
                "path": "",
            }
        )
    return ops


def _safe_media_id(media_id: str | None) -> str:
    safe = "".join(ch for ch in str(media_id or "") if ch.isalnum() or ch in "-_")
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
    suffix = src.suffix or ".mp4"
    dest = download_dir / f"L{level}_{_safe_media_id(media_id)}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return ""
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return str(dest)


def build_seed_job(*, profile: str, level: int, previous: dict[str, Any], cate: str, ts: int) -> dict[str, Any]:
    spec = OPERATION_SPECS[cate]
    return {
        "id": f"matrix-{profile}-{cate}-L{level}-{ts}",
        "type": spec.job_type,
        "job_level": level,
        "parent_job_id": f"matrix-{profile}-{cate}-L{level - 1}-{ts}",
        "project_url": previous.get("project_url"),
        "edit_url": previous.get("edit_url"),
        "media_id": previous.get("media_id"),
        "profile": profile,
    }


async def call_l1_operation(operation: Operation, cate: str, client) -> dict[str, Any]:
    prompt = DEFAULT_PROMPT
    if cate == "t2v":
        return await operation(client, prompt=prompt, free_mode=True)
    if cate == "t2i":
        return await operation(client, prompt=prompt)
    if cate == "i2i":
        return await operation(client, prompt=prompt, ref_image_path=str(ensure_reference_image()))
    if cate == "f2v":
        return await operation(
            client,
            prompt=prompt,
            start_image_path=str(ensure_reference_image()),
            free_mode=True,
        )
    if cate == "i2v":
        return await operation(
            client,
            prompt=prompt,
            ingredient_image_paths=[str(ensure_reference_image())],
            free_mode=True,
        )
    raise ValueError(f"not an L1 cate: {cate}")


async def call_l2_operation(
    operation: Operation,
    cate: str,
    client,
    *,
    job: dict[str, Any],
) -> dict[str, Any]:
    if cate == "extend":
        return await operation(client, job=job, prompt="continue the shot with gentle motion", free_mode=True)
    if cate == "camera":
        return await operation(client, job=job, direction="Dolly in")
    if cate == "insert":
        return await operation(client, job=job, prompt="a small red balloon", bbox=DEFAULT_BBOX)
    if cate == "remove":
        return await operation(client, job=job, bbox=DEFAULT_BBOX)
    raise ValueError(f"not an L2 cate: {cate}")


def chain_ok_label(chain: dict[str, Any]) -> str:
    prefix = "plan" if chain.get("dry_run") else "L"
    return f"{prefix}{chain['success_count']}/{chain['total_count']}"


def first_error(ops: list[dict[str, Any]]) -> str | None:
    for op in ops:
        if op.get("status") == "failed":
            return str(op.get("error") or "failed")
    return None


def finalize_chain(
    *,
    profile: str,
    cate: str,
    mode: str,
    depth: int,
    ts: int,
    env: dict[str, str],
    ops: list[dict[str, Any]],
    download_dir: Path,
    started_at: float,
    dry_run: bool,
    stitch: bool = False,
) -> dict[str, Any]:
    total_count = len(ops)
    success_count = total_count if dry_run else sum(1 for op in ops if op.get("status") == "completed")
    total_wall = 0.0 if dry_run else time.time() - started_at
    chain = {
        "profile": profile,
        "cate": cate,
        "mode": mode,
        "depth": depth,
        "dry_run": dry_run,
        "env": env,
        "download_dir": str(download_dir),
        "ops": ops,
        "total_wall_sec": round(total_wall, 3),
        "success_count": success_count,
        "total_count": total_count,
        "first_error": first_error(ops),
        "ts": ts,
    }
    if stitch and not dry_run:
        chain["stitch"] = stitch_completed_ops(ops, download_dir)
    elif stitch:
        chain["stitch"] = {"enabled": True, "ok": True, "dry_run": True}
    else:
        chain["stitch"] = {"enabled": False}
    chain["ok_label"] = chain_ok_label(chain)
    return chain


def stitch_completed_ops(ops: list[dict[str, Any]], download_dir: Path) -> dict[str, Any]:
    paths = [Path(op["path"]) for op in ops if op.get("status") == "completed" and op.get("path")]
    if len(paths) != len(ops):
        return {"enabled": True, "ok": False, "error": "not all ops have completed clip paths"}
    try:
        from flow.operations._chain_stitch import stitch_chain_clips

        stitched_path = stitch_chain_clips(paths, download_dir / "_chain_stitched.mp4")
    except Exception as exc:
        return {"enabled": True, "ok": False, "error": str(exc)}
    return {"enabled": True, "ok": True, "path": str(stitched_path)}


async def run_dry_chain(
    *, profile: str, cate: str, mode: str, depth: int, ts: int, stitch: bool = False
) -> dict[str, Any]:
    env = configure_mode_env(cate, mode)
    download_dir = REPO_ROOT / "downloads" / f"matrix_{ts}" / cate / mode
    ops = build_op_plan(cate, depth)
    return finalize_chain(
        profile=profile,
        cate=cate,
        mode=mode,
        depth=depth,
        ts=ts,
        env=env,
        ops=ops,
        download_dir=download_dir,
        started_at=time.time(),
        dry_run=True,
        stitch=stitch,
    )


async def run_live_chain(
    *, profile: str, cate: str, mode: str, depth: int, ts: int, stitch: bool = False
) -> dict[str, Any]:
    env = configure_mode_env(cate, mode)
    download_dir = REPO_ROOT / "downloads" / f"matrix_{ts}" / cate / mode
    download_dir.mkdir(parents=True, exist_ok=True)
    profile_base_dir = os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")
    ops = build_op_plan(cate, depth)
    started_at = time.time()
    FlowClient = get_flow_client_class()

    client = FlowClient(
        profile_name=profile,
        profile_base_dir=profile_base_dir,
        download_dir=str(download_dir),
    )
    async with client:
        client._job_id = f"matrix-{profile}-{cate}-{mode}-{ts}"
        previous: dict[str, Any] | None = None
        for op in ops:
            level = int(op["level"])
            op_cate = str(op["cate"])
            operation = load_operation(op_cate)
            op_started = time.time()
            try:
                if op_cate in L2_CATES:
                    if not previous:
                        raise RuntimeError(f"{op_cate} requires previous media")
                    job = build_seed_job(
                        profile=profile,
                        level=level,
                        previous=previous,
                        cate=op_cate,
                        ts=ts,
                    )
                    result = await call_l2_operation(operation, op_cate, client, job=job)
                else:
                    result = await call_l1_operation(operation, op_cate, client)
            except Exception as exc:
                op.update(
                    {
                        "status": "failed",
                        "wall_sec": round(time.time() - op_started, 3),
                        "error": str(exc),
                    }
                )
                break

            output_files = result.get("output_files") or []
            path = materialize_output_file(
                level=level,
                media_id=result.get("media_id"),
                output_files=output_files,
                download_dir=download_dir,
            )
            op.update(
                {
                    "status": "completed",
                    "wall_sec": round(time.time() - op_started, 3),
                    "media_id": result.get("media_id") or "",
                    "project_url": result.get("project_url") or "",
                    "edit_url": result.get("edit_url") or "",
                    "generation_id": result.get("generation_id") or "",
                    "output_files": output_files,
                    "path": path,
                }
            )
            previous = result

    return finalize_chain(
        profile=profile,
        cate=cate,
        mode=mode,
        depth=depth,
        ts=ts,
        env=env,
        ops=ops,
        download_dir=download_dir,
        started_at=started_at,
        dry_run=False,
        stitch=stitch,
    )


def best_mode_for(cate_results: dict[str, dict[str, Any]], modes: list[str], *, dry_run: bool) -> str:
    if dry_run:
        return "-"
    full_success = [result for result in cate_results.values() if result["success_count"] == result["total_count"]]
    candidates = full_success or list(cate_results.values())
    if not candidates:
        return "-"
    best = min(candidates, key=lambda result: (-result["success_count"], result["total_wall_sec"]))
    return str(best["mode"])


def build_matrix(chains: list[dict[str, Any]], cates: list[str], modes: list[str], *, dry_run: bool) -> dict[str, Any]:
    matrix: dict[str, Any] = {}
    by_key = {(chain["cate"], chain["mode"]): chain for chain in chains}
    for cate in cates:
        cate_results = {mode: by_key[(cate, mode)] for mode in modes if (cate, mode) in by_key}
        matrix[cate] = {
            "modes": cate_results,
            "best_mode": best_mode_for(cate_results, modes, dry_run=dry_run),
        }
    return matrix


def format_wall(chain: dict[str, Any] | None) -> str:
    if not chain:
        return "-"
    return f"{int(round(float(chain.get('total_wall_sec') or 0)))}s"


def format_ok(chain: dict[str, Any] | None) -> str:
    if not chain:
        return "-"
    return str(chain.get("ok_label") or "-")


def print_matrix_table(matrix: dict[str, Any], modes: list[str]) -> None:
    headers = ["Cate"]
    for mode in ALL_MODES:
        if mode in modes:
            label = "UI" if mode == "ui" else "Hybrid" if mode == "hybrid" else "RevAPI"
            headers.extend([f"{label} wall", f"{label} ok"])
    headers.append("Best mode")

    rows: list[list[str]] = []
    for cate, entry in matrix.items():
        mode_results = entry["modes"]
        row = [cate]
        for mode in ALL_MODES:
            if mode in modes:
                row.extend([format_wall(mode_results.get(mode)), format_ok(mode_results.get(mode))])
        row.append(str(entry["best_mode"]))
        rows.append(row)

    widths = [len(header) for header in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths)) + " |"

    print(render(headers))
    print("|" + "|".join("-" * (width + 2) for width in widths) + "|")
    for row in rows:
        print(render(row))


def write_json_summary(summary: dict[str, Any], ts: int) -> Path:
    out_dir = REPO_ROOT / "tests" / "live_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}_matrix.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


async def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    try:
        cates = parse_csv(args.cates, ALL_CATES, label="cates")
        modes = parse_csv(args.modes, ALL_MODES, label="modes")
        validate_depth(args.depth)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2, {}

    ts = int(time.time())
    print(
        f"{'DRY-RUN' if args.dry_run else 'LIVE'} matrix: "
        f"profile={args.profile} depth={args.depth} cates={','.join(cates)} modes={','.join(modes)}"
    )

    chains: list[dict[str, Any]] = []
    total = len(cates) * len(modes)
    completed = 0
    for cate in cates:
        for mode in modes:
            runner = run_dry_chain if args.dry_run else run_live_chain
            chain = await runner(
                profile=args.profile,
                cate=cate,
                mode=mode,
                depth=args.depth,
                ts=ts,
                stitch=args.stitch,
            )
            chains.append(chain)
            completed += 1
            if not args.dry_run and completed < total and args.cooldown_sec > 0:
                await asyncio.sleep(args.cooldown_sec)

    matrix = build_matrix(chains, cates, modes, dry_run=args.dry_run)
    print_matrix_table(matrix, modes)

    summary = {
        "ts": ts,
        "profile": args.profile,
        "depth": args.depth,
        "cates": cates,
        "modes": modes,
        "dry_run": args.dry_run,
        "chains": chains,
        "matrix": matrix,
        "all_pass": all(chain["success_count"] == chain["total_count"] for chain in chains),
    }
    out_path = write_json_summary(summary, ts)
    summary["json_path"] = str(out_path)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON: {out_path}")

    return (0 if summary["all_pass"] else 1), summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    code, _summary = asyncio.run(run(args))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
