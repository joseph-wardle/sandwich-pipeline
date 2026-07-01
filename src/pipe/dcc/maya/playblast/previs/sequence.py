"""`MSequencePlayblaster` — stitches every previs shot's primary into one MP4.

Each cut runs as its own `capture()` call into a *shared* image basename.
Because the previs sequencer lays shots out contiguously starting at frame
1001, the per-cut PNGs land at non-overlapping frame numbers and together
form one continuous sequence — one encode pass produces one MP4.

HUD lines are burned in by `pipe.core.hud.apply_hud` during encode (called by the
`Playblaster` base after `_write_images`), not by Maya during capture.
`show_ornaments=False` keeps Maya from drawing its own HUD on top.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path

import maya.cmds as mc
from mayacapture.capture import capture  # type: ignore[import-not-found]

from pipe.core.hud import (
    ARTIST,
    HudContent,
    labeled_line,
    line_date,
    line_shot,
)
from pipe.core.playblast import FFmpegPreset, Playblaster
from pipe.core.shotgrid import Shot
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.playblast.previs._viewport import apply_viewport_options
from pipe.dcc.maya.playblast.shot.config import dummy_shot
from pipe.dcc.maya.previs.cameras import resolve_camera_node
from pipe.dcc.maya.util.selection import maintain_selection

log = logging.getLogger(__name__)


CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 720


@dataclass
class MSequenceConfig:
    """Inputs for one sequence playblast.

    `cuts` lists every shot's `(camera, start_frame, end_frame)` in playback
    order. `proxy_shot` is the ShotGrid Shot the sequence is anchored to (e.g.
    `A_previs`) — the ShotGrid Version uploads against it.
    """

    cuts: list[tuple[str, int, int]]
    proxy_shot: Shot
    paths: dict[FFmpegPreset, list[Path | str]]
    viewport_options: dict[str, bool] = field(default_factory=dict)

    def final_output_paths(self) -> list[Path]:
        out: list[Path] = []
        for preset, bases in self.paths.items():
            for base in bases:
                out.append(Path(str(base) + "." + preset.ext))
        return out

    def frame_range(self) -> tuple[int, int]:
        if not self.cuts:
            return (0, 0)
        return (self.cuts[0][1], self.cuts[-1][2])


class MSequencePlayblaster(Playblaster):
    _config: MSequenceConfig

    def configure(self, config: MSequenceConfig) -> MSequencePlayblaster:
        self._config = config
        return self

    def playblast(self) -> None:
        with maintain_selection():
            mc.select(clear=True)
            cut_in, cut_out = self._config.frame_range()
            virtual_shot = dummy_shot(
                code=self._config.proxy_shot.code or "previs",
                cut_in=cut_in,
                cut_out=cut_out,
                cut_duration=max(0, cut_out - cut_in + 1),
            )
            super()._do_playblast(virtual_shot, self._config.paths, tails=(0, 0))

    def _hud_content(self, shot: Shot, start_frame: int) -> HudContent:
        # Per-cut camera labels can't sit in a single static drawtext line
        # because cameras change at every cut boundary. v1 shows sequence-
        # level info (artist, sequence label, date) + the auto frame counter;
        # mapping frame → previs-shot stays a mental exercise for now.
        left_lines = (
            labeled_line(ARTIST, resolve_artist_display_name()),
            line_shot(shot.code or ""),
        )
        right_lines = (line_date(),)
        return HudContent(
            left_lines=left_lines,
            right_lines=right_lines,
            frame_start=start_frame,
        )

    def _write_images(self, shot: Shot, path: str) -> None:  # type: ignore[override]
        del shot  # we drive frame ranges off `_config.cuts`, not the virtual shot
        capture_kwargs = apply_viewport_options({}, self._config.viewport_options)
        for camera, cut_in, cut_out in self._config.cuts:
            capture(
                width=CAPTURE_WIDTH,
                height=CAPTURE_HEIGHT,
                filename=path,
                start_frame=cut_in,
                end_frame=cut_out,
                camera=resolve_camera_node(camera),
                format="image",
                compression="png",
                off_screen=True,
                show_ornaments=False,
                overwrite=True,
                maintain_aspect_ratio=False,
                viewer=0,
                **copy.deepcopy(capture_kwargs),
            )


__all__ = [
    "MSequenceConfig",
    "MSequencePlayblaster",
]
