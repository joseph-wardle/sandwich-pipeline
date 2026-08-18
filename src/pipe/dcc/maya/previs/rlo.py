"""Break out a previs shot: cut the open scene down to one shot's content.

Break-out re-stages the previs session in place rather than exporting from it,
so this module is the surgery half. It leaves a scene holding exactly what the
shot delivers, retimed to open on 1001 and stripped of everything that made the
file a previs sequence. Building the RLO stage and saving it is the caller's job.

Nothing here writes to disk; every function either answers a question about the
shot or mutates the open Maya scene.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import maya.cmds as mc

from pipe.core.previs import codes
from pipe.core.shotgrid.paths import build_shot_path
from pipe.dcc.maya.util.on_open import remove_on_open_node

from . import cameras
from .state import FRAME_START, PrevisShot, PrevisState, delete_state

log = logging.getLogger(__name__)

# Frames carried past each end of the shot so the RLO has room to ease animation
# in and settle the camera. The delivered cut range excludes them.
HANDLE_FRAMES = 8

# `cutKey` time ranges are inclusive, so the keys sitting on the handle
# boundaries need a nudge to stay out of the cut.
_EPS = 0.001
_FAR_PAST = -1_000_000.0
_FAR_FUTURE = 1_000_000.0

_TIME_CURVE_TYPES = ("animCurveTA", "animCurveTL", "animCurveTT", "animCurveTU")


class BreakOutError(Exception):
    """A failure an artist can act on; its message is safe to show in a dialog."""


def rlo_path(shot_code: str, prod_root: Path) -> Path:
    """Spelled out from a sticky code rather than reused from `maya_rlo_stream`,
    which needs a ShotGrid `Shot` the panel would have to fetch."""
    return prod_root / build_shot_path(shot_code) / "rlo" / f"{shot_code}.mb"


def is_broken_out(shot: PrevisShot, prod_root: Path) -> bool:
    """True once an RLO scene exists on disk for this shot's sticky code.

    Says nothing about whether it still matches the live previs scene.
    """
    return bool(shot.code) and rlo_path(shot.code, prod_root).exists()


def cut_range(shot: PrevisShot) -> tuple[int, int]:
    """The shot's frame range once delivered (handles excluded)."""
    return FRAME_START, FRAME_START + shot.primary_duration - 1


def slice_scene_to_shot(shot: PrevisShot, state: PrevisState) -> None:
    """Cut the open scene down to `shot`, retimed to start at `FRAME_START`."""
    code = _require_code(shot)
    primary = _require_primary(shot)

    _drop_other_shot_cameras(shot, state)
    _rename_primary(primary, code)

    mc.currentTime(shot.source_in)
    _trim_keys(shot.source_in - HANDLE_FRAMES, shot.source_out + HANDLE_FRAMES)
    _retime(FRAME_START - shot.source_in)
    _strip_previs_scaffold()
    _frame_playback(shot)


def _require_code(shot: PrevisShot) -> str:
    if not shot.code.strip():
        raise BreakOutError(
            "This shot has no sequence code yet. Give it a code (e.g. A_010) "
            "before breaking it out."
        )
    try:
        return codes.normalize_code(shot.code)
    except ValueError as exc:
        raise BreakOutError(str(exc)) from exc


def _require_primary(shot: PrevisShot) -> str:
    label = shot.code or shot.id
    if not shot.primary:
        raise BreakOutError(
            f"Shot {label} has no primary camera, so there is nothing to deliver."
        )
    if not cameras.is_live(shot.primary):
        raise BreakOutError(
            f"Shot {label}'s primary camera ({shot.primary}) is no longer in the "
            "scene. Promote another take, or re-create the camera."
        )
    return shot.primary


def _drop_other_shot_cameras(shot: PrevisShot, state: PrevisState) -> None:
    """Remove the camera rigs belonging to every other shot in the sequence."""
    keep = set(shot.namespaces)
    for other in state.shots:
        if other.id == shot.id:
            continue
        for namespace in other.namespaces:
            if namespace not in keep and cameras.is_live(namespace):
                _remove_namespace(namespace)


def _remove_namespace(namespace: str) -> None:
    reference = _reference_node_for(namespace)
    if reference:
        mc.file(removeReference=True, referenceNode=reference)
    else:
        mc.namespace(removeNamespace=f":{namespace}", deleteNamespaceContent=True)


def _reference_node_for(namespace: str) -> str | None:
    """The reference node behind `namespace`, or None if it holds local nodes."""
    for node in mc.ls(f"{namespace}:*", long=True) or []:
        if mc.referenceQuery(node, isNodeReferenced=True):
            return cast(str, mc.referenceQuery(node, referenceNode=True))
    return None


def _rename_primary(namespace: str, code: str) -> None:
    """Name the delivered camera after the shot, so the RLO opens on `A_010:...`."""
    if namespace == code:
        return
    if not cameras.rename_camera(namespace, code):
        raise BreakOutError(
            f"Could not rename the camera {namespace} to {code} — something else "
            f"in the scene already uses the name {code}."
        )


def _trim_keys(keep_start: int, keep_end: int) -> None:
    """Drop every key outside the handled range, on every curve in the scene."""
    _warn_referenced_animation()
    for span in ((_FAR_PAST, keep_start - _EPS), (keep_end + _EPS, _FAR_FUTURE)):
        curves = _anim_curves()
        if curves:
            mc.cutKey(*curves, time=span, clear=True)


def _retime(offset: int) -> None:
    curves = _anim_curves()
    if offset and curves:
        mc.keyframe(*curves, edit=True, relative=True, timeChange=offset)


def _anim_curves() -> list[str]:
    curves: list[str] = []
    for curve_type in _TIME_CURVE_TYPES:
        curves += cast("list[str]", mc.ls(type=curve_type) or [])
    return curves


def _warn_referenced_animation() -> None:
    """Maya refuses to trim or retime a curve that lives inside a reference, and
    does it silently."""
    referenced = [
        c for c in _anim_curves() if mc.referenceQuery(c, isNodeReferenced=True)
    ]
    if referenced:
        log.warning(
            "%d referenced animation curves cannot be retimed and will keep their "
            "previs frame numbers: %s",
            len(referenced),
            ", ".join(sorted(referenced)[:5]),
        )


def _strip_previs_scaffold() -> None:
    """Drop what makes this a previs file: the state node, the previs open hook
    and the sequence stage."""
    delete_state()
    remove_on_open_node()
    for shape in mc.ls(type="mayaUsdProxyShape", long=True) or []:
        parents = mc.listRelatives(shape, parent=True, fullPath=True) or []
        mc.delete(shape)
        for parent in parents:
            if not mc.listRelatives(parent, children=True):
                mc.delete(parent)


def _frame_playback(shot: PrevisShot) -> None:
    """Frame what actually travelled, handles included."""
    cut_in, cut_out = cut_range(shot)
    start, end = cut_in - HANDLE_FRAMES, cut_out + HANDLE_FRAMES
    mc.playbackOptions(
        animationStartTime=start, animationEndTime=end, minTime=start, maxTime=end
    )
    mc.currentTime(cut_in)


__all__ = [
    "HANDLE_FRAMES",
    "BreakOutError",
    "cut_range",
    "is_broken_out",
    "rlo_path",
    "slice_scene_to_shot",
]
