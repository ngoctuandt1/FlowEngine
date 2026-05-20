import re
from pathlib import Path

import pytest

from flow.model_selector import DEFAULT_MODEL as FLOW_DEFAULT_MODEL, MODEL_MAP
from server.models.job import DEFAULT_MODEL as JOB_DEFAULT_MODEL, Job, JobCreate, JobType


EXPECTED_DEFAULT_MODEL = "veo-3.1-lite-lp"
REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_CONSTANTS = REPO_ROOT / "frontend" / "js" / "constants.js"

# TODO(Unit E): migrate cross-layer defaults after LP model removal finishes.
pytestmark = pytest.mark.skip(reason="Unit E migration pending")


def _extract_frontend_default_model() -> str:
    source = FRONTEND_CONSTANTS.read_text(encoding="utf-8")
    match = re.search(r"const DEFAULT_MODEL = '([^']+)';", source)
    if match is None:
        raise AssertionError("Could not parse DEFAULT_MODEL from frontend/js/constants.js")
    return match.group(1)


def test_default_model_is_lite_lp_across_server_frontend_and_flow():
    assert JOB_DEFAULT_MODEL == EXPECTED_DEFAULT_MODEL
    assert JobCreate(type=JobType.TEXT_TO_VIDEO).model == EXPECTED_DEFAULT_MODEL
    assert Job(type=JobType.TEXT_TO_VIDEO).model == EXPECTED_DEFAULT_MODEL
    assert FLOW_DEFAULT_MODEL == EXPECTED_DEFAULT_MODEL
    assert _extract_frontend_default_model() == EXPECTED_DEFAULT_MODEL


def test_fast_lp_remains_a_valid_model_choice():
    assert "veo-3.1-fast-lp" in MODEL_MAP
