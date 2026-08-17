"""`MTakePlayblaster` renders one previs shot's primary into a HUD-burned
preview clip. The viewer's "Send to Edit" turns that preview into a take."""

from __future__ import annotations

from dataclasses import dataclass, field

import maya.cmds as mc

from pipe.core.playblast import Playblaster, PreviewClip
from pipe.core.shotgrid import Shot
from pipe.dcc.maya.playblast.previs._viewport import apply_viewport_options
from pipe.dcc.maya.playblast.previs.capture import capture_cut
from pipe.dcc.maya.playblast.shot.config import dummy_shot
from pipe.dcc.maya.util.selection import maintain_selection
from pipe.dcc.maya.util.time import scene_frame_rate


@dataclass
class MTakeConfig:
    """Inputs for one take playblast.

    `camera` is the shot's primary namespace
    `code` labels the temp frames and the virtual shot.
    `cut_in`/`cut_out` are the same frame range the dailies sequence would
                       render for this shot, so a take matches that shot's
                       slice of the dailies.
    """

    camera: str
    code: str
    cut_in: int
    cut_out: int
    viewport_options: dict[str, bool] = field(default_factory=dict)


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
            virtual_shot = dummy_shot(
                code=self._config.code,
                cut_in=self._config.cut_in,
                cut_out=self._config.cut_out,
                cut_duration=max(0, self._config.cut_out - self._config.cut_in + 1),
            )
            return [super()._do_playblast(virtual_shot, tails=(0, 0))]

    def _write_images(self, shot: Shot, path: str) -> None:  # type: ignore[override]
        del shot  # frame range comes from `_config`, not the virtual shot
        capture_kwargs = apply_viewport_options({}, self._config.viewport_options)
        capture_cut(
            path,
            self._config.camera,
            self._config.cut_in,
            self._config.cut_out,
            capture_kwargs,
        )


__all__ = [
    "MTakeConfig",
    "MTakePlayblaster",
]
