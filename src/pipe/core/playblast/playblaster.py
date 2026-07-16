from __future__ import annotations

import logging
import re
import shutil
from abc import ABCMeta, abstractmethod
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pipe.core import telemetry
from pipe.core.hud import HudContent
from pipe.core.playblast.encoding import (
    build_image_input_chain,
    burn_hud_frames,
    encode_movie,
)
from pipe.core.playblast.presets import FFmpegPreset
from pipe.core.playblast.preview_spec import PreviewClip, padded_frame_number
from pipe.core.playblast.tempdir import create_preview_dir

if TYPE_CHECKING:
    from pipe.core.shotgrid import Shot


log = logging.getLogger(__name__)

DEFAULT_RESOLUTION: tuple[int, int] = (1280, 720)


class PlayblastError(Exception):
    """Raised when playblast image-write, encode, or copy steps fail.

    `error_code` is read by `telemetry.record()` to classify the event.
    """

    error_code = "PLAYBLAST_FAILED"


class Playblaster(metaclass=ABCMeta):
    """Cross-DCC playblast base. Subclasses implement `_write_images` to dump
    a PNG sequence. This base burns the HUD onto those frames, encodes each
    requested preset via FFmpeg, copies to multiple outputs, post-processes,
    and emits telemetry. Override `_hud_content` to bake in a HUD"""

    fps: int = 24
    resolution: tuple[int, int] = DEFAULT_RESOLUTION

    @abstractmethod
    def _write_images(self, shot: Shot, path: str) -> None:
        pass

    def _hud_content(self, shot: Shot, start_frame: int) -> HudContent:
        del shot, start_frame
        return HudContent()

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
    ) -> PreviewClip:
        """Render one shot's playblast: dump PNG frames, burn the HUD onto
        them, then encode and copy each preset in `out_paths`."""
        out_paths = out_paths or {}

        # Each render gets its own directory
        tempdir = create_preview_dir()
        image_basename = self._image_basename(shot)

        cut_in, cut_out = shot.frame_range
        frame_start = cut_in - tails[0]
        frame_end = cut_out + tails[1]
        hud_content = self._hud_content(shot, frame_start)
        common_payload: dict[str, object] = {
            "frame_start": frame_start,
            "frame_end": frame_end,
            "fps": max(1, int(self.fps)),
        }

        # The frame render + HUD burn feed the viewer and every preset, so
        # they get their own telemetry event.
        with telemetry.record(
            telemetry.EVENT_PLAYBLAST_CREATE,
            payload={**common_payload, "preset": "frames", "output_count": 0},
            shot=shot,
        ):
            try:
                self._write_images(shot, str(tempdir / image_basename))
            except Exception as exc:
                raise PlayblastError(str(exc) or exc.__class__.__name__) from exc
            self._normalize_frame_filenames(tempdir, image_basename)
            frames_basename = self._burn_hud(
                tempdir, image_basename, hud_content, frame_start
            )

        encoded_input = build_image_input_chain(
            str(tempdir / frames_basename) + ".%04d.png",
            start_frame=frame_start,
            frame_rate=self.fps,
        )
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
                final_paths = self._encode_and_publish_preset(
                    shot=shot,
                    preset=preset,
                    paths=paths,
                    encoded_input=encoded_input,
                    tempdir=tempdir,
                    image_basename=frames_basename,
                    start_frame=frame_start,
                )
                telemetry_event.update(output_count=len(final_paths))

        return PreviewClip(
            label=shot.code or "playblast",
            frames_dir=tempdir,
            frames_basename=frames_basename,
            frame_start=frame_start,
            frame_end=frame_end,
            destinations={
                preset: [Path(str(raw)) for raw in paths]
                for preset, paths in out_paths.items()
            },
        )

    @abstractmethod
    def playblast(self) -> list[PreviewClip]:
        """Trigger a playblast. Concrete implementations build inputs from
        configured state and call `super()._do_playblast(shot, out_paths, tails)`.

        Returns one `PreviewClip` per rendered shot, for the caller to hand
        to the viewer."""
        pass

    # ------------------------------------------------------------------
    # Pipeline steps (small, single-responsibility helpers).
    # ------------------------------------------------------------------

    @staticmethod
    def _image_basename(shot: Shot) -> str:
        return "playblast_temp." + (shot.code or "")

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
            new_name = f"{basename}.{padded_frame_number(int(match.group(1)))}.png"
            path.rename(path.with_name(new_name))

    def _burn_hud(
        self,
        tempdir: Path,
        image_basename: str,
        content: HudContent,
        frame_start: int,
    ) -> str:
        """Burn `content` onto the rendered frames as a sibling `.hud`
        sequence. Returns the basename every consumer should read from."""
        if content.is_empty():
            return image_basename
        hud_basename = image_basename + ".hud"
        try:
            burn_hud_frames(
                str(tempdir / image_basename) + ".%04d.png",
                str(tempdir / hud_basename) + ".%04d.png",
                content,
                self.resolution,
                start_frame=frame_start,
            )
        except Exception as exc:
            raise PlayblastError(str(exc) or exc.__class__.__name__) from exc
        return hud_basename

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


__all__ = ["Playblaster", "PlayblastError"]
