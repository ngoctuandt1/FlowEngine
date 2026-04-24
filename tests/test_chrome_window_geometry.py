"""Unit tests for ``flow.client._build_window_geometry_args``.

Guards the env-var contract so typos produce a warning, not a crash.
"""

from __future__ import annotations

import logging

import pytest

from flow.client import _build_window_geometry_args


def test_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("FLOW_WINDOW_SIZE", raising=False)
    monkeypatch.delenv("FLOW_WINDOW_POSITION", raising=False)
    assert _build_window_geometry_args() == ["--window-size=1920,1080"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("810x700", "--window-size=810,700"),
        ("810,700", "--window-size=810,700"),
        ("  1280X720 ", "--window-size=1280,720"),
    ],
)
def test_size_override(monkeypatch, raw, expected):
    monkeypatch.setenv("FLOW_WINDOW_SIZE", raw)
    monkeypatch.delenv("FLOW_WINDOW_POSITION", raising=False)
    assert _build_window_geometry_args() == [expected]


def test_position_added_when_set(monkeypatch):
    monkeypatch.delenv("FLOW_WINDOW_SIZE", raising=False)
    monkeypatch.setenv("FLOW_WINDOW_POSITION", "1750,0")
    assert _build_window_geometry_args() == [
        "--window-size=1920,1080",
        "--window-position=1750,0",
    ]


def test_size_and_position_combined(monkeypatch):
    monkeypatch.setenv("FLOW_WINDOW_SIZE", "810x700")
    monkeypatch.setenv("FLOW_WINDOW_POSITION", "1750,10")
    assert _build_window_geometry_args() == [
        "--window-size=810,700",
        "--window-position=1750,10",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "garbage",
        "810",
        "810x",
        "x700",
        "0x700",        # zero not allowed for size
        "50x50",        # below 100 floor
        "-100x100",
        "abc,def",
    ],
)
def test_malformed_size_falls_back_with_warning(monkeypatch, caplog, bad):
    monkeypatch.setenv("FLOW_WINDOW_SIZE", bad)
    monkeypatch.delenv("FLOW_WINDOW_POSITION", raising=False)
    caplog.set_level(logging.WARNING, logger="flow.client")
    result = _build_window_geometry_args()
    assert result == ["--window-size=1920,1080"]
    assert any("FLOW_WINDOW_SIZE" in rec.message for rec in caplog.records)


@pytest.mark.parametrize(
    "bad",
    [
        "garbage",
        "100",
        "-10,0",        # negative not allowed
        "abc,0",
    ],
)
def test_malformed_position_ignored_with_warning(monkeypatch, caplog, bad):
    monkeypatch.delenv("FLOW_WINDOW_SIZE", raising=False)
    monkeypatch.setenv("FLOW_WINDOW_POSITION", bad)
    caplog.set_level(logging.WARNING, logger="flow.client")
    result = _build_window_geometry_args()
    assert result == ["--window-size=1920,1080"]
    assert any("FLOW_WINDOW_POSITION" in rec.message for rec in caplog.records)


def test_position_zero_allowed(monkeypatch):
    """(0, 0) is a valid screen-corner position."""
    monkeypatch.delenv("FLOW_WINDOW_SIZE", raising=False)
    monkeypatch.setenv("FLOW_WINDOW_POSITION", "0,0")
    assert _build_window_geometry_args() == [
        "--window-size=1920,1080",
        "--window-position=0,0",
    ]
