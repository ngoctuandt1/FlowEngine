from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import _base
from flow.operations._base import (
    CreditBudgetExceeded,
    L2_PAYWALL_BANNER_TEXT,
    L2PaywallError,
)


class _VisibleLocator:
    def __init__(self, visible: bool):
        self.visible = visible
        self.first = self
        self.click = AsyncMock()

    async def is_visible(self, timeout=None):
        return self.visible

    async def is_enabled(self):
        return True


class _PaywallPage:
    def __init__(self, *, banner: bool = False, upgrade: bool = False):
        self.banner = banner
        self.upgrade = upgrade

    def get_by_text(self, text, exact=True):
        return _VisibleLocator(
            (text == L2_PAYWALL_BANNER_TEXT and self.banner)
            or (text == "Upgrade" and self.upgrade)
        )

    def get_by_role(self, role, name=None):
        return _VisibleLocator(role in {"button", "link"} and name == "Upgrade" and self.upgrade)


class _NoButtonPage(_PaywallPage):
    def __init__(self):
        super().__init__(banner=False, upgrade=False)

    def locator(self, selector):
        return _VisibleLocator(False)


class _EditorMountedLocator:
    first = None

    def __init__(self, mounted: bool):
        self.mounted = mounted
        self.first = self

    async def wait_for(self, state=None, timeout=None):
        if not self.mounted:
            raise TimeoutError("editor not mounted")


class _L2ToolbarPage(_PaywallPage):
    def __init__(self, *, url: str, mounted: bool, visible_tokens: set[str]):
        super().__init__(banner=False, upgrade=False)
        self.url = url
        self.mounted = mounted
        self.visible_tokens = visible_tokens
        self.searched_selectors: list[str] = []

    def locator(self, selector):
        if selector == "video, button:has-text('Hide history'), button:has-text('Ẩn lịch sử')":
            return _EditorMountedLocator(self.mounted)
        self.searched_selectors.append(selector)
        return _VisibleLocator(any(token in selector for token in self.visible_tokens))


class _NavigatingL2ToolbarPage(_L2ToolbarPage):
    def __init__(self):
        super().__init__(
            url="https://labs.google/fx/tools/flow/project/proj/edit/media",
            mounted=True,
            visible_tokens=set(),
        )

    def locator(self, selector):
        if selector != "video, button:has-text('Hide history'), button:has-text('áº¨n lá»‹ch sá»­')":
            self.url = "https://labs.google/fx/tools/flow/project/proj"
        return super().locator(selector)


class _ProfileManagerStub:
    def __init__(self):
        self.busy: list[tuple[str, str]] = []
        self.available: list[str] = []
        self.removed: list[str] = []
        self.replaced: list[tuple[str, str]] = []

    def mark_busy(self, profile: str, job_id: str):
        self.busy.append((profile, job_id))

    def mark_available(self, profile: str):
        self.available.append(profile)

    def remove_profile(self, profile: str):
        self.removed.append(profile)

    def replace_profile(self, old_profile: str, new_profile: str):
        self.replaced.append((old_profile, new_profile))


class _ProjectLockStub:
    def __init__(self):
        self.acquired: list[tuple[str, str | None]] = []
        self.released: list[tuple[str, str | None]] = []

    def acquire(self, url: str, job_id: str | None = None) -> bool:
        self.acquired.append((url, job_id))
        return True

    def release(self, url: str, job_id: str | None = None):
        self.released.append((url, job_id))


def _l2_job(job_id: str, *, profile: str = "prof-free", op_type: str = "extend-video") -> dict:
    return {
        "id": job_id,
        "type": op_type,
        "job_level": 2,
        "profile": profile,
    }


def _l1_job(job_id: str, *, profile: str = "prof-lite") -> dict:
    return {
        "id": job_id,
        "type": "text-to-video",
        "job_level": 1,
        "profile": profile,
    }


def _fake_client_lease(profile: str):
    @asynccontextmanager
    async def _ctx():
        client = MagicMock()
        client._job_id = None
        yield client

    return _ctx()


async def test_l2_paywall_banner_and_upgrade_cta_raise_canonical_error():
    page = _PaywallPage(banner=True, upgrade=True)

    with pytest.raises(L2PaywallError) as exc_info:
        await _base._assert_l2_available(page, "extend-video", "free-profile")

    exc = exc_info.value
    assert exc.error_kind == "paid_tier_required"
    assert exc.operation == "extend-video"
    assert exc.profile == "free-profile"
    assert str(exc) == L2_PAYWALL_BANNER_TEXT


