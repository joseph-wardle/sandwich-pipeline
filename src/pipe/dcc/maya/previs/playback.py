"""Install/remove the `timeChanged` scriptJob that swaps cameras at shot boundaries."""

from __future__ import annotations

import logging
from typing import cast

import maya.cmds as mc

from . import cameras, monitor
from .state import PrevisState, read_state

log = logging.getLogger(__name__)

FRAME_START = 1001

_SCRIPT_JOB_ID: int | None = None


def install_camera_callback() -> None:
    """(Re)install the `timeChanged` callback. Safe to call multiple times."""
    global _SCRIPT_JOB_ID
    remove_camera_callback()
    _SCRIPT_JOB_ID = cast(int, mc.scriptJob(event=("timeChanged", _on_time_changed)))


def remove_camera_callback() -> None:
    global _SCRIPT_JOB_ID
    if _SCRIPT_JOB_ID is not None and mc.scriptJob(exists=_SCRIPT_JOB_ID):
        mc.scriptJob(kill=_SCRIPT_JOB_ID, force=True)
    _SCRIPT_JOB_ID = None


def compute_shot_ranges(state: PrevisState) -> dict[str, tuple[int, int]]:
    """Stack shots in order from `FRAME_START`, sizing each by its primary's duration."""
    ranges: dict[str, tuple[int, int]] = {}
    cursor = FRAME_START
    for shot in state.shots:
        end = cursor + max(shot.primary_duration - 1, 0)
        ranges[shot.id] = (cursor, end)
        cursor = end + 1
    return ranges


def resolve_camera_for_frame(
    state: PrevisState,
    ranges: dict[str, tuple[int, int]],
    frame: int,
) -> str | None:
    for shot in state.shots:
        start, end = ranges.get(shot.id, (0, -1))
        if start <= frame <= end:
            return shot.primary or None
    return None


def sync_monitor() -> None:
    """Point the monitor at whatever camera the playhead currently sits on."""
    state = read_state()
    if state is None or not state.shots:
        return
    ranges = compute_shot_ranges(state)
    frame = int(mc.currentTime(query=True))
    ns = resolve_camera_for_frame(state, ranges, frame)
    if not ns:
        return
    shape = cameras.camera_shape_for_namespace(ns)
    if shape:
        monitor.look_through(shape)


def _on_time_changed() -> None:
    sync_monitor()
