"""Reading a rendered EXR one channel group at a time, through the show's OCIO config, as Nuke would show it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
import OpenEXR
import PyOpenColorIO as ocio

from pipe.core.color import DEFAULT_VIEW, DISPLAY, config_path

RAW_VIEW = "Raw"
BEAUTY = "RGBA"
# Channel suffixes, in the order they fill red, green and blue.
_COMPONENTS = (
    "R",
    "G",
    "B",
    "r",
    "g",
    "b",
    "X",
    "Y",
    "Z",
    "x",
    "y",
    "z",
    "U",
    "V",
    "u",
    "v",
)
_ALPHA = ("A", "a")
# Groups that hold data rather than light, which a display transform would
# distort: their values are shown as they are.
_DATA_GROUPS = re.compile(
    r"^(__|normal|forward|backward|zfiltered|depth|st$|P$|N$|motion)", re.IGNORECASE
)


@dataclass(frozen=True)
class ChannelGroup:
    """An AOV or LPE: up to three channels shown as red, green and blue, and
    its alpha when it has one."""

    name: str
    channels: tuple[str, ...]
    alpha: str | None = None

    @property
    def is_data(self) -> bool:
        return bool(_DATA_GROUPS.match(self.name))


def channel_groups(exr: Path) -> list[ChannelGroup]:
    """The frame's AOVs and LPEs, beauty first."""
    header = OpenEXR.InputFile(str(exr)).header()
    by_group: dict[str, list[str]] = {}
    for channel in header["channels"]:
        group, _, _ = channel.rpartition(".")
        if not group:
            # Bare R, G, B and A are the beauty; any other bare channel stands alone.
            group = BEAUTY if channel in _COMPONENTS + _ALPHA else channel
        by_group.setdefault(group, []).append(channel)
    groups = []
    for name, channels in by_group.items():
        ordered = sorted(
            (c for c in channels if c.rpartition(".")[2] in _COMPONENTS),
            key=lambda c: _COMPONENTS.index(c.rpartition(".")[2]),
        )[:3]
        alpha = next((c for c in channels if c.rpartition(".")[2] in _ALPHA), None)
        # A lone channel such as depth, or an alpha, shows as grey.
        groups.append(ChannelGroup(name, tuple(ordered or channels[:1]), alpha))
    return sorted(groups, key=lambda g: (g.name != BEAUTY, g.is_data, g.name.lower()))


def read(exr: Path, group: ChannelGroup) -> np.ndarray:
    """The group's pixels as float32 height × width × 4: red, green and blue,
    grey when it has one channel, then its alpha, NaN when it has none.

    Safe to call from several threads at once: OpenEXR's File API decodes
    without holding Python's lock, so frames read in parallel. (Its legacy
    InputFile reads only the chosen channels, but holds the lock throughout,
    which kept playback near one frame a second.)
    """
    with OpenEXR.File(str(exr), separate_channels=True) as file:
        channels = file.channels()
        planes = [
            channels[name].pixels.astype(np.float32, copy=False)
            for name in group.channels
        ]
        alpha = (
            channels[group.alpha].pixels.astype(np.float32, copy=False)
            if group.alpha
            else np.full_like(planes[0], np.nan)
        )
    while len(planes) < 3:
        planes.append(planes[-1])
    return np.dstack([*planes, alpha])


EMPTY = "empty: every pixel is black"
NOT_FINITE = "broken: holds NaN or infinite pixels"


@dataclass(frozen=True)
class Thumbnail:
    """A small copy of one channel group, or why there is nothing to show."""

    group: ChannelGroup
    # Height × width × 4, as `read` gives; None when `problem` says why.
    pixels: np.ndarray | None
    problem: str | None = None


def thumbnails(exr: Path, groups: list[ChannelGroup], width: int) -> list[Thumbnail]:
    """Every group of the frame, `width` pixels across, reading the file once.

    A group whose pixels are all black, or that holds NaN or infinite values,
    is reported as a problem rather than shown: it is an LPE or AOV that
    rendered nothing, or that broke.
    """
    with OpenEXR.File(str(exr), separate_channels=True) as file:
        channels = file.channels()
        found = []
        for group in groups:
            try:
                planes = [channels[name].pixels for name in group.channels]
            except KeyError as exc:
                found.append(Thumbnail(group, None, f"missing channel {exc}"))
                continue
            found.append(_thumbnail(group, planes, channels, width))
    return found


def _thumbnail(
    group: ChannelGroup, planes: list[np.ndarray], channels: dict, width: int
) -> Thumbnail:
    # Judged on every pixel, so one bad pixel isn't skipped by the shrink.
    # Data passes aren't judged: depth holds infinity where nothing was hit,
    # and motion vectors are all zero when nothing moves.
    if not group.is_data:
        if not all(np.isfinite(plane).all() for plane in planes):
            return Thumbnail(group, None, NOT_FINITE)
        if not any(np.any(plane) for plane in planes):
            return Thumbnail(group, None, EMPTY)
    step = max(planes[0].shape[1] // max(width, 1), 1)
    small = [plane[::step, ::step].astype(np.float32) for plane in planes]
    while len(small) < 3:
        small.append(small[-1])
    alpha = (
        channels[group.alpha].pixels[::step, ::step].astype(np.float32)
        if group.alpha in channels
        else np.full_like(small[0], np.nan)
    )
    return Thumbnail(group, np.dstack([*small, alpha]))


def views() -> list[str]:
    return list(_config().getViews(DISPLAY))


def default_view(group: ChannelGroup) -> str:
    return RAW_VIEW if group.is_data else DEFAULT_VIEW


def display(pixels: np.ndarray, view: str, exposure: float) -> np.ndarray:
    """8-bit height × width × 3 for the screen: `exposure` stops in scene linear,
    then the show's display transform for `view`. An alpha in `pixels` is left out."""
    shown = np.ascontiguousarray(
        pixels[..., :3] * np.float32(2.0**exposure), dtype=np.float32
    )
    _processor(view).applyRGB(shown)
    return (np.clip(shown, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


@cache
def _config() -> ocio.Config:
    return ocio.Config.CreateFromFile(str(config_path()))


@cache
def _processor(view: str) -> ocio.CPUProcessor:
    transform = ocio.DisplayViewTransform(
        src=ocio.ROLE_SCENE_LINEAR, display=DISPLAY, view=view
    )
    return _config().getProcessor(transform).getDefaultCPUProcessor()
