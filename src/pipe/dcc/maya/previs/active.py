"""The active shot (which shot owns the current scene frame) and the job that follows it."""

from __future__ import annotations

import logging
from typing import cast

import maya.cmds as mc

from . import cameras, monitor
from .state import PrevisShot, PrevisState, read_state

log = logging.getLogger(__name__)

_SCRIPT_JOB_ID: int | None = None
# Not persisted: selection is a view of the scene, not part of it.
_SELECTED_SHOT_ID: str | None = None


def selected_shot_id() -> str | None:
    return _SELECTED_SHOT_ID


def set_selected_shot(shot_id: str | None) -> None:
    global _SELECTED_SHOT_ID
    _SELECTED_SHOT_ID = shot_id


def active_shot(state: PrevisState, frame: int) -> PrevisShot | None:
    """The selected shot when it covers `frame`, else the first covering shot in list order."""
    covering = [s for s in state.shots if s.source_in <= frame <= s.source_out]
    if not covering:
        return None
    return next((s for s in covering if s.id == _SELECTED_SHOT_ID), covering[0])


def install_camera_callback() -> None:
    """(Re)install the `timeChanged` callback. Safe to call multiple times."""
    global _SCRIPT_JOB_ID
    remove_camera_callback()
    # A new file inherits none of the previous file's shots, so its selection
    # would name a shot that no longer exists.
    set_selected_shot(None)
    _SCRIPT_JOB_ID = cast(int, mc.scriptJob(event=("timeChanged", _on_time_changed)))


def remove_camera_callback() -> None:
    global _SCRIPT_JOB_ID
    if _SCRIPT_JOB_ID is not None and mc.scriptJob(exists=_SCRIPT_JOB_ID):
        mc.scriptJob(kill=_SCRIPT_JOB_ID, force=True)
    _SCRIPT_JOB_ID = None


def sync_monitor() -> None:
    """Point the monitor at the active shot's primary camera."""
    state = read_state()
    if state is None or not state.shots:
        return
    shot = active_shot(state, int(mc.currentTime(query=True)))
    if shot is None or not shot.primary:
        return
    shape = cameras.camera_shape_for_namespace(shot.primary)
    if shape:
        monitor.look_through(shape)


def _on_time_changed() -> None:
    sync_monitor()
