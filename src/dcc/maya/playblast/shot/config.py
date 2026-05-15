from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, cast

from core.playblast import FFmpegPreset
from core.shotgrid import Shot

log = logging.getLogger(__name__)


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
class MShotDialogConfig:
    """Information needed to add a shot to the playblast dialog
    id: str
        Unique id for this shot
    name: str
        Display name of the shot
    save_locs: list[tuple[SaveLocation, bool]]
        List of save locations, paired with their default enable value
    """

    id: str
    name: str
    save_locs: list[tuple[SaveLocation, bool]]


@dataclass
class MShotPlayblastConfig:
    """`camera` is ignored when `use_sequencer=True`.
    `pass_label` adds a `Pass: <label>` line to the HUD
    (anim uses this for blocking/polish tags).
    `version_label` / `version_title` are the resolved HUD strings for this
    scene's latest saved version; both `None` when there's no version to show"""

    camera: str | None
    shot: Shot
    paths: dict[FFmpegPreset, list[str | Path]] = field(default_factory=dict)
    tails: tuple[int, int] = (0, 0)
    use_sequencer: bool = False
    pass_label: str | None = None
    version_label: str | None = None
    version_title: str | None = None


@dataclass
class MPlayblastConfig:
    """Viewport flags + the shot configs to playblast."""

    dof: bool
    hardware_fog: bool
    lighting: bool
    shadows: bool
    shots: list[MShotPlayblastConfig]
    ssao: bool


class SaveLocation:
    """Information needed for a save location. If a lambda is provided to
    `path` it will call that and return the value"""

    name: str
    preset: FFmpegPreset
    _path: str | Path | Callable[[], str | Path]

    def __init__(
        self,
        name: str,
        path: str | Path | Callable[[], str | Path],
        preset: FFmpegPreset,
    ):
        self.name = name
        self._path = path
        self.preset = preset

    @property
    def path(self) -> str | Path:
        if callable(self._path):
            return cast(Callable[[], str | Path], self._path)()
        return self._path


__all__ = [
    "MPlayblastConfig",
    "MShotDialogConfig",
    "MShotPlayblastConfig",
    "SaveLocation",
    "dummy_shot",
]
