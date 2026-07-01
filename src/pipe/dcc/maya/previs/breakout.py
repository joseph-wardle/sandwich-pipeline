"""Break out a previs shot to its RLO Maya scene.

A destructive full re-bake: slices the live previs scene down to one shot,
retimes it to start at frame 1001, trims keys to the shot range plus 8-frame
handles, and writes `shot/<code>/rlo/<code>.mb`. Off-shot cameras are dropped,
and cut_in/cut_out/cut_duration are stamped onto the ShotGrid Shot. Does not
bake cam.usd.

The slice is cut from the open scene in place, so the bake saves the previs file
first, mutates it, exports, then reopens it to restore the artist's session.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import maya.cmds as mc
from pipe.core.shotgrid import Shot, ShotGrid
from pipe.core.util.paths import get_production_path

from . import cameras, playback, status
from .state import PrevisShot, PrevisState

log = logging.getLogger(__name__)

HANDLE_FRAMES = 8

_FAR_PAST = -1_000_000.0
_FAR_FUTURE = 1_000_000.0
_EPS = 0.001


def break_out_shot(
    previs_shot: PrevisShot,
    sg_shot: Shot,
    shot_range: tuple[int, int],
    conn: ShotGrid,
) -> Path:
    """Bake `previs_shot` to its RLO scene and stamp the shot's cut range.

    `shot_range` is the shot's global previs window from `compute_shot_ranges`.
    Mutates the open scene, then reopens the previs file before returning so the
    artist's session is restored. Returns the written `rlo/<code>.mb` path.
    """
    if not previs_shot.primary:
        raise ValueError(f"Previs shot {previs_shot.id} has no primary camera.")
    if not sg_shot.code:
        raise ValueError(f"ShotGrid shot {sg_shot.id} has no code.")

    previs_path = _current_scene_path()
    rlo = status.rlo_path(sg_shot.code, get_production_path())
    rlo.parent.mkdir(parents=True, exist_ok=True)

    baked_hash = cameras.compute_animation_hash(previs_shot.primary)

    mc.file(save=True)
    try:
        _slice_to_shot(previs_shot.primary, shot_range)
        mc.file(
            str(rlo),
            exportAll=True,
            type="mayaBinary",
            force=True,
        )
    finally:
        mc.file(str(previs_path), open=True, force=True)

    start, end = shot_range
    cut_out = playback.FRAME_START + (end - start)
    conn.set_shot_cut_range(sg_shot, cut_in=playback.FRAME_START, cut_out=cut_out)
    previs_shot.rlo_animation_hash = baked_hash
    return rlo


def break_out_sequence(state: PrevisState, conn: ShotGrid) -> list[Path]:
    """Break out every paired shot, in order. Returns the written RLO paths.

    Each shot is a full save/mutate/export/reopen cycle, so the scene is back to
    clean previs before the next one is sliced.
    """
    ranges = playback.compute_shot_ranges(state)
    paths: list[Path] = []
    for shot in state.shots:
        if not shot.shotgrid_code:
            log.info("Skipping unpaired previs shot %s.", shot.id)
            continue
        sg_shot = conn.get_shot(code=shot.shotgrid_code)
        paths.append(break_out_shot(shot, sg_shot, ranges[shot.id], conn))
    return paths


def _slice_to_shot(keep_namespace: str, shot_range: tuple[int, int]) -> None:
    """Reduce the open scene to `shot_range`, retimed to start at FRAME_START."""
    start, end = shot_range
    _drop_off_shot_cameras(keep_namespace)
    _trim_keys(start - HANDLE_FRAMES, end + HANDLE_FRAMES)
    _retime(playback.FRAME_START - start)

    lo = playback.FRAME_START - HANDLE_FRAMES
    hi = playback.FRAME_START + (end - start) + HANDLE_FRAMES
    mc.playbackOptions(
        minTime=lo, maxTime=hi, animationStartTime=lo, animationEndTime=hi
    )
    mc.currentTime(playback.FRAME_START)


def _drop_off_shot_cameras(keep_namespace: str) -> None:
    """Remove every previs camera rig except `keep_namespace`."""
    for ns in _camera_namespaces():
        if ns != keep_namespace:
            _remove_namespace(ns)


def _camera_namespaces() -> set[str]:
    """Namespaces of every namespaced camera in the scene (skips persp/top/...)."""
    namespaces: set[str] = set()
    for shape in mc.ls(type="camera", long=True) or []:
        leaf = shape.rsplit("|", 1)[-1]
        if ":" in leaf:
            namespaces.add(leaf.split(":")[0])
    return namespaces


def _remove_namespace(namespace: str) -> None:
    ref = _reference_for_namespace(namespace)
    if ref:
        mc.file(ref, removeReference=True)
    else:
        mc.namespace(removeNamespace=f":{namespace}", deleteNamespaceContent=True)


def _reference_for_namespace(namespace: str) -> str | None:
    for node in mc.ls(f"{namespace}:*", long=True) or []:
        if mc.referenceQuery(node, isNodeReferenced=True):
            return cast(str, mc.referenceQuery(node, filename=True))
    return None


def _trim_keys(keep_start: int, keep_end: int) -> None:
    curves = mc.ls(type="animCurve") or []
    if not curves:
        return
    mc.cutKey(*curves, time=(_FAR_PAST, keep_start - _EPS), clear=True)
    mc.cutKey(*curves, time=(keep_end + _EPS, _FAR_FUTURE), clear=True)


def _retime(offset: int) -> None:
    if offset == 0:
        return
    curves = mc.ls(type="animCurve") or []
    if curves:
        mc.keyframe(*curves, edit=True, relative=True, timeChange=offset)


def _current_scene_path() -> Path:
    name = cast(str, mc.file(query=True, sceneName=True))
    if not name:
        raise RuntimeError(
            "Save the previs scene before breaking out — the bake reopens it "
            "afterward to restore your session."
        )
    return Path(name)
