from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, cast

from pipe.m.playblast.hud import HudDefinition
from pipe.playblast import FFmpegPreset
from pipe.shotgrid import Shot

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
    """Information needed to playblast a shot.
    Attributes:
        camera: str | None
            Camera to use. Value ignored if `use_sequencer` is set
        shot: Shot
            Shot struct to hold shot code, cut in, cut out, and duration
        paths: dict[FFmpegPreset, list[str | Path]]
            Paths to output to
        tails: tuple[int, int]
            How many frames early/late to start playblasting
        use_sequencer: bool = False
            Whether to playblast from the sequencer. If set to True, `camera`
            will be ignored
    """

    camera: str | None
    shot: Shot
    paths: dict[FFmpegPreset, list[str | Path]] = field(default_factory=dict)
    tails: tuple[int, int] = (0, 0)
    use_sequencer: bool = False

    def set_paths(self, paths: dict[FFmpegPreset, list[str | Path]]) -> None:
        self.paths = paths


@dataclass
class MPlayblastConfig:
    """Information needed to configure a Maya playblast
    Attributes:
        builtin_huds: list[str]
            List of valid Maya builtin HUD names
        custom_huds: list[HudDefinition]
            List of `HudDefinition`s
        dof: bool
            Toggle depth of field
        hardware_fog: bool
            Toggle hardware fog
        lighting: bool
            Toggle viewport lighting
        shadows: bool
            Toggle viewport shadows
        shots: list[MShotPlayblastConfig]
            List of shots to playblast
        ssao: bool
            Toggle viewport screen-space anti-aliasing
    """

    builtin_huds: list[str]
    custom_huds: list[HudDefinition]
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
