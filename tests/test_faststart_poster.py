from pathlib import Path
from types import SimpleNamespace

from flow.download import _faststart_mp4


def test_faststart_mp4_generates_poster_jpg(monkeypatch, tmp_path):
    recorded = []
    filepath = tmp_path / "test.mp4"
    filepath.write_bytes(b"mp4")

    def fake_run(cmd, capture_output, timeout):
        recorded.append(cmd)
        output_path = Path(cmd[-1])
        if output_path.suffix == ".tmp":
            output_path.write_bytes(b"faststart")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("flow.download.subprocess.run", fake_run)

    _faststart_mp4(filepath)

    poster_calls = [
        cmd
        for cmd in recorded
        if "-frames:v" in cmd and str(cmd[-1]).endswith(".poster.jpg")
    ]
    assert poster_calls, "no ffmpeg call matching .poster.jpg"
