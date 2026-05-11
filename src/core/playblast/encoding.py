"""Shared FFmpeg encode primitive used by the cross-DCC `Playblaster` base
class and by Maya turnaround playblasts. The two flows differ in how they
*produce* PNG sequences but share a single encode shape."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import ffmpeg  # type: ignore[import-untyped]

from core.playblast.presets import FFmpegPreset

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


def build_image_input_chain(
    image_pattern: str,
    *,
    start_frame: int,
    frame_rate: int,
) -> Any:
    """Return an ffmpeg input chain for a zero-padded PNG sequence.

    `image_pattern` is the printf-style path like `/tmp/foo.%04d.png`.
    """
    return ffmpeg.input(
        image_pattern,
        start_number=start_frame,
        r=frame_rate,
        # precisely define input colorspace
        colorspace="bt709",
        color_trc="iec61966-2-1",
    ).filter("format", "yuv422p")


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
    timecode = "00:00:{:02}:{:02}".format(
        start_frame // frame_rate,
        start_frame % frame_rate,
    )
    try:
        ffmpeg.output(
            input_chain,
            str(output_path),
            **preset.out_kwargs,
            timecode=timecode,
            r=frame_rate,
        ).overwrite_output().run()
    except ffmpeg.Error as exc:
        raise FFmpegEncodeError.from_exc(exc, output_path) from exc
    return output_path


__all__ = ["FFmpegEncodeError", "build_image_input_chain", "encode_movie"]
