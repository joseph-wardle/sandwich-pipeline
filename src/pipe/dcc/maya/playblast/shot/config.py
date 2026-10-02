from __future__ import annotations

from dataclasses import dataclass

from pipe.core.shotgrid import Shot
from pipe.dcc.maya.playblast.viewport import ViewportQuality


def dummy_shot(code: str, cut_in: int, cut_out: int) -> Shot:
    """A `Shot` for a frame range ShotGrid holds no shot for."""
    return Shot(id=0, code=code, cut_in=cut_in, cut_out=cut_out)


@dataclass
class MShotPlayblastConfig:
    """`pass_label` adds a `Pass: <label>` line to the HUD
    (anim uses this for blocking/polish tags).
    `version_label` / `version_title` are the resolved HUD strings for this
    scene's latest saved version; both `None` when there's no version to show"""

    camera: str | None
    shot: Shot
    tails: tuple[int, int] = (0, 0)
    pass_label: str | None = None
    version_label: str | None = None
    version_title: str | None = None


@dataclass
class MPlayblastConfig:
    """What the capture should look like + the shot configs to playblast."""

    quality: ViewportQuality
    shots: list[MShotPlayblastConfig]


__all__ = [
    "MPlayblastConfig",
    "MShotPlayblastConfig",
    "dummy_shot",
]
