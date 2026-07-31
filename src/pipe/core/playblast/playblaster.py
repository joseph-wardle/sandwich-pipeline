from __future__ import annotations

import logging
import re
from abc import ABCMeta, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from pipe.core import telemetry
from pipe.core.hud import HudContent
from pipe.core.playblast.encoding import burn_hud_frames
from pipe.core.playblast.clip import PreviewClip, padded_frame_number
from pipe.core.playblast.tempdir import create_preview_dir

if TYPE_CHECKING:
    from pipe.core.shotgrid import Shot


log = logging.getLogger(__name__)

DEFAULT_RESOLUTION: tuple[int, int] = (1280, 720)


class PlayblastError(Exception):
    """Raised when playblast image-write or HUD-burn steps fail.

    `error_code` is read by `telemetry.record()` to classify the event.
    """

    error_code = "PLAYBLAST_FAILED"


class Playblaster(metaclass=ABCMeta):
    """Cross-DCC playblast base."""

    fps: int = 24
    resolution: tuple[int, int] = DEFAULT_RESOLUTION

    @abstractmethod
    def _write_images(self, shot: Shot, path: str) -> None:
        pass

    def _hud_content(self, shot: Shot, start_frame: int) -> HudContent:
        del shot, start_frame
        return HudContent()

    def _do_playblast(
        self,
        shot: Shot,
        tails: tuple[int, int] = (0, 0),
    ) -> PreviewClip:
        """Render one shot's playblast: dump PNG frames and burn the HUD onto
        them. The frames feed the viewer; nothing is encoded or delivered here."""
        # Each render gets its own directory
        tempdir = create_preview_dir()
        image_basename = self._image_basename(shot)

        cut_in, cut_out = shot.frame_range
        frame_start = cut_in - tails[0]
        frame_end = cut_out + tails[1]
        hud_content = self._hud_content(shot, frame_start)

        with telemetry.record(
            telemetry.EVENT_PLAYBLAST_CREATE,
            payload={
                "frame_start": frame_start,
                "frame_end": frame_end,
                "fps": max(1, int(self.fps)),
                "preset": "frames",
                "output_count": 0,
            },
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

        return PreviewClip(
            label=shot.code or "playblast",
            frames_dir=tempdir,
            frames_basename=frames_basename,
            frame_start=frame_start,
            frame_end=frame_end,
            fps=self.fps,
        )

    @abstractmethod
    def playblast(self) -> list[PreviewClip]:
        """Trigger a playblast. Concrete implementations build inputs from
        configured state and call `super()._do_playblast(shot, tails)`.

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


__all__ = ["Playblaster", "PlayblastError"]
