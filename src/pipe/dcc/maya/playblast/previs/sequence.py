"""`MSequencePlayblaster` — stitches every previs shot's primary into one clip.

Each cut runs as its own `capture_cut()` call into a *shared* image basename.
Because the previs sequencer lays shots out contiguously starting at frame
1001, the per-cut PNGs land at non-overlapping frame numbers and together
form one continuous sequence — one `PreviewClip` for the viewer.

HUD lines are burned onto the frames by the `Playblaster` base after
`_write_images`, not by Maya during capture.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import maya.cmds as mc

from pipe.core.hud import (
    ARTIST,
    HudContent,
    labeled_line,
    line_date,
    line_shot,
)
from pipe.core.playblast import Playblaster, PreviewClip
from pipe.core.shotgrid import Shot
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.playblast.previs._viewport import apply_viewport_options
from pipe.dcc.maya.playblast.previs.capture import capture_cut
from pipe.dcc.maya.playblast.shot.config import dummy_shot
from pipe.dcc.maya.util.selection import maintain_selection
from pipe.dcc.maya.util.time import scene_frame_rate


@dataclass
class MSequenceConfig:
    """Inputs for one sequence playblast.

    `cuts` lists every shot's `(camera, start_frame, end_frame)` in playback
    order. `code` labels the HUD and the clip — the sequence-proxy Shot code
    (e.g. `A_previs`).
    """

    cuts: list[tuple[str, int, int]]
    code: str
    viewport_options: dict[str, bool] = field(default_factory=dict)

    def frame_range(self) -> tuple[int, int]:
        if not self.cuts:
            return (0, 0)
        return (self.cuts[0][1], self.cuts[-1][2])


class MSequencePlayblaster(Playblaster):
    _config: MSequenceConfig

    def configure(self, config: MSequenceConfig) -> MSequencePlayblaster:
        self._config = config
        return self

    def playblast(self) -> list[PreviewClip]:
        self.fps = scene_frame_rate()
        with maintain_selection():
            mc.select(clear=True)
            cut_in, cut_out = self._config.frame_range()
            virtual_shot = dummy_shot(
                code=self._config.code or "previs",
                cut_in=cut_in,
                cut_out=cut_out,
                cut_duration=max(0, cut_out - cut_in + 1),
            )
            return [super()._do_playblast(virtual_shot, tails=(0, 0))]

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
        # Resolve viewport options once; capture_cut deep-copies per cut.
        capture_kwargs = apply_viewport_options({}, self._config.viewport_options)
        for camera, cut_in, cut_out in self._config.cuts:
            capture_cut(path, camera, cut_in, cut_out, capture_kwargs)


__all__ = [
    "MSequenceConfig",
    "MSequencePlayblaster",
]
