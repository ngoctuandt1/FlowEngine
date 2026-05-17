"""ffmpeg helpers for stitching per-level chain clips."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def stitch_chain_clips(
    clip_paths: list[Path | str],
    output_path: Path | str,
    *,
    reencode: bool = False,
    timeout_sec: int = 120,
) -> Path:
    """Concat per-level extend clips into one cumulative MP4 via ffmpeg.

    Uses concat demuxer (stream-copy by default, no reencode = fast). Falls
    back to `concat` filter graph (with reencode) when stream-copy fails
    due to codec/timebase mismatch.

    Args:
        clip_paths: ordered list of per-level clip paths (L1, L2, ..., LN).
        output_path: destination MP4 path. Parent dir auto-created.
        reencode: force reencode path (slower but more compatible).
        timeout_sec: ffmpeg invocation timeout.

    Returns:
        output_path (resolved Path).

    Raises:
        RuntimeError: ffmpeg not on PATH, no clips, output path invalid,
        or ffmpeg invocation failed.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    clips = _validate_clip_paths(clip_paths)
    output = _validate_output_path(output_path, clips)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)

    if reencode:
        _run_concat_filter_reencode(clips, output, timeout_sec=timeout_sec)
        return output

    manifest = _write_concat_manifest(clips)
    try:
        copy_result = _run_ffmpeg(
            [
                "ffmpeg",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest),
                "-c",
                "copy",
                str(output),
            ],
            timeout_sec=timeout_sec,
        )
        if copy_result.returncode == 0:
            return output
    finally:
        manifest.unlink(missing_ok=True)

    output.unlink(missing_ok=True)
    _run_concat_filter_reencode(clips, output, timeout_sec=timeout_sec, previous=copy_result)
    return output


def ffprobe_clip_duration_total(clip_paths: list[Path]) -> float:
    """Sum ffprobe durations across clips. Caller uses for cumulative assertion."""
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found on PATH")

    clips = _validate_clip_paths(clip_paths)
    total = 0.0
    for clip in clips:
        result = _run_ffprobe(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(clip),
            ],
            timeout_sec=30,
        )
        if result.returncode != 0:
            raise RuntimeError(_format_failure("ffprobe duration failed", result))
        try:
            total += float(result.stdout.strip())
        except ValueError as exc:
            raise RuntimeError(f"ffprobe returned invalid duration for {clip}") from exc
    return total


def _validate_clip_paths(clip_paths: list[Path | str]) -> list[Path]:
    if not clip_paths:
        raise RuntimeError("no clips to stitch")

    clips: list[Path] = []
    for raw_path in clip_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"clip does not exist: {path}")
        if not path.is_file():
            raise RuntimeError(f"clip is not a file: {path}")
        if path.stat().st_size <= 0:
            raise RuntimeError(f"clip is empty: {path}")
        clips.append(path)
    return clips


def _validate_output_path(output_path: Path | str, clips: list[Path]) -> Path:
    if isinstance(output_path, str) and not output_path.strip():
        raise RuntimeError("output path invalid")
    output = Path(output_path).expanduser()
    if output.name in ("", ".") or output.suffix.lower() != ".mp4":
        raise RuntimeError("output path invalid; expected .mp4 file")
    output = output.resolve()
    if output.exists() and output.is_dir():
        raise RuntimeError(f"output path invalid; is directory: {output}")
    if output in clips:
        raise RuntimeError("output path must not overwrite input clip")
    return output


def _write_concat_manifest(clips: list[Path]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".txt",
        prefix="flow_chain_concat_",
        delete=False,
    )
    with handle:
        for clip in clips:
            handle.write(_concat_file_entry(clip))
    return Path(handle.name)


def _concat_file_entry(path: Path) -> str:
    escaped = path.as_posix().replace("'", r"'\''")
    return f"file '{escaped}'\n"


def _run_concat_filter_reencode(
    clips: list[Path],
    output: Path,
    *,
    timeout_sec: int,
    previous: subprocess.CompletedProcess[str] | None = None,
) -> None:
    has_audio = all(_has_audio_stream(clip, timeout_sec=timeout_sec) for clip in clips)
    command = ["ffmpeg"]
    for clip in clips:
        command.extend(["-i", str(clip)])

    if has_audio:
        inputs = "".join(f"[{index}:v:0][{index}:a:0]" for index in range(len(clips)))
        command.extend(
            [
                "-filter_complex",
                f"{inputs}concat=n={len(clips)}:v=1:a=1[v][a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
    else:
        inputs = "".join(f"[{index}:v:0]" for index in range(len(clips)))
        command.extend(
            [
                "-filter_complex",
                f"{inputs}concat=n={len(clips)}:v=1:a=0[v]",
                "-map",
                "[v]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-an",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )

    result = _run_ffmpeg(command, timeout_sec=timeout_sec)
    if result.returncode != 0:
        details = _format_failure("ffmpeg reencode concat failed", result)
        if previous is not None:
            details += "\n" + _format_failure("ffmpeg stream-copy concat failed", previous)
        output.unlink(missing_ok=True)
        raise RuntimeError(details)


def _has_audio_stream(clip: Path, *, timeout_sec: int) -> bool:
    if shutil.which("ffprobe") is None:
        return False
    try:
        result = _run_ffprobe(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(clip),
            ],
            timeout_sec=timeout_sec,
        )
    except RuntimeError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _run_ffmpeg(command: list[str], *, timeout_sec: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg timed out after {timeout_sec}s") from exc
    except OSError as exc:
        raise RuntimeError(f"ffmpeg invocation failed: {exc}") from exc


def _run_ffprobe(command: list[str], *, timeout_sec: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out after {timeout_sec}s") from exc
    except OSError as exc:
        raise RuntimeError(f"ffprobe invocation failed: {exc}") from exc


def _format_failure(label: str, result: subprocess.CompletedProcess[str]) -> str:
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    details = stderr or stdout or "unknown error"
    return f"{label}: rc={result.returncode} {details}"
