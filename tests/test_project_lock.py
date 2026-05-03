"""Tests for ProjectLock semaphore behavior (FLOW_PROJECT_INFLIGHT)."""
from __future__ import annotations

import importlib

from worker import project_lock as project_lock_mod


def _reload():
    return importlib.reload(project_lock_mod)


def test_default_cap_is_one(monkeypatch):
    monkeypatch.delenv("FLOW_PROJECT_INFLIGHT", raising=False)
    mod = _reload()
    lock = mod.ProjectLock()
    assert lock.max_inflight == 1


def test_env_default_cap(monkeypatch):
    monkeypatch.setenv("FLOW_PROJECT_INFLIGHT", "3")
    mod = _reload()
    lock = mod.ProjectLock()
    assert lock.max_inflight == 3
    monkeypatch.setenv("FLOW_PROJECT_INFLIGHT", "")
    _reload()


def test_invalid_env_falls_back_to_one(monkeypatch):
    monkeypatch.setenv("FLOW_PROJECT_INFLIGHT", "abc")
    mod = _reload()
    lock = mod.ProjectLock()
    assert lock.max_inflight == 1
    monkeypatch.setenv("FLOW_PROJECT_INFLIGHT", "")
    _reload()


def test_constructor_overrides_env(monkeypatch):
    monkeypatch.setenv("FLOW_PROJECT_INFLIGHT", "5")
    mod = _reload()
    lock = mod.ProjectLock(max_inflight=2)
    assert lock.max_inflight == 2
    monkeypatch.setenv("FLOW_PROJECT_INFLIGHT", "")
    _reload()


def test_acquire_under_cap_three():
    lock = project_lock_mod.ProjectLock(max_inflight=3)
    url = "https://flow.example/project/x"
    assert lock.acquire(url, "job-1") is True
    assert lock.acquire(url, "job-2") is True
    assert lock.acquire(url, "job-3") is True
    # Fourth job blocked.
    assert lock.acquire(url, "job-4") is False
    assert sorted(lock.held_by(url)) == ["job-1", "job-2", "job-3"]


def test_acquire_idempotent_same_job():
    lock = project_lock_mod.ProjectLock(max_inflight=2)
    url = "p"
    assert lock.acquire(url, "job-A") is True
    # Re-acquiring with same job_id is a no-op success.
    assert lock.acquire(url, "job-A") is True
    assert lock.acquire(url, "job-B") is True
    # job-A still holds 1 of 2; new "job-A" call did not consume a slot.
    assert sorted(lock.held_by(url)) == ["job-A", "job-B"]


def test_release_specific_job_frees_slot():
    lock = project_lock_mod.ProjectLock(max_inflight=2)
    url = "p"
    lock.acquire(url, "j1")
    lock.acquire(url, "j2")
    assert lock.acquire(url, "j3") is False
    lock.release(url, "j1")
    assert lock.acquire(url, "j3") is True


def test_release_without_job_id_clears_all():
    lock = project_lock_mod.ProjectLock(max_inflight=3)
    url = "p"
    lock.acquire(url, "j1")
    lock.acquire(url, "j2")
    lock.release(url)
    assert tuple(lock.held_by(url)) == ()


def test_release_unknown_job_id_is_noop():
    lock = project_lock_mod.ProjectLock(max_inflight=2)
    url = "p"
    lock.acquire(url, "j1")
    lock.release(url, "j-other")
    assert tuple(lock.held_by(url)) == ("j1",)


def test_separate_projects_have_independent_caps():
    lock = project_lock_mod.ProjectLock(max_inflight=1)
    assert lock.acquire("p1", "j1") is True
    assert lock.acquire("p2", "j2") is True
    # p1 still locked for any other job.
    assert lock.acquire("p1", "j3") is False


def test_min_one_cap_floor():
    lock = project_lock_mod.ProjectLock(max_inflight=0)
    assert lock.max_inflight == 1
    lock = project_lock_mod.ProjectLock(max_inflight=-5)
    assert lock.max_inflight == 1