async def test_missing_legacy_buttons_do_not_count_as_paywall():
    page = _NoButtonPage()

    await _base._assert_l2_available(page, "camera-move", "free-profile")

    clicked = await _base.click_action_button(page, ["Camera"])
    assert clicked is False


async def test_l2_silent_hide_paid_english_toolbar_does_not_raise():
    page = _L2ToolbarPage(
        url="https://labs.google/fx/tools/flow/project/proj/edit/media",
        mounted=True,
        visible_tokens={"Extend"},
    )

    await _base._assert_l2_available(page, "extend-video", "paid-profile")

    searched = "\n".join(page.searched_selectors)
    assert "Extend" in searched
    assert {"Mở rộng", "Chèn", "Xoá", "Máy quay"}.issubset(_base._L2_TOOLBAR_TOKENS)
    assert {"keyboard_double_arrow_right", "add_box", "ink_eraser", "videocam"}.issubset(
        _base._L2_TOOLBAR_TOKENS
    )
    assert "arrow_outward" not in _base._L2_TOOLBAR_TOKENS
    assert "add_circle" not in _base._L2_TOOLBAR_TOKENS
    assert "cancel" not in _base._L2_TOOLBAR_TOKENS


async def test_l2_silent_hide_paid_vi_toolbar_does_not_raise():
    page = _L2ToolbarPage(
        url="https://labs.google/fx/tools/flow/project/proj/edit/media",
        mounted=True,
        visible_tokens={"Máy quay"},
    )

    await _base._assert_l2_available(page, "camera-move", "paid-profile")


async def test_l2_silent_hide_paid_icon_only_toolbar_does_not_raise():
    page = _L2ToolbarPage(
        url="https://labs.google/fx/tools/flow/project/proj/edit/media",
        mounted=True,
        visible_tokens={"ink_eraser"},
    )

    await _base._assert_l2_available(page, "remove-object", "paid-profile")


async def test_l2_silent_hide_free_edit_url_without_tokens_raises(monkeypatch):
    monkeypatch.setattr(_base, "_L2_SILENT_HIDE_PAINT_WAIT_MS", 1)
    page = _L2ToolbarPage(
        url="https://labs.google/fx/tools/flow/project/proj/edit/media",
        mounted=True,
        visible_tokens=set(),
    )

    with pytest.raises(L2PaywallError) as exc_info:
        await _base._assert_l2_available(page, "insert-object", "free-profile")

    assert exc_info.value.error_kind == "paid_tier_required"
    assert str(exc_info.value) == "L2 editing controls absent (free-tier silent gating)"


async def test_l2_silent_hide_project_navigation_before_raise_does_not_raise(monkeypatch):
    monkeypatch.setattr(_base, "_L2_SILENT_HIDE_PAINT_WAIT_MS", 1)
    page = _NavigatingL2ToolbarPage()

    result = await _base._assert_l2_available(page, "extend-video", "free-profile")

    assert result is None
    assert "/edit/" not in page.url
    assert page.searched_selectors


async def test_l2_silent_hide_non_edit_url_does_not_raise(monkeypatch):
    monkeypatch.setattr(_base, "_L2_SILENT_HIDE_PAINT_WAIT_MS", 1)
    page = _L2ToolbarPage(
        url="https://labs.google/fx/tools/flow/project/proj",
        mounted=True,
        visible_tokens=set(),
    )

    await _base._assert_l2_available(page, "insert-object", "free-profile")

    assert page.searched_selectors == []


async def test_dispatch_job_paywall_failure_is_canonical_and_not_retried(monkeypatch):
    from worker import dispatcher

    calls = 0

    async def raise_paywall(job):
        nonlocal calls
        calls += 1
        raise L2PaywallError(operation=job["type"], profile=job["profile"])

    monkeypatch.setitem(dispatcher.HANDLER_MAP, "extend-video", raise_paywall)


    profile_manager = _ProfileManagerStub()
    result = await dispatcher.dispatch_job(
        _l2_job("paywall-single"),
        profile_manager,
        _ProjectLockStub(),
    )

    assert calls == 1
    assert result == {
        "status": "failed",
        "error_kind": "paid_tier_required",
        "error_message": L2_PAYWALL_BANNER_TEXT,
        "error": L2_PAYWALL_BANNER_TEXT,
    }
    assert profile_manager.available == ["prof-free"]
    assert profile_manager.removed == []
    assert profile_manager.replaced == []


