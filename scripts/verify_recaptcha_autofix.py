"""Simulated verification for the reCAPTCHA burn-and-replace autofix.

Usage:
    python scripts/verify_recaptcha_autofix.py [--dry-run]

The verification is intentionally offline-only. It replays hand-crafted
network calls into the real `flow.recaptcha`, `flow.wait`, and
`worker.dispatcher` code paths while mocking the profile-swap warm step.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import flow.recaptcha as recaptcha_module
import flow.wait as wait_module
import worker.dispatcher as dispatcher_module
import worker.profile_manager as profile_manager_module
from worker.project_lock import ProjectLock

OLD_PROFILE = "oldprofile"
NEW_PROFILE = "newprofile_test"
JOB_ID = "job-rec-autofix-verify"
JOB_TYPE = "text-to-video"
FLOW_URL = "https://labs.google/fx/tools/flow/project/test/edit/media"


@dataclass(frozen=True)
class ScenarioSpec:
    key: str
    description: str
    calls: tuple[dict, ...]
    expect_network_kind: str | None
    expect_wait_kind: str | None
    expect_wait_error: str | None
    auto_replace_enabled: bool
    swap_return: str | None
    expect_dispatch_error: str
    expect_swap_calls: tuple[str, ...]
    expect_replace_args: tuple[str, str] | None
    expect_remove_profile: str | None
    expect_available_profiles: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioOutcome:
    spec: ScenarioSpec
    dry_run: bool
    network_kind: str | None
    wait_kind: str | None
    wait_url: str | None
    wait_result: dict | None
    dispatch_result: dict
    swap_calls: tuple[str, ...]
    swapper_init_args: tuple[tuple[Path, Path], ...]
    replace_calls: tuple[tuple[str, str], ...]
    remove_calls: tuple[tuple[str], ...]
    available_profiles: tuple[str, ...]


@dataclass(frozen=True)
class AssertionResult:
    name: str
    passed: bool
    detail: str = ""


def build_recaptcha_v3_calls(now: float | None = None) -> list[dict]:
    ts = time.time() if now is None else now
    return [
        {
            "url": "https://www.google.com/recaptcha/enterprise/clr?k=ABC",
            "status": 200,
            "method": "GET",
            "ts": ts - 2,
        },
        {
            "url": "https://www.google.com/recaptcha/enterprise/reload?k=ABC",
            "status": 200,
            "method": "GET",
            "ts": ts - 1,
        },
        {
            "url": "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
            "status": 403,
            "method": "POST",
            "ts": ts,
        },
    ]


def build_blocked_403_only_calls(now: float | None = None) -> list[dict]:
    ts = time.time() if now is None else now
    return [
        {
            "url": "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
            "status": 403,
            "method": "POST",
            "ts": ts,
        }
    ]


def build_recaptcha_only_calls(now: float | None = None) -> list[dict]:
    ts = time.time() if now is None else now
    return [
        {
            "url": "https://www.google.com/recaptcha/enterprise/clr?k=ABC",
            "status": 200,
            "method": "GET",
            "ts": ts,
        }
    ]


def scenario_specs(now: float | None = None) -> dict[str, ScenarioSpec]:
    return {
        "A": ScenarioSpec(
            key="A",
            description="403 + recaptcha clr/reload -> autofix swap fires",
            calls=tuple(build_recaptcha_v3_calls(now)),
            expect_network_kind="v3_invisible",
            expect_wait_kind="v3_invisible",
            expect_wait_error=None,
            auto_replace_enabled=True,
            swap_return=NEW_PROFILE,
            expect_dispatch_error="recaptcha_v3_invisible_burned_oldprofile",
            expect_swap_calls=(OLD_PROFILE,),
            expect_replace_args=(OLD_PROFILE, NEW_PROFILE),
            expect_remove_profile=None,
            expect_available_profiles=(NEW_PROFILE,),
        ),
        "B": ScenarioSpec(
            key="B",
            description="403 alone -> blocked_403 path, no swap",
            calls=tuple(build_blocked_403_only_calls(now)),
            expect_network_kind=None,
            expect_wait_kind=None,
            expect_wait_error="blocked_403",
            auto_replace_enabled=True,
            swap_return=NEW_PROFILE,
            expect_dispatch_error="blocked_403",
            expect_swap_calls=(),
            expect_replace_args=None,
            expect_remove_profile=None,
            expect_available_profiles=(OLD_PROFILE,),
        ),
        "C": ScenarioSpec(
            key="C",
            description="recaptcha clr alone -> no trigger",
            calls=tuple(build_recaptcha_only_calls(now)),
            expect_network_kind=None,
            expect_wait_kind=None,
            expect_wait_error="timeout",
            auto_replace_enabled=True,
            swap_return=NEW_PROFILE,
            expect_dispatch_error="timeout",
            expect_swap_calls=(),
            expect_replace_args=None,
            expect_remove_profile=None,
            expect_available_profiles=(OLD_PROFILE,),
        ),
        "D": ScenarioSpec(
            key="D",
            description="auto-replace disabled -> remove burned profile",
            calls=tuple(build_recaptcha_v3_calls(now)),
            expect_network_kind="v3_invisible",
            expect_wait_kind="v3_invisible",
            expect_wait_error=None,
            auto_replace_enabled=False,
            swap_return=NEW_PROFILE,
            expect_dispatch_error="recaptcha_v3_invisible_burned_oldprofile",
            expect_swap_calls=(),
            expect_replace_args=None,
            expect_remove_profile=OLD_PROFILE,
            expect_available_profiles=(),
        ),
        "E": ScenarioSpec(
            key="E",
            description="swap pool exhausted -> remove burned profile",
            calls=tuple(build_recaptcha_v3_calls(now)),
            expect_network_kind="v3_invisible",
            expect_wait_kind="v3_invisible",
            expect_wait_error=None,
            auto_replace_enabled=True,
            swap_return=None,
            expect_dispatch_error="recaptcha_v3_invisible_burned_oldprofile",
            expect_swap_calls=(OLD_PROFILE,),
            expect_replace_args=None,
            expect_remove_profile=OLD_PROFILE,
            expect_available_profiles=(),
        ),
    }


def _make_job() -> dict:
    return {
        "id": JOB_ID,
        "type": JOB_TYPE,
        "profile": OLD_PROFILE,
        "job_level": 1,
    }


def _make_client(calls: tuple[dict, ...], *, job_id: str = JOB_ID):
    return SimpleNamespace(
        page=SimpleNamespace(url=FLOW_URL),
        _calls=[dict(call) for call in calls],
        _video_urls=[],
        _media_id_events=[],
        _job_id=job_id,
    )


class _MonotonicClock:
    def __init__(self, *, start: float = 0.0, step: float = 0.5) -> None:
        self._value = start - step
        self._step = step

    def __call__(self) -> float:
        self._value += self._step
        return self._value


async def _no_sleep(_seconds: float) -> None:
    return None


async def _passthrough_message_with_capture(client, kind, message, *, extra=None) -> str:
    del client, kind, extra
    return message


async def _run_wait_once(calls: tuple[dict, ...], *, job_id: str = JOB_ID) -> dict:
    client = _make_client(calls, job_id=job_id)
    clock = _MonotonicClock()

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(wait_module, "_inject_observer", AsyncMock(return_value=None))
        )
        stack.enter_context(
            patch.object(
                wait_module,
                "_read_observer",
                AsyncMock(
                    return_value={"progress": 0, "error": "", "new_video": False}
                ),
            )
        )
        stack.enter_context(
            patch.object(wait_module, "detect_recaptcha", AsyncMock(return_value=False))
        )
        stack.enter_context(
            patch.object(
                wait_module,
                "capture_failure_nonblocking",
                AsyncMock(return_value=None),
            )
        )
        stack.enter_context(
            patch.object(
                wait_module,
                "message_with_failure_capture",
                new=_passthrough_message_with_capture,
            )
        )
        stack.enter_context(patch.object(wait_module.asyncio, "sleep", new=_no_sleep))
        stack.enter_context(patch.object(wait_module.time, "monotonic", new=clock))
        return await wait_module.wait_for_completion(
            client,
            job_type="extend-video",
            timeout=1,
        )


def _make_wait_backed_handler(calls: tuple[dict, ...]):
    async def _handler(job: dict) -> dict:
        result = await _run_wait_once(calls, job_id=str(job["id"]))
        if result.get("done"):
            return {"status": "completed", "profile": job.get("profile", "")}

        error = str(result.get("error") or "unknown")
        return {
            "status": "failed",
            "error": error,
            "error_message": error,
        }

    return _handler


async def _run_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


async def simulate_scenario(
    spec: ScenarioSpec,
    *,
    dry_run: bool = False,
    job: dict | None = None,
) -> ScenarioOutcome:
    job_payload = dict(job or _make_job())
    network_kind = await recaptcha_module.detect_recaptcha_in_network(
        _make_client(spec.calls, job_id=str(job_payload["id"]))
    )

    wait_kind: str | None = None
    wait_url: str | None = None
    wait_result: dict | None = None
    try:
        wait_result = await _run_wait_once(spec.calls, job_id=str(job_payload["id"]))
    except recaptcha_module.RecaptchaError as exc:
        wait_kind = getattr(exc, "kind", None)
        wait_url = getattr(exc, "url", None)

    swap_calls: list[str] = []
    swapper_init_args: list[tuple[Path, Path]] = []
    temp_dir = tempfile.TemporaryDirectory()
    try:
        temp_root = Path(temp_dir.name)
        profile_base_dir = temp_root / "chrome-profiles"
        credentials_file = temp_root / "profiles_ultra.txt"
        profile_base_dir.mkdir(parents=True, exist_ok=True)
        credentials_file.write_text("# simulated\n", encoding="utf-8")

        class FakeProfileSwapper:
            def __init__(self, profile_base_dir: Path, credentials_file: Path) -> None:
                swapper_init_args.append((profile_base_dir, credentials_file))

            def swap_burned(self, old_name: str) -> str | None:
                swap_calls.append(old_name)
                return spec.swap_return

        fake_profile_swapper_module = ModuleType("worker.profile_swapper")
        fake_profile_swapper_module.ProfileSwapper = FakeProfileSwapper

        profile_manager = profile_manager_module.ProfileManager(
            chrome_user_data_dir=str(profile_base_dir),
            profile_names=[job_payload["profile"]],
        )
        replace_spy = Mock(wraps=profile_manager.replace_profile)
        remove_spy = Mock(wraps=profile_manager.remove_profile)
        profile_manager.replace_profile = replace_spy
        profile_manager.remove_profile = remove_spy

        env_updates = {
            "CHROME_USER_DATA_DIR": str(profile_base_dir),
            "FLOW_PROFILE_LIST_FILE": str(credentials_file),
            "FLOW_AUTO_REPLACE_PROFILES": "1" if spec.auto_replace_enabled else "0",
        }
        handler = _make_wait_backed_handler(spec.calls)

        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env_updates, clear=False))
            stack.enter_context(
                patch.dict(dispatcher_module.HANDLER_MAP, {JOB_TYPE: handler}, clear=False)
            )
            stack.enter_context(
                patch.dict(
                    sys.modules,
                    {"worker.profile_swapper": fake_profile_swapper_module},
                    clear=False,
                )
            )
            stack.enter_context(
                patch.object(dispatcher_module.asyncio, "to_thread", new=_run_to_thread)
            )
            dispatch_result = await dispatcher_module.dispatch_job(
                job_payload,
                profile_manager,
                ProjectLock(),
            )
    finally:
        temp_dir.cleanup()

    return ScenarioOutcome(
        spec=spec,
        dry_run=dry_run,
        network_kind=network_kind,
        wait_kind=wait_kind,
        wait_url=wait_url,
        wait_result=wait_result,
        dispatch_result=dispatch_result,
        swap_calls=tuple(swap_calls),
        swapper_init_args=tuple(swapper_init_args),
        replace_calls=tuple(tuple(call.args) for call in replace_spy.call_args_list),
        remove_calls=tuple(tuple(call.args) for call in remove_spy.call_args_list),
        available_profiles=tuple(sorted(profile_manager.get_available())),
    )


def evaluate_outcome(outcome: ScenarioOutcome) -> list[AssertionResult]:
    spec = outcome.spec
    dispatch_error = str(
        outcome.dispatch_result.get("error_message")
        or outcome.dispatch_result.get("error")
        or ""
    )

    results = [
        AssertionResult(
            name=f"{spec.key}: network detection",
            passed=outcome.network_kind == spec.expect_network_kind,
            detail=f"expected={spec.expect_network_kind!r} actual={outcome.network_kind!r}",
        ),
        AssertionResult(
            name=f"{spec.key}: dispatcher marks job failed",
            passed=outcome.dispatch_result.get("status") == "failed",
            detail=f"actual={outcome.dispatch_result.get('status')!r}",
        ),
        AssertionResult(
            name=f"{spec.key}: dispatcher error path",
            passed=dispatch_error == spec.expect_dispatch_error,
            detail=f"expected={spec.expect_dispatch_error!r} actual={dispatch_error!r}",
        ),
        AssertionResult(
            name=f"{spec.key}: swap calls",
            passed=outcome.swap_calls == spec.expect_swap_calls,
            detail=f"expected={spec.expect_swap_calls!r} actual={outcome.swap_calls!r}",
        ),
        AssertionResult(
            name=f"{spec.key}: available profiles after dispatch",
            passed=outcome.available_profiles == spec.expect_available_profiles,
            detail=(
                f"expected={spec.expect_available_profiles!r} "
                f"actual={outcome.available_profiles!r}"
            ),
        ),
    ]

    if spec.expect_wait_kind is not None:
        results.append(
            AssertionResult(
                name=f"{spec.key}: wait raises RecaptchaError",
                passed=outcome.wait_kind == spec.expect_wait_kind,
                detail=f"expected={spec.expect_wait_kind!r} actual={outcome.wait_kind!r}",
            )
        )
        results.append(
            AssertionResult(
                name=f"{spec.key}: wait preserves recaptcha URL",
                passed="recaptcha" in str(outcome.wait_url or "").lower(),
                detail=f"actual={outcome.wait_url!r}",
            )
        )
    else:
        wait_error = None if outcome.wait_result is None else outcome.wait_result.get("error")
        results.append(
            AssertionResult(
                name=f"{spec.key}: wait returns non-recaptcha error",
                passed=wait_error == spec.expect_wait_error,
                detail=f"expected={spec.expect_wait_error!r} actual={wait_error!r}",
            )
        )

    if spec.expect_replace_args is not None:
        results.append(
            AssertionResult(
                name=f"{spec.key}: replace_profile called",
                passed=outcome.replace_calls == (spec.expect_replace_args,),
                detail=f"actual={outcome.replace_calls!r}",
            )
        )
    else:
        results.append(
            AssertionResult(
                name=f"{spec.key}: replace_profile not called",
                passed=outcome.replace_calls == (),
                detail=f"actual={outcome.replace_calls!r}",
            )
        )

    if spec.expect_remove_profile is not None:
        results.append(
            AssertionResult(
                name=f"{spec.key}: remove_profile called",
                passed=outcome.remove_calls == ((spec.expect_remove_profile,),),
                detail=f"actual={outcome.remove_calls!r}",
            )
        )
    elif spec.expect_replace_args is None:
        results.append(
            AssertionResult(
                name=f"{spec.key}: remove_profile not called",
                passed=outcome.remove_calls == (),
                detail=f"actual={outcome.remove_calls!r}",
            )
        )

    return results


def assert_outcome(outcome: ScenarioOutcome) -> None:
    failures = [result for result in evaluate_outcome(outcome) if not result.passed]
    if not failures:
        return

    formatted = "; ".join(f"{result.name}: {result.detail}" for result in failures)
    raise AssertionError(formatted)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate the reCAPTCHA burn-and-replace autofix end-to-end."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Keep the verification explicitly offline-only; the swapper warm step stays mocked.",
    )
    return parser.parse_args(argv)


async def _main_async(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    specs = tuple(scenario_specs().values())
    logging.disable(logging.CRITICAL)
    try:
        outcomes = [
            await simulate_scenario(spec, dry_run=args.dry_run)
            for spec in specs
        ]
    finally:
        logging.disable(logging.NOTSET)

    assertions: list[AssertionResult] = []
    for outcome in outcomes:
        scenario_assertions = evaluate_outcome(outcome)
        assertions.extend(scenario_assertions)
        for result in scenario_assertions:
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {result.name} :: {result.detail}")

    failed = [result for result in assertions if not result.passed]
    mode = "dry-run" if args.dry_run else "simulated"
    if failed:
        print(f"FAIL [{mode}] {len(assertions) - len(failed)}/{len(assertions)} assertions passed")
        return 1

    print(f"PASS [{mode}] {len(assertions)}/{len(assertions)} assertions passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
