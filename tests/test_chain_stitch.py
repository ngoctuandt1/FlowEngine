from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from flow.operations import _chain_stitch as chain_stitch


def _clip(tmp_path: Path, name: str, content: bytes = b"mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_stream_copy_success_writes_concat_manifest(monkeypatch, tmp_path):
    clips = [_clip(tmp_path, "L1.mp4"), _clip(tmp_path, "L2 with space.mp4")]
    output = tmp_path / "out" / "stitched.mp4"
    calls: list[list[str]] = []
    manifests: list[str] = []

    monkeypatch.setattr(chain_stitch.shutil, "which", lambda name: name)

    def fake_run(command, **kwargs):
        calls.append(command)
        manifests.append(Path(command[6]).read_text(encoding="utf-8"))
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 120
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(chain_stitch.subprocess, "run", fake_run)

    result = chain_stitch.stitch_chain_clips(clips, output)

    assert result == output.resolve()
    assert output.parent.exists()
    assert calls == [
        [
            "ffmpeg",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            calls[0][6],
            "-c",
            "copy",
            str(output.resolve()),
        ]
    ]
    assert manifests == [
        f"file '{clips[0].resolve().as_posix()}'\n"
        f"file '{clips[1].resolve().as_posix()}'\n"
    ]
    assert not Path(calls[0][6]).exists()


def test_stream_copy_failure_retries_reencode_filter(monkeypatch, tmp_path):
    clips = [_clip(tmp_path, "L1.mp4"), _clip(tmp_path, "L2.mp4")]
    output = tmp_path / "stitched.mp4"
    calls: list[list[str]] = []

    monkeypatch.setattr(chain_stitch.shutil, "which", lambda name: name)

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="0\n", stderr="")
        if command[:3] == ["ffmpeg", "-f", "concat"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="codec mismatch")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(chain_stitch.subprocess, "run", fake_run)

    result = chain_stitch.stitch_chain_clips(clips, output, timeout_sec=7)

    assert result == output.resolve()
    assert calls[0][:6] == ["ffmpeg", "-f", "concat", "-safe", "0", "-i"]
    assert calls[1][0] == "ffprobe"
    assert calls[2][0] == "ffprobe"
    assert calls[3][:5] == ["ffmpeg", "-i", str(clips[0].resolve()), "-i", str(clips[1].resolve())]
    assert "[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[v][a]" in calls[3]
    assert "libx264" in calls[3]
    assert "aac" in calls[3]
    assert str(output.resolve()) == calls[3][-1]


def test_reencode_true_skips_stream_copy(monkeypatch, tmp_path):
    clips = [_clip(tmp_path, "L1.mp4"), _clip(tmp_path, "L2.mp4")]
    output = tmp_path / "stitched.mp4"
    calls: list[list[str]] = []

    monkeypatch.setattr(chain_stitch.shutil, "which", lambda name: name)

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(chain_stitch.subprocess, "run", fake_run)

    chain_stitch.stitch_chain_clips(clips, output, reencode=True)

    assert not any(command[:3] == ["ffmpeg", "-f", "concat"] for command in calls)
    assert calls[-1][0] == "ffmpeg"
    assert any("concat=n=2:v=1:a=0[v]" in arg for arg in calls[-1])
    assert "-an" in calls[-1]


def test_missing_clip_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(chain_stitch.shutil, "which", lambda name: name)

    with pytest.raises(RuntimeError, match="clip does not exist"):
        chain_stitch.stitch_chain_clips([tmp_path / "missing.mp4"], tmp_path / "out.mp4")


def test_empty_clip_list_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(chain_stitch.shutil, "which", lambda name: name)

    with pytest.raises(RuntimeError, match="no clips"):
        chain_stitch.stitch_chain_clips([], tmp_path / "out.mp4")


def test_output_dir_auto_create(monkeypatch, tmp_path):
    clips = [_clip(tmp_path, "L1.mp4")]
    output = tmp_path / "nested" / "dir" / "stitched.mp4"

    monkeypatch.setattr(chain_stitch.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        chain_stitch.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )

    chain_stitch.stitch_chain_clips(clips, output)

    assert output.parent.is_dir()


def test_ffprobe_clip_duration_total_sums_durations(monkeypatch, tmp_path):
    clips = [_clip(tmp_path, "L1.mp4"), _clip(tmp_path, "L2.mp4")]
    durations = iter(["8.0\n", "8.25\n"])
    calls: list[list[str]] = []

    monkeypatch.setattr(chain_stitch.shutil, "which", lambda name: name)

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=next(durations), stderr="")

    monkeypatch.setattr(chain_stitch.subprocess, "run", fake_run)

    total = chain_stitch.ffprobe_clip_duration_total(clips)

    assert total == pytest.approx(16.25)
    assert len(calls) == 2
    assert all(command[:5] == ["ffprobe", "-v", "error", "-show_entries", "format=duration"] for command in calls)


def test_live_ffmpeg_fixture_stitch_when_available(tmp_path):
    fixtures = sorted(Path("tests/fixtures").glob("**/*.mp4"))
    if len(fixtures) < 2:
        pytest.skip("tests/fixtures has fewer than two mp4 fixtures")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    output = tmp_path / "stitched.mp4"
    result = chain_stitch.stitch_chain_clips(fixtures[:2], output)

    assert result == output.resolve()
    assert output.exists()
    assert output.stat().st_size > 0
