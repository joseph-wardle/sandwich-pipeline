from __future__ import annotations

import logging
import re
import shutil
from abc import ABCMeta, abstractmethod
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core import telemetry
from core.playblast.encoding import build_image_input_chain, encode_movie
from core.playblast.presets import FFmpegPreset
from core.playblast.tempdir import resolve_playblast_tempdir

if TYPE_CHECKING:
    from core.shotgrid import Shot


log = logging.getLogger(__name__)


class PlayblastError(Exception):
    """Raised when playblast image-write, encode, or copy steps fail.

    `error_code` is read by `telemetry.record()` to classify the event.
    """

    error_code = "PLAYBLAST_FAILED"


class Playblaster(metaclass=ABCMeta):
    """Cross-DCC base for playblasters. Uses FFmpeg to encode videos.

    Subclasses implement `_write_images` to dump a PNG sequence; this base
    handles encoding via FFmpeg, copying to multiple output paths, post-
    processing for VLC compatibility, and emitting telemetry.
    """

    fps: int = 24

    @abstractmethod
    def _write_images(self, shot: Shot, path: str) -> None:
        pass

    def _run_postprocess(self, video_path: Path) -> None:
        """Optional post-encode pass on each final output path.

        Default is a no-op. DCC-specific subclasses may override to add
        steps that need runtime DCC state — HUD burn-in via FFmpeg
        `drawtext`, slate-frame insertion, LUT application, etc. — by
        mutating the file at `video_path` in place.

        Encoding format choices belong on `FFmpegPreset.out_kwargs`,
        not here: this hook runs *after* the desired codec is already on
        disk, so don't re-encode it.
        """
        return

    def _do_playblast(
        self,
        shot: Shot,
        out_paths: dict[FFmpegPreset, list[Path | str]] | None = None,
        tails: tuple[int, int] = (0, 0),
    ) -> None:
        out_paths = out_paths or {}

        tempdir = self._resolve_tempdir()
        image_basename = self._image_basename(shot)
        self._cleanup_temp_files(tempdir, image_basename)

        cut_in, cut_out = shot.frame_range
        frame_start = cut_in - tails[0]
        frame_end = cut_out + tails[1]
        common_payload: dict[str, object] = {
            "frame_start": frame_start,
            "frame_end": frame_end,
            "fps": max(1, int(self.fps)),
        }

        # Image write / frame normalize / ffmpeg input-chain build is shared
        # work for every preset in this call, so it runs once on the first
        # preset's telemetry event. A failure there is recorded against that
        # preset (the one the artist actually triggered) and the propagating
        # PlayblastError skips the remaining presets in out_paths.
        encoded_input: Any = None
        for preset, paths in out_paths.items():
            with telemetry.record(
                telemetry.EVENT_PLAYBLAST_CREATE,
                payload={
                    **common_payload,
                    "preset": self._preset_name(preset),
                    "output_count": len(paths),
                },
                shot=shot,
            ) as telemetry_event:
                if encoded_input is None:
                    try:
                        self._write_images(shot, str(tempdir / image_basename))
                    except Exception as exc:
                        raise PlayblastError(
                            str(exc) or exc.__class__.__name__
                        ) from exc
                    self._normalize_frame_filenames(tempdir, image_basename)
                    encoded_input = self._build_ffmpeg_input(
                        shot, tempdir, image_basename, frame_start
                    )

                final_paths = self._encode_and_publish_preset(
                    shot=shot,
                    preset=preset,
                    paths=paths,
                    encoded_input=encoded_input,
                    tempdir=tempdir,
                    image_basename=image_basename,
                    start_frame=frame_start,
                )
                telemetry_event.update(output_count=len(final_paths))

        if not log.isEnabledFor(logging.DEBUG):
            self._cleanup_temp_files(tempdir, image_basename)

    @abstractmethod
    def playblast(self) -> None:
        """Trigger a playblast. Concrete implementations build inputs from
        configured state and call `super()._do_playblast(shot, out_paths, tails)`."""
        pass

    # ------------------------------------------------------------------
    # Pipeline steps (small, single-responsibility helpers).
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_tempdir() -> Path:
        return resolve_playblast_tempdir()

    @staticmethod
    def _image_basename(shot: Shot) -> str:
        return "playblast_temp." + (shot.code or "")

    @staticmethod
    def _cleanup_temp_files(tempdir: Path, basename: str) -> None:
        for path in tempdir.glob(basename + "*"):
            path.unlink()

    @staticmethod
    def _normalize_frame_filenames(tempdir: Path, basename: str) -> None:
        # Houdini emits negative frame numbers as `name.-3.png`; ffmpeg's
        # image2 demuxer needs fixed-width zero-padded numbers
        # (`name.-0003.png`). Rewrite both signs to a uniform width.
        pattern = re.compile(rf"{re.escape(basename)}\.(\-?\d+)\.png$")
        for path in tempdir.glob(f"{basename}.*.png"):
            match = pattern.match(path.name)
            if not match:
                continue
            new_name = f"{basename}.{_padded_signed_int(int(match.group(1)))}.png"
            path.rename(path.with_name(new_name))

    def _build_ffmpeg_input(
        self, shot: Shot, tempdir: Path, basename: str, start_frame: int
    ) -> Any:
        del shot  # base impl ignores shot context; HPlayblaster's HUD uses it
        return build_image_input_chain(
            str(tempdir / basename) + ".%04d.png",
            start_frame=start_frame,
            frame_rate=self.fps,
        )

    def _encode_preset(
        self,
        input_chain: Any,
        preset: FFmpegPreset,
        tempdir: Path,
        basename: str,
        start_frame: int,
    ) -> Path:
        return encode_movie(
            input_chain,
            output_path=Path(str(tempdir / basename) + "." + preset.ext),
            preset=preset,
            frame_rate=self.fps,
            start_frame=start_frame,
        )

    @staticmethod
    def _copy_outputs(
        source: Path,
        paths: list[Path | str],
        ext: str,
    ) -> list[Path]:
        final_paths: list[Path] = []
        for raw_path in paths:
            destination = Path(str(raw_path) + "." + ext)
            if not destination.parent.exists():
                destination.parent.mkdir(mode=0o770, parents=True)
            shutil.copyfile(source, destination)
            final_paths.append(destination)
        return final_paths

    def _safe_run_postprocess(self, final_path: Path) -> None:
        try:
            self._run_postprocess(final_path)
        except Exception as exc:
            log.error("Post-process failed for %s: %s", final_path, exc)

    def _encode_and_publish_preset(
        self,
        *,
        shot: Shot,
        preset: FFmpegPreset,
        paths: list[Path | str],
        encoded_input: Any,
        tempdir: Path,
        image_basename: str,
        start_frame: int,
    ) -> list[Path]:
        """Encode one preset, copy to all destinations, run post-process.

        Returns the destination paths produced
        """
        del shot  # parity with `_build_ffmpeg_input`; HUD subclasses may want this
        try:
            preset_temp = self._encode_preset(
                encoded_input, preset, tempdir, image_basename, start_frame
            )
        except Exception as exc:
            raise PlayblastError(str(exc) or exc.__class__.__name__) from exc

        try:
            final_paths = self._copy_outputs(preset_temp, paths, preset.ext)
        except Exception as exc:
            raise PlayblastError(str(exc) or exc.__class__.__name__) from exc

        # Post-process is best-effort — failure does not invalidate the playblast.
        for final_path in final_paths:
            self._safe_run_postprocess(final_path)

        return final_paths

    @staticmethod
    def _preset_name(preset: object | None) -> str:
        if isinstance(preset, Enum):
            normalized = str(preset.name).strip().lower()
            if normalized:
                return normalized
        if preset is None:
            return "unknown"
        normalized = str(preset).strip().lower()
        return normalized or "unknown"


def _padded_signed_int(num: int, width: int = 4) -> str:
    """Render `num` as a fixed-width zero-padded integer, preserving a leading
    `-` for negatives but emitting no sign for positives.

    `f"{num:+05d}"` gives `+0003`/`-0003`; we strip the `+` so positives
    render as `0003`. Width is the *digit* width, so the rendered string is
    `width` chars for positives and `width + 1` for negatives.
    """
    return f"{num:+0{width + 1}d}".replace("+", "")


__all__ = ["Playblaster", "PlayblastError"]
