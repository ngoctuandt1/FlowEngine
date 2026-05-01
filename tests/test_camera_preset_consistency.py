import ast
import re
from pathlib import Path

from server.models.job import CAMERA_PRESETS


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_JOB_MODEL = REPO_ROOT / "server" / "models" / "job.py"
FRONTEND_CONSTANTS = REPO_ROOT / "frontend" / "js" / "constants.js"


def _extract_backend_camera_presets() -> list[str]:
    tree = ast.parse(BACKEND_JOB_MODEL.read_text(encoding="utf-8"))

    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if getattr(node.target, "id", None) != "CAMERA_PRESETS":
            continue
        if not isinstance(node.value, ast.Call) or not node.value.args:
            break
        preset_set = node.value.args[0]
        if not isinstance(preset_set, ast.Set):
            break
        return [
            elt.value
            for elt in preset_set.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]

    raise AssertionError("Could not parse CAMERA_PRESETS from server/models/job.py")


def _extract_frontend_camera_presets() -> list[str]:
    source = FRONTEND_CONSTANTS.read_text(encoding="utf-8")
    match = re.search(r"const CAMERA_PRESETS = \[(.*?)\];", source, re.DOTALL)
    if match is None:
        raise AssertionError("Could not parse CAMERA_PRESETS from frontend/js/constants.js")
    return re.findall(r"'([^']+)'", match.group(1))


def test_frontend_camera_presets_match_backend_literal_order_and_values():
    backend_presets = _extract_backend_camera_presets()
    frontend_presets = _extract_frontend_camera_presets()

    assert frontend_presets == backend_presets
    assert frozenset(frontend_presets) == CAMERA_PRESETS
