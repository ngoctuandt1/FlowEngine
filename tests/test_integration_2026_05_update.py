"""Integration smoke for the 2026-05 Flow feature update.

These tests intentionally stay browser/network-free. They verify the merged
Unit A/B/C/D/E/G/H seams through public handlers and shared exception shapes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow import agent
from flow.model_selector import canonicalize_video_model_key
from flow.operations import generate
from flow.operations._base import L2PaywallError
from worker import dispatcher


def test_model_alias_path_maps_legacy_lp_to_lite() -> None:
    assert canonicalize_video_model_key("veo-3.1-lite-lp") == "veo-3.1-lite"


@pytest.mark.asyncio
async def test_non_lp_default_path_reaches_text_to_video(monkeypatch) -> None:
    calls: list[dict] = []

    @asynccontextmanager
    async def fake_client_lease(profile: str, *, target_url: str | None = None):
        assert profile == "paid-profile"
        assert target_url is None
        yield SimpleNamespace(_job_id=None, profile_name=profile)

    async def fake_text_to_video(client, **kwargs):
        calls.append({"client": client, **kwargs})
        return {
            "project_url": "https://labs.google/fx/tools/flow/project/project-1",
            "media_id": "media-1",
            "output_files": ["out.mp4"],
        }

    monkeypatch.setattr(dispatcher, "_client_lease", fake_client_lease)
    monkeypatch.setattr(generate, "text_to_video", fake_text_to_video)

    result = await dispatcher.handle_text_to_video(
        {
            "id": "job-default-model",
            "type": "text-to-video",
            "profile": "paid-profile",
            "prompt": "smoke default model",
        }
    )

    assert result["media_id"] == "media-1"
    assert calls == [
        {
            "client": calls[0]["client"],
            "prompt": "smoke default model",
            "model": "veo-3.1-lite",
            "aspect_ratio": "16:9",
            "free_mode": True,
        }
    ]
    assert calls[0]["client"]._job_id == "job-default-model"


@pytest.mark.asyncio
async def test_composer_video_count_guard_blocks_over_budget(monkeypatch) -> None:
    monkeypatch.setenv("FLOW_MAX_CREDITS_PER_JOB", "10")
    verify_count = AsyncMock(return_value="Video crop_16_9 x1")
    verify_credits = AsyncMock(return_value=40)
    monkeypatch.setattr(generate, "_verify_l1_output_count", verify_count)
    monkeypatch.setattr(generate, "_verify_credits", verify_credits)
    page = object()

    with pytest.raises(generate.CreditBudgetExceeded) as exc_info:
        await generate._guard_l1_submit(page)

    assert exc_info.value.cost == 40
    assert exc_info.value.budget == 10
    assert exc_info.value.error_kind == "credit_budget_exceeded"
    verify_count.assert_awaited_once_with(page, 1)


@pytest.mark.asyncio
async def test_agent_already_off_noop_path(monkeypatch) -> None:
    async def noop(*_args, **_kwargs):
        return None

    async def detect_off(*_args, **_kwargs):
        return agent._AgentDetection("off")

    monkeypatch.setattr(agent, "install_agent_auth_probe", noop)
    monkeypatch.setattr(agent, "_detect_agent_state", detect_off)

    page = SimpleNamespace(
        url="https://labs.google/fx/tools/flow/project/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    )

    result = await agent.disable_agent_mode_if_active(
        page,
        profile_name="free-profile",
    )

    assert result.status == "already_off"
    assert result.previous_detection_state == "off"
    assert result.restoration_token is None


@pytest.mark.asyncio
async def test_dispatcher_propagates_canonical_l2_paywall(monkeypatch) -> None:
    async def paywalled_handler(job: dict) -> dict:
        raise L2PaywallError(
            operation=job["type"],
            profile=job["profile"],
        )

    class FakeProfileManager:
        def __init__(self) -> None:
            self.events: list[tuple[str, str, str | None]] = []

        def mark_busy(self, profile: str, job_id: str) -> None:
            self.events.append(("busy", profile, job_id))

        def mark_available(self, profile: str) -> None:
            self.events.append(("available", profile, None))

    class FakeProjectLock:
        def __init__(self) -> None:
            self.released: list[tuple[str, str]] = []

        def acquire(self, project_url: str, job_id: str) -> bool:
            return True

        def release(self, project_url: str, job_id: str) -> None:
            self.released.append((project_url, job_id))

    monkeypatch.setitem(dispatcher.HANDLER_MAP, "extend-video", paywalled_handler)

    profile_manager = FakeProfileManager()
    project_lock = FakeProjectLock()
    result = await dispatcher.dispatch_job(
        {
            "id": "job-l2-paywall",
            "type": "extend-video",
            "profile": "free-profile",
            "job_level": 2,
            "project_url": "https://labs.google/fx/tools/flow/project/project-1",
            "media_id": "media-1",
        },
        profile_manager,
        project_lock,
    )

    assert result == {
        "status": "failed",
        "error_kind": "paid_tier_required",
        "error_message": "Video editing is only available for paid subscribers",
        "error": "Video editing is only available for paid subscribers",
    }
    assert project_lock.released == [
        ("https://labs.google/fx/tools/flow/project/project-1", "job-l2-paywall")
    ]
    assert profile_manager.events == [
        ("busy", "free-profile", "job-l2-paywall"),
        ("available", "free-profile", None),
    ]
