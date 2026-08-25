"""`MTakePlayblaster` renders one previs shot's primary take into a preview clip
for the viewer."""

from __future__ import annotations

from dataclasses import dataclass

import maya.cmds as mc

from pipe.core.playblast import Playblaster, PreviewClip
from pipe.core.shotgrid import Shot
from pipe.dcc.maya.playblast.previs.capture import capture_cut
from pipe.dcc.maya.playblast.shot.config import dummy_shot
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
        capture_cut(
            path, self._config.camera, self._config.source_in, self._config.source_out
        )


__all__ = [
    "MTakeConfig",
    "MTakePlayblaster",
]
