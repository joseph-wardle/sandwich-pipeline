"""Install/remove the `timeChanged` scriptJob that swaps cameras at shot boundaries."""

from __future__ import annotations

import logging
from typing import cast

import maya.cmds as mc

from . import cameras, monitor
from .state import PrevisState, read_state

log = logging.getLogger(__name__)

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


def resolve_camera_for_frame(state: PrevisState, frame: int) -> str | None:
    """The primary camera of the first shot whose source range covers `frame`.

    Shots may overlap in source time, so "first" means authoring order.
    """
    for shot in state.shots:
        if shot.source_in <= frame <= shot.source_out:
            return shot.primary or None
    return None


def sync_monitor() -> None:
    """Point the monitor at whatever camera the playhead currently sits on."""
    state = read_state()
    if state is None or not state.shots:
        return
    ns = resolve_camera_for_frame(state, int(mc.currentTime(query=True)))
    if not ns:
        return
    shape = cameras.camera_shape_for_namespace(ns)
    if shape:
        monitor.look_through(shape)


def _on_time_changed() -> None:
    sync_monitor()
