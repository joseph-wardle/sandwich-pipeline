from __future__ import annotations

from typing import TYPE_CHECKING

import maya.cmds as mc

from pipe.core.hud import (
    ARTIST,
    TITLE,
    HudContent,
    TimedText,
    labeled_line,
    line_date,
    line_shot,
)
from pipe.core.playblast import Playblaster, PreviewClip
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.playblast.capture import capture_frames
from pipe.dcc.maya.playblast.hud import camera_focal_lines
from pipe.dcc.maya.playblast.shot.config import MPlayblastConfig, MShotPlayblastConfig
from pipe.dcc.maya.util.selection import maintain_selection
from pipe.dcc.maya.util.time import scene_frame_rate

if TYPE_CHECKING:
    from pipe.core.shotgrid import Shot

# Anim-specific label; the cross-DCC ones live in `pipe.core.hud`.
_LABEL_PASS = "Pass"


class MPlayblaster(Playblaster):
    _config: MPlayblastConfig
    _current_shot_config: MShotPlayblastConfig | None

    def __init__(self) -> None:
        self._current_shot_config = None

    def configure(self, config: MPlayblastConfig) -> MPlayblaster:
        self._config = config
        return self

    def _write_images(self, shot: Shot, path: str) -> None:
        shot_config = self._current_shot_config
        cut_in, cut_out = shot.frame_range
        head, tail = shot_config.tails if shot_config else (0, 0)
        capture_frames(
            path,
            shot_config.camera if shot_config else None,
            cut_in - head,
            cut_out + tail,
            quality=self._config.quality,
            resolution=self.resolution,
        )

    def _hud_content(self, shot: Shot, start_frame: int) -> HudContent:
        shot_config = self._current_shot_config

        left_lines: list[str] = [labeled_line(ARTIST, resolve_artist_display_name())]
        if shot_config is not None and shot_config.version_title:
            left_lines.append(labeled_line(TITLE, shot_config.version_title))
        if shot_config is not None and shot_config.pass_label:
            left_lines.append(labeled_line(_LABEL_PASS, shot_config.pass_label))
        left_lines.append(
            line_shot(
                shot.code or "",
                version=shot_config.version_label if shot_config else None,
                unsaved=bool(mc.file(query=True, modified=True)),
            )
        )

        end_frame = start_frame
        if shot_config is not None:
            end_frame = shot.frame_range[1] + shot_config.tails[1]

        right_lines: list[str | TimedText] = [line_date()]
        if shot_config is not None and shot_config.camera:
            right_lines.extend(
                camera_focal_lines(str(shot_config.camera), start_frame, end_frame)
            )

        return HudContent(
            left_lines=tuple(left_lines),
            right_lines=tuple(right_lines),
            frame_start=start_frame,
        )

    def _frame_rate(self) -> int:
        return scene_frame_rate()

    def playblast(self) -> list[PreviewClip]:
        with maintain_selection():
            mc.select(clear=True)

            clips: list[PreviewClip] = []
            for shot_config in self._config.shots:
                # Stashed so `_write_images` and `_hud_content` can read per-shot
                # inputs when the base calls them back up the stack.
                self._current_shot_config = shot_config
                try:
                    clip = super()._do_playblast(
                        shot_config.shot,
                        tails=shot_config.tails,
                    )
                finally:
                    self._current_shot_config = None
                clips.append(clip)
            return clips


__all__ = ["MPlayblaster"]
