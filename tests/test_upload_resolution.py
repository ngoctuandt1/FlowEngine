"""`_resolve_upload_path` + `_resolve_upload_paths` security contracts.

The resolver maps a server-stored `start_image_path` / `ref_image_path` /
`ingredient_image_paths` value — received from the server's JSON — to a
local filesystem path the Playwright file-chooser can feed to Chrome.

Threat model: a malicious or malformed server payload may try to make the
worker open files outside `FLOW_UPLOAD_DIR` (path traversal, absolute
paths). The resolver MUST refuse any path that escapes the sandbox.
"""

from pathlib import Path

import pytest


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """Point the dispatcher's UPLOAD_DIR at a clean tempdir for the test."""
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(tmp_path))
    # dispatcher.py evaluates UPLOAD_DIR at import time — patch the live
    # module binding as well so already-imported code picks up the tempdir.
    import worker.dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "UPLOAD_DIR", tmp_path.resolve(), raising=False)
    return tmp_path.resolve()


def test_returns_none_for_none(upload_dir):
    from worker.dispatcher import _resolve_upload_path
    assert _resolve_upload_path(None) is None


def test_returns_none_for_empty_string(upload_dir):
    from worker.dispatcher import _resolve_upload_path
    assert _resolve_upload_path("") is None


def test_returns_none_for_whitespace_only(upload_dir):
    from worker.dispatcher import _resolve_upload_path
    assert _resolve_upload_path("   ") is None


def test_relative_path_resolves_under_upload_dir(upload_dir):
    from worker.dispatcher import _resolve_upload_path
    resolved = _resolve_upload_path("photo.png")
    assert Path(resolved) == upload_dir / "photo.png"


def test_strips_leading_uploads_prefix(upload_dir):
    from worker.dispatcher import _resolve_upload_path
    resolved = _resolve_upload_path("uploads/photo.png")
    assert Path(resolved) == upload_dir / "photo.png"


def test_retarget_frame_path_resolves_under_flow_upload_dir(upload_dir):
    from worker.dispatcher import _resolve_upload_path
    resolved = _resolve_upload_path("retarget/frame_123.jpg")
    assert Path(resolved) == upload_dir / "retarget" / "frame_123.jpg"


def test_strips_leading_uploads_prefix_case_insensitively(upload_dir):
    from worker.dispatcher import _resolve_upload_path
    resolved = _resolve_upload_path("Uploads/photo.png")
    assert Path(resolved) == upload_dir / "photo.png"


def test_strips_uploads_prefix_only_once(upload_dir):
    """`uploads/uploads/x.png` must keep the second segment."""
    from worker.dispatcher import _resolve_upload_path
    resolved = _resolve_upload_path("uploads/uploads/photo.png")
    assert Path(resolved) == upload_dir / "uploads" / "photo.png"


def test_absolute_path_inside_upload_dir_is_accepted(upload_dir):
    from worker.dispatcher import _resolve_upload_path
    inside = upload_dir / "nested" / "photo.png"
    resolved = _resolve_upload_path(str(inside))
    assert Path(resolved) == inside


def test_absolute_path_outside_upload_dir_is_rejected(upload_dir, tmp_path):
    """Path traversal via absolute path must raise."""
    from worker.dispatcher import _resolve_upload_path
    outside = tmp_path.parent / "escape.png"
    with pytest.raises(RuntimeError, match="escapes FLOW_UPLOAD_DIR"):
        _resolve_upload_path(str(outside))


def test_parent_traversal_is_rejected(upload_dir):
    """Relative `..` segments that escape UPLOAD_DIR must raise."""
    from worker.dispatcher import _resolve_upload_path
    with pytest.raises(RuntimeError, match="escapes FLOW_UPLOAD_DIR"):
        _resolve_upload_path("../../etc/passwd")


def test_resolve_upload_paths_empty_list(upload_dir):
    from worker.dispatcher import _resolve_upload_paths
    assert _resolve_upload_paths([]) == []


def test_resolve_upload_paths_none_returns_empty(upload_dir):
    from worker.dispatcher import _resolve_upload_paths
    assert _resolve_upload_paths(None) == []


def test_resolve_upload_paths_filters_none_entries(upload_dir):
    from worker.dispatcher import _resolve_upload_paths
    paths = ["a.png", None, "b.png", ""]
    resolved = _resolve_upload_paths(paths)
    assert len(resolved) == 2
    assert Path(resolved[0]) == upload_dir / "a.png"
    assert Path(resolved[1]) == upload_dir / "b.png"


def test_resolve_upload_paths_propagates_escape_errors(upload_dir, tmp_path):
    """One bad entry fails the whole list — we don't partially resolve."""
    from worker.dispatcher import _resolve_upload_paths
    outside = tmp_path.parent / "escape.png"
    with pytest.raises(RuntimeError, match="escapes FLOW_UPLOAD_DIR"):
        _resolve_upload_paths(["ok.png", str(outside)])
