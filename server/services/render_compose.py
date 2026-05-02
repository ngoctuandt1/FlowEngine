"""Compose a timeline payload into a single MP4 via ffmpeg."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from server.models.render import TimelineClip, TimelinePayload, TimelineTrack

DOWNLOAD_DIR = Path("downloads").resolve()
UPLOAD_DIR = Path("uploads").resolve()
FFMPEG_TIMEOUT_SECONDS = 600
FRAME_RATE = 30
AUDIO_SAMPLE_RATE = 48000
RATIO_DIMENSIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


def _resolve_asset_path(asset_id: str) -> Path:
    raw_text = str(asset_id or "").strip()
    if not raw_text:
        raise ValueError("asset_id is required")

    raw_path = Path(raw_text).expanduser()
    if raw_path.is_absolute():
        resolved = raw_path.resolve()
        if resolved.is_relative_to(DOWNLOAD_DIR) or resolved.is_relative_to(UPLOAD_DIR):
            return resolved
        raise ValueError(f"asset_id must stay within downloads/ or uploads/: {asset_id}")

    parts = list(raw_path.parts)
    if parts and parts[0].lower() in {"downloads", "uploads"}:
        prefix = parts.pop(0).lower()
        base_dir = DOWNLOAD_DIR if prefix == "downloads" else UPLOAD_DIR
        resolved = base_dir.joinpath(*parts).resolve()
    else:
        resolved = DOWNLOAD_DIR.joinpath(*parts).resolve()

    if resolved.is_relative_to(DOWNLOAD_DIR) or resolved.is_relative_to(UPLOAD_DIR):
        return resolved
    raise ValueError(f"asset_id must stay within downloads/ or uploads/: {asset_id}")


def _run_ffmpeg(command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg timed out during timeline compose") from exc
    except OSError as exc:
        raise RuntimeError(f"ffmpeg unavailable: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown ffmpeg error").strip()
        raise RuntimeError(f"ffmpeg compose failed: {detail}")


def _write_concat_file(paths: list[Path], destination: Path) -> None:
    lines = []
    for path in paths:
        normalized = path.resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{normalized}'\n")
    destination.write_text("".join(lines), encoding="utf-8")


def _make_transparent_gap(
    *,
    temp_dir: Path,
    stem: str,
    duration_sec: float,
    width: int,
    height: int,
) -> Path:
    output_path = temp_dir / f"{stem}.mov"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black@0.0:s={width}x{height}:r={FRAME_RATE}:d={duration_sec}",
            "-vf",
            "format=rgba",
            "-an",
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            str(output_path),
        ]
    )
    return output_path


def _make_video_segment(
    *,
    clip: TimelineClip,
    temp_dir: Path,
    stem: str,
    width: int,
    height: int,
) -> Path:
    source_path = _resolve_asset_path(clip.asset_id)
    if not source_path.is_file():
        raise FileNotFoundError(f"asset not found: {clip.asset_id}")

    trim_in = clip.trim_in or 0.0
    output_path = temp_dir / f"{stem}.mov"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(trim_in),
            "-i",
            str(source_path),
            "-t",
            str(clip.duration_sec),
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
                "format=rgba"
            ),
            "-an",
            "-r",
            str(FRAME_RATE),
            "-c:v",
            "qtrle",
            "-pix_fmt",
            "argb",
            str(output_path),
        ]
    )
    return output_path


def _build_video_track(
    *,
    track: TimelineTrack,
    track_index: int,
    total_duration_sec: float,
    width: int,
    height: int,
    temp_dir: Path,
) -> Path:
    segments: list[Path] = []
    cursor = 0.0
    sorted_clips = sorted(track.clips, key=lambda clip: (clip.start_sec, clip.asset_id))

    for clip_index, clip in enumerate(sorted_clips):
        if clip.start_sec < (cursor - 1e-6):
            raise ValueError("overlapping clips within one video track are not supported")

        gap_duration = clip.start_sec - cursor
        if gap_duration > 1e-6:
            segments.append(
                _make_transparent_gap(
                    temp_dir=temp_dir,
                    stem=f"video_track_{track_index}_gap_{clip_index}",
                    duration_sec=gap_duration,
                    width=width,
                    height=height,
                )
            )

        segments.append(
            _make_video_segment(
                clip=clip,
                temp_dir=temp_dir,
                stem=f"video_track_{track_index}_clip_{clip_index}",
                width=width,
                height=height,
            )
        )
        cursor = clip.start_sec + clip.duration_sec

    trailing_gap = total_duration_sec - cursor
    if trailing_gap > 1e-6 or not segments:
        segments.append(
            _make_transparent_gap(
                temp_dir=temp_dir,
                stem=f"video_track_{track_index}_tail",
                duration_sec=max(trailing_gap, total_duration_sec),
                width=width,
                height=height,
            )
        )

    concat_file = temp_dir / f"video_track_{track_index}.txt"
    track_output = temp_dir / f"video_track_{track_index}.mov"
    _write_concat_file(segments, concat_file)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(track_output),
        ]
    )
    return track_output


def _cleanup_partial_output(output_path: Path) -> None:
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass


def compose_timeline(payload: TimelinePayload, output_path: Path) -> None:
    """Render the full timeline to one MP4 output."""
    width, height = RATIO_DIMENSIONS[payload.ratio.value]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_tracks = [track for track in payload.tracks if track.kind.value == "video"]
    audio_tracks = [track for track in payload.tracks if track.kind.value == "audio"]

    with tempfile.TemporaryDirectory(prefix="flowengine-render-") as tmp_root:
        temp_dir = Path(tmp_root)
        video_track_paths = [
            _build_video_track(
                track=track,
                track_index=track_index,
                total_duration_sec=payload.total_duration_sec,
                width=width,
                height=height,
                temp_dir=temp_dir,
            )
            for track_index, track in enumerate(video_tracks)
            if track.clips
        ]

        command: list[str] = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r={FRAME_RATE}:d={payload.total_duration_sec}",
        ]
        for track_path in video_track_paths:
            command.extend(["-i", str(track_path)])

        audio_inputs: list[tuple[int, TimelineClip]] = []
        for track in audio_tracks:
            for clip in sorted(track.clips, key=lambda item: (item.start_sec, item.asset_id)):
                source_path = _resolve_asset_path(clip.asset_id)
                if not source_path.is_file():
                    raise FileNotFoundError(f"asset not found: {clip.asset_id}")
                command.extend(["-i", str(source_path)])
                audio_inputs.append((len(command), clip))

        filter_steps = [f"[0:v]format=yuv420p[base]"]
        video_label = "base"
        for input_index in range(len(video_track_paths)):
            next_label = f"v{input_index}"
            filter_steps.append(
                f"[{video_label}][{input_index + 1}:v]overlay=format=auto[{next_label}]"
            )
            video_label = next_label

        audio_labels: list[str] = []
        first_audio_input_index = 1 + len(video_track_paths)
        for audio_offset, clip in enumerate((clip for _, clip in audio_inputs)):
            input_index = first_audio_input_index + audio_offset
            delay_ms = int(round(clip.start_sec * 1000))
            trim_in = clip.trim_in or 0.0
            label = f"a{audio_offset}"
            filter_steps.append(
                (
                    f"[{input_index}:a]"
                    f"atrim=start={trim_in}:duration={clip.duration_sec},"
                    f"asetpts=PTS-STARTPTS,"
                    f"adelay={delay_ms}|{delay_ms}"
                    f"[{label}]"
                )
            )
            audio_labels.append(label)

        audio_output_label = ""
        if audio_labels:
            mixed_inputs = "".join(f"[{label}]" for label in audio_labels)
            audio_output_label = "mixed_audio"
            if len(audio_labels) == 1:
                filter_steps.append(
                    f"{mixed_inputs}apad=pad_dur={payload.total_duration_sec},"
                    f"atrim=0:{payload.total_duration_sec}[{audio_output_label}]"
                )
            else:
                filter_steps.append(
                    f"{mixed_inputs}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
                    f"apad=pad_dur={payload.total_duration_sec},"
                    f"atrim=0:{payload.total_duration_sec}[{audio_output_label}]"
                )

        filter_complex = ";".join(filter_steps)
        command.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                f"[{video_label}]",
            ]
        )
        if audio_output_label:
            command.extend(["-map", f"[{audio_output_label}]"])
        else:
            command.append("-an")

        command.extend(
            [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
        )
        if audio_output_label:
            command.extend(["-c:a", "aac", "-ar", str(AUDIO_SAMPLE_RATE)])
        command.append(str(output_path))

        try:
            _run_ffmpeg(command)
        except Exception:
            _cleanup_partial_output(output_path)
            raise
