from __future__ import annotations

from dataclasses import dataclass

from pipe.core.shotgrid import Shot
from pipe.dcc.maya.playblast.viewport import ViewportQuality


def dummy_shot(code: str, cut_in: int, cut_out: int, cut_duration: int) -> Shot:
    """Generate a generic `Shot` object to hold cut info that doesn't
    correspond to a ShotGrid shot"""
    return Shot(
        code=code,
        id=0,
        assets=[],
        cut_in=cut_in,
        cut_out=cut_out,
        cut_duration=cut_duration,
        sequence=None,
        set=None,
        sets=[],
    )


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
