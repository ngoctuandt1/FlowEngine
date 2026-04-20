"""Unit tests for scripts/probe_l2_media_id.py regex + assertion builder."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "probe_l2_media_id.py"
)


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("probe_l2_media_id", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe_module()


REAL_SLUG_L1 = "cf348f45-c772-472b-ae70-02f331400d2f"
REAL_SLUG_INSERT = "97c23ab3-4c07-433c-892e-32932e151f44"
REAL_SLUG_REMOVE = "11112222-3333-4444-5555-666677778888"


def _job(media_id: str, status: str = "completed") -> dict:
    return {
        "id": f"job-{media_id[:8]}",
        "status": status,
        "media_id": media_id,
        "edit_url": f"https://labs.google/fx/tools/flow/project/x/edit/{media_id}",
        "chain_id": "chain-1",
        "output_files": ["a.mp4"],
    }


def test_regex_matches_real_flow_uuid_slug():
    assert probe.ROUTE_SLUG_RE.fullmatch(REAL_SLUG_L1)
    assert probe.ROUTE_SLUG_RE.fullmatch(REAL_SLUG_INSERT)


@pytest.mark.parametrize(
    "bad_slug",
    [
        "CF348F45-C772-472B-AE70-02F331400D2F",   # uppercase
        "cf348f45c772472bae7002f331400d2f",       # 32-no-dashes (old regex)
        "",                                        # empty
        "cf348f45-c772-472b-ae70-02f331400d2",    # last segment too short
        "cf348f45-c772-472b-ae70-02f331400d2ff",  # last segment too long
        "cf348f45-c772-472b-ae70-02f331400d2g",   # non-hex char
    ],
)
def test_regex_rejects_bad_slugs(bad_slug: str):
    assert probe.ROUTE_SLUG_RE.fullmatch(bad_slug) is None


def test_build_assertions_all_pass_on_distinct_uuid_slugs():
    assertions = probe.build_assertions(
        _job(REAL_SLUG_L1),
        _job(REAL_SLUG_INSERT),
        _job(REAL_SLUG_REMOVE),
    )
    assert assertions == {
        "all_completed": True,
        "l1_media_id_is_route_slug_format": True,
        "insert_media_id_is_route_slug_format": True,
        "remove_media_id_is_route_slug_format": True,
        "insert_media_id_differs_from_l1": True,
        "remove_media_id_differs_from_l1": True,
        "insert_media_id_differs_from_remove": True,
    }
    report = probe.build_report("p", "prompt",
                                _job(REAL_SLUG_L1),
                                _job(REAL_SLUG_INSERT),
                                _job(REAL_SLUG_REMOVE))
    assert report["verdict"] == "PASS"


def test_build_assertions_flags_sibling_collision():
    """Live-probe case: insert and remove siblings returned the same slug."""
    assertions = probe.build_assertions(
        _job(REAL_SLUG_L1),
        _job(REAL_SLUG_INSERT),
        _job(REAL_SLUG_INSERT),  # remove collided with insert
    )
    assert assertions["all_completed"] is True
    assert assertions["insert_media_id_differs_from_l1"] is True
    assert assertions["remove_media_id_differs_from_l1"] is True
    assert assertions["insert_media_id_differs_from_remove"] is False
    report = probe.build_report("p", "prompt",
                                _job(REAL_SLUG_L1),
                                _job(REAL_SLUG_INSERT),
                                _job(REAL_SLUG_INSERT))
    assert report["verdict"] == "FAIL"


def test_build_assertions_flags_failed_status():
    assertions = probe.build_assertions(
        _job(REAL_SLUG_L1, status="failed"),
        _job(REAL_SLUG_INSERT),
        _job(REAL_SLUG_REMOVE),
    )
    assert assertions["all_completed"] is False


def test_build_assertions_handles_missing_jobs():
    assertions = probe.build_assertions(_job(REAL_SLUG_L1), None, None)
    assert assertions["all_completed"] is False
    assert assertions["l1_media_id_is_route_slug_format"] is True
    assert assertions["insert_media_id_is_route_slug_format"] is False
    assert assertions["insert_media_id_differs_from_l1"] is False


def test_build_assertions_flags_non_uuid_slug():
    """Old 32-char-hex string should not pass the new UUID regex."""
    bad = _job("cf348f45c772472bae7002f331400d2f")
    assertions = probe.build_assertions(bad, _job(REAL_SLUG_INSERT), _job(REAL_SLUG_REMOVE))
    assert assertions["l1_media_id_is_route_slug_format"] is False