async def test_dispatch_job_credit_budget_failure_is_canonical_and_not_retried(monkeypatch):
    from worker import dispatcher

    calls = 0

    async def raise_budget(job):
        nonlocal calls
        calls += 1
        raise CreditBudgetExceeded(cost=9, budget=4)

    monkeypatch.setitem(dispatcher.HANDLER_MAP, "extend-video", raise_budget)

    profile_manager = _ProfileManagerStub()
    result = await dispatcher.dispatch_job(
        _l2_job("budget-single"),
        profile_manager,
        _ProjectLockStub(),
    )

    assert calls == 1
    assert result == {
        "status": "failed",
        "error_kind": "credit_budget_exceeded",
        "error_message": "cost 9 exceeds budget 4",
        "error": "cost 9 exceeds budget 4",
    }
    assert profile_manager.available == ["prof-free"]
    assert profile_manager.removed == []


async def test_l1_batch_credit_budget_failure_is_canonical(monkeypatch):
    from worker import dispatcher

    async def raise_budget(client, jobs):
        raise CreditBudgetExceeded(cost=12, budget=8)

    monkeypatch.setattr(dispatcher, "_client_lease", _fake_client_lease)
    monkeypatch.setattr(
        "flow.operations._batch.batch_dispatch_l1_same_project",
        raise_budget,
    )

    jobs = [_l1_job("l1-a"), _l1_job("l1-b")]
    result = await dispatcher.dispatch_batch_l1_same_project(
        jobs,
        _ProfileManagerStub(),
        _ProjectLockStub(),
    )

    assert result == [
        {
            "job_id": "l1-a",
            "status": "failed",
            "error_kind": "credit_budget_exceeded",
            "error_message": "cost 12 exceeds budget 8",
            "error": "cost 12 exceeds budget 8",
        },
        {
            "job_id": "l1-b",
            "status": "failed",
            "error_kind": "credit_budget_exceeded",
            "error_message": "cost 12 exceeds budget 8",
            "error": "cost 12 exceeds budget 8",
        },
    ]


async def test_multitab_paywall_exception_becomes_per_job_failure(monkeypatch):
    from flow.operations import _multitab

    async def dispatch_one(client, job):
        if job["id"] == "mt-paywall":
            raise L2PaywallError(operation=job["type"], profile="prof-free")
        return {"job_id": job["id"], "status": "completed", "media_id": "media-ok"}

    monkeypatch.setattr(_multitab, "dispatch_op_in_new_tab", dispatch_one)
    monkeypatch.setattr(_multitab.asyncio, "sleep", AsyncMock())

    jobs = [
        {"id": "mt-paywall", "type": "extend-video"},
        {"id": "mt-ok", "type": "camera-move"},
    ]

    result = await _multitab.batch_dispatch_ops_multitab(MagicMock(), jobs)

    assert result[0] == {
        "job_id": "mt-paywall",
        "status": "failed",
        "error_kind": "paid_tier_required",
        "error_message": L2_PAYWALL_BANNER_TEXT,
        "error": L2_PAYWALL_BANNER_TEXT,
    }
    assert result[1]["status"] == "completed"


async def test_dispatch_batch_l2_inspects_exception_results(monkeypatch):
    from worker import dispatcher

    async def fake_multitab(jobs, profile_manager, project_lock):
        return [
            L2PaywallError(operation="extend-video", profile="prof-free"),
            {"job_id": "batch-ok", "status": "completed", "media_id": "media-ok"},
        ]

    monkeypatch.setattr(dispatcher, "dispatch_batch_multitab", fake_multitab)

    jobs = [_l2_job("batch-paywall"), _l2_job("batch-ok", op_type="camera-move")]
    result = await dispatcher.dispatch_batch(
        jobs,
        _ProfileManagerStub(),
        _ProjectLockStub(),
    )

    assert result[0] == {
        "job_id": "batch-paywall",
        "status": "failed",
        "error_kind": "paid_tier_required",
        "error_message": L2_PAYWALL_BANNER_TEXT,
        "error": L2_PAYWALL_BANNER_TEXT,
    }
    assert result[1]["status"] == "completed"
