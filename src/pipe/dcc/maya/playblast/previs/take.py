"""`MTakePlayblaster` renders one previs shot's primary take into a preview clip
for the viewer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import maya.cmds as mc

from pipe.core.hud import (
    ARTIST,
    FILE,
    UNSAVED_SUFFIX,
    HudContent,
    TimedText,
    labeled_line,
    line_date,
    line_shot,
)
from pipe.core.playblast import Playblaster, PreviewClip
from pipe.core.shotgrid import Shot
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.playblast.capture import capture_frames
from pipe.dcc.maya.playblast.hud import camera_focal_lines
from pipe.dcc.maya.playblast.shot.config import dummy_shot
from pipe.dcc.maya.playblast.viewport import ViewportQuality
from pipe.dcc.maya.previs.cameras import camera_shape_for_namespace, resolve_camera_node
from pipe.dcc.maya.util.selection import maintain_selection
from pipe.dcc.maya.util.time import scene_frame_rate


@dataclass
class MTakeConfig:
    """Inputs for one take playblast; `code` labels the temp frames and the
    virtual shot handed to the `Playblaster` base.

    The range is the shot's *source* range — a capture samples scene frames, so
    the PNGs are numbered in scene time, not cut time.
    """

    camera: str
    code: str
    source_in: int
    source_out: int
    quality: ViewportQuality


class MTakePlayblaster(Playblaster):
    _config: MTakeConfig

    def configure(self, config: MTakeConfig) -> MTakePlayblaster:
        self._config = config
        return self

    def _frame_rate(self) -> int:
        return scene_frame_rate()

    def playblast(self) -> list[PreviewClip]:
        with maintain_selection():
            mc.select(clear=True)
            # The base builds the clip's frame numbering from the shot's cut
            # range, so the source range goes in there — the one place previs'
            # scene time is spelled as a `Shot`'s cut.
            source_in, source_out = self._config.source_in, self._config.source_out
            virtual_shot = dummy_shot(
                code=self._config.code,
                cut_in=source_in,
                cut_out=source_out,
                cut_duration=max(0, source_out - source_in + 1),
            )
            return [super()._do_playblast(virtual_shot, tails=(0, 0))]

    def _write_images(self, shot: Shot, path: str) -> None:  # type: ignore[override]
        del shot  # frame range comes from `_config`, not the virtual shot
        capture_frames(
            path,
            resolve_camera_node(self._config.camera),
            self._config.source_in,
            self._config.source_out,
            quality=self._config.quality,
            resolution=self.resolution,
        )

    def _hud_content(self, shot: Shot, start_frame: int) -> HudContent:
        del shot
        left_lines: list[str] = [labeled_line(ARTIST, resolve_artist_display_name())]
        scene_file = _scene_file()
        if scene_file:
            left_lines.append(labeled_line(FILE, scene_file))
        left_lines.append(line_shot(self._config.code))

        namespace = self._config.camera
        right_lines: list[str | TimedText] = [line_date()]
        right_lines.extend(
            camera_focal_lines(
                camera_shape_for_namespace(namespace) or "",
                start_frame,
                self._config.source_out,
                name=namespace,
            )
        )

        return HudContent(
            left_lines=tuple(left_lines),
            right_lines=tuple(right_lines),
            frame_start=start_frame,
        )


def _scene_file() -> str:
    """The open previs file's name, marked `*` when the scene has drifted from it."""
    scene = mc.file(query=True, sceneName=True)
    if not isinstance(scene, str) or not scene:
        return ""
    modified = UNSAVED_SUFFIX if mc.file(query=True, modified=True) else ""
    return f"{Path(scene).stem}{modified}"


__all__ = [
    "MTakeConfig",
    "MTakePlayblaster",
]
