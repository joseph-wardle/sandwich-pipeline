"""A playblast's FFmpeg steps: burn the HUD onto its PNG frames, then encode
them with a preset."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import ffmpeg  # type: ignore[import-untyped]

from pipe.core.hud import HudContent, apply_hud
from pipe.core.playblast.presets import FFmpegPreset

log = logging.getLogger(__name__)


class FFmpegEncodeError(RuntimeError):
    """Raised when an FFmpeg encode invocation exits non-zero. Captures
    stdout/stderr for production-debug logs."""

    @classmethod
    def from_exc(cls, exc: ffmpeg.Error, output_path: Path) -> "FFmpegEncodeError":
        stdout = exc.stdout.decode() if exc.stdout else ""
        stderr = exc.stderr.decode() if exc.stderr else ""
        log.error(
            "FFmpeg encode failed for %s.\nstdout:%s\nstderr:%s",
            output_path,
            stdout,
            stderr,
        )
        return cls(f"FFmpeg encode failed for {output_path}: {stderr or stdout}")


def timecode(frame: int, frame_rate: int) -> str:
    """Non-drop SMPTE timecode of `frame`, counting frame 0 as 00:00:00:00."""
    seconds, frames = divmod(frame, frame_rate)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}:{frames:02}"


def build_image_input_chain(
    image_pattern: str,
    *,
    start_frame: int,
    frame_rate: int,
) -> Any:
    """Return an ffmpeg input chain for a zero-padded PNG sequence.

    `image_pattern` is the printf-style path like `/tmp/foo.%04d.png`.
    """
    return ffmpeg.input(image_pattern, start_number=start_frame, r=frame_rate)


def burn_hud_frames(
    input_pattern: str,
    output_pattern: str,
    content: HudContent,
    resolution: tuple[int, int],
    *,
    start_frame: int,
) -> None:
    """Burn `content` onto a PNG sequence, writing a sibling PNG sequence."""
    chain = apply_hud(
        ffmpeg.input(input_pattern, start_number=start_frame), content, resolution
    )
    try:
        ffmpeg.output(
            chain, output_pattern, start_number=start_frame
        ).overwrite_output().run()
    except ffmpeg.Error as exc:
        raise FFmpegEncodeError.from_exc(exc, Path(output_pattern)) from exc


def encode_movie(
    input_chain: Any,
    *,
    output_path: Path,
    preset: FFmpegPreset,
    frame_rate: int,
    start_frame: int = 0,
) -> Path:
    """Run a single FFmpeg encode of `input_chain` to `output_path`.

    Raises `FFmpegEncodeError` on non-zero exit, with stdout/stderr logged
    via `log.error` for production-side debugging.
    """
    try:
        ffmpeg.output(
            input_chain,
            str(output_path),
            **preset.out_kwargs,
            timecode=timecode(start_frame, frame_rate),
            r=frame_rate,
        ).overwrite_output().run()
    except ffmpeg.Error as exc:
        raise FFmpegEncodeError.from_exc(exc, output_path) from exc
    return output_path


__all__ = [
    "FFmpegEncodeError",
    "build_image_input_chain",
    "burn_hud_frames",
    "encode_movie",
    "timecode",
]
