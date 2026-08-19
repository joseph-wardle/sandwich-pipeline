"""Break out a previs shot: plan the delivery, then carry it out."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import maya.cmds as mc

from pipe.core.previs import codes
from pipe.core.shot import maya_rlo_stream, shot_owner_for
from pipe.core.shotgrid import (
    Environment,
    Sequence,
    Shot,
    ShotGrid,
    ShotGridError,
    ShotGridNotFound,
    build_shot_path,
)
from pipe.core.util.paths import get_production_path
from pipe.core.versioning import save_version
from pipe.dcc.maya.shotfile.stage import (
    build_shot_stage,
    linked_environments,
    setup_environment,
)
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

# Titles the version an outgoing RLO scene is kept under when a delivery replaces it.
_REPLACED_TITLE = "Before break-out"


class BreakOutError(Exception):
    """A failure an artist can act on; its message is safe to show in a dialog."""


def _rlo_path(shot_code: str) -> Path:
    """Spelled out from a sticky code rather than reused from `maya_rlo_stream`,
    which needs a ShotGrid `Shot` the panel would have to fetch."""
    return (
        get_production_path() / build_shot_path(shot_code) / "rlo" / f"{shot_code}.mb"
    )


def is_broken_out(shot: PrevisShot) -> bool:
    """True once an RLO scene exists on disk for this shot's sticky code.

    Says nothing about whether it still matches the live previs scene.
    """
    return bool(shot.code) and _rlo_path(shot.code).exists()


def _cut_range(shot: PrevisShot) -> tuple[int, int]:
    """The shot's frame range once delivered (handles excluded)."""
    return FRAME_START, FRAME_START + shot.primary_duration - 1


@dataclass(frozen=True)
class DeliveryPlan:
    """What one break-out will do, settled before anything is written."""

    code: str
    cut_in: int
    cut_out: int
    sequence: Sequence
    previs_file: Path
    # The shot as ShotGrid held it when planning looked, or None when production
    # has never registered it and `deliver` will.
    sg_shot: Shot | None
    # Settled here rather than derived on demand: a `Shot` hydrates in place, so a
    # plan that re-read the range would stop describing the delivery that was
    # confirmed.
    recuts: bool
    # The shot whose outgoing RLO this delivery keeps before replacing it. Carries
    # the shot rather than a flag so a delivery cannot claim a replacement it has
    # nowhere to file.
    replaces: Shot | None
    # What previs is laid out against, and what the delivered RLO will compose.
    previs_sets: tuple[str, ...]
    rlo_sets: tuple[str, ...]

    @property
    def destination(self) -> Path:
        return _rlo_path(self.code)

    @property
    def frames(self) -> int:
        return self.cut_out - self.cut_in + 1

    @property
    def replaces_rlo(self) -> bool:
        return self.replaces is not None


def plan_delivery(shot: PrevisShot, proxy: Shot, conn: ShotGrid) -> DeliveryPlan:
    """What delivering `shot` would do, or a `BreakOutError` naming what to fix.

    Reads ShotGrid, the scene and the disk, and writes to none of them."""
    previs_file = _require_saved_scene()
    code = _require_code(shot)
    _require_primary(shot)
    sequence = _require_sequence(proxy)
    sg_shot = _find_shot(conn, code)
    cut_in, cut_out = _cut_range(shot)
    held_cut = None if sg_shot is None else (sg_shot.cut_in, sg_shot.cut_out)
    return DeliveryPlan(
        code=code,
        cut_in=cut_in,
        cut_out=cut_out,
        sequence=sequence,
        previs_file=previs_file,
        sg_shot=sg_shot,
        recuts=held_cut is not None and held_cut != (cut_in, cut_out),
        replaces=_outgoing_rlo(code, sg_shot),
        previs_sets=_set_codes(linked_environments(proxy)),
        rlo_sets=_set_codes(_rlo_environments(sequence, sg_shot)),
    )


def deliver(
    plan: DeliveryPlan, shot: PrevisShot, state: PrevisState, conn: ShotGrid
) -> Path:
    """Carry out a confirmed `plan`, and return the RLO scene it wrote."""
    _save_previs_scene()
    if plan.replaces is not None:
        _keep_outgoing_rlo(plan.replaces, plan.destination)
    sg_shot = _register_shot(plan, conn)
    try:
        _slice_scene_to_shot(plan, shot, state)
        _save_as_rlo(sg_shot, plan.destination)
    finally:
        _restore_previs_session(plan.previs_file)
    return plan.destination


def _find_shot(conn: ShotGrid, code: str) -> Shot | None:
    try:
        return conn.get_shot(code=code)
    except ShotGridNotFound:
        return None


def _require_sequence(proxy: Shot) -> Sequence:
    if proxy.sequence is None:
        raise BreakOutError(
            f"The previs sequence {proxy.code} is not linked to a sequence in "
            "ShotGrid, so a shot broken out of it would have nowhere to live. Ask "
            "production to fill in its Sequence field."
        )
    return proxy.sequence


def _require_saved_scene() -> Path:
    """The previs file break-out will save, cut up, and come home to."""
    scene = mc.file(query=True, sceneName=True)
    if not scene:
        raise BreakOutError(
            "This previs scene has never been saved, so there would be nothing to "
            "come back to after breaking out. Save it first."
        )
    return Path(str(scene))


def _outgoing_rlo(code: str, sg_shot: Shot | None) -> Shot | None:
    """The shot an RLO already sitting at the destination is kept under."""
    if not _rlo_path(code).exists():
        return None
    if sg_shot is None:
        raise BreakOutError(
            f"There is already an RLO scene for {code}, but ShotGrid has no shot "
            f"{code} to keep it under. Ask production to restore the shot before "
            "breaking this one out."
        )
    return sg_shot


def _rlo_environments(sequence: Sequence, sg_shot: Shot | None) -> list[Environment]:
    """The sets `setup_environment` will compose in the delivered RLO."""
    if sg_shot is not None:
        return linked_environments(sg_shot)
    return [sequence.set] if sequence.set else []


def _set_codes(environments: list[Environment]) -> tuple[str, ...]:
    return tuple(sorted(env.code or "?" for env in environments))


def _save_previs_scene() -> None:
    """A `mayaUsdProxyShape` marks every previs scene modified the moment it
    opens, so there is no dirty state worth testing."""
    try:
        mc.file(save=True, force=True)
    except RuntimeError as exc:
        log.exception("Could not save the previs scene before break-out.")
        raise BreakOutError(
            "Could not save this previs scene before breaking out, so nothing "
            "was cut. Check the file is not read-only, then try again."
        ) from exc


def _keep_outgoing_rlo(sg_shot: Shot, destination: Path) -> None:
    """Version the RLO scene about to be replaced, so a delivery is never a loss."""
    stream = maya_rlo_stream(sg_shot, owner=shot_owner_for(sg_shot))
    try:
        record = save_version(destination, stream, title=_REPLACED_TITLE)
    except Exception as exc:
        log.exception("Could not version %s before replacing it.", destination)
        raise BreakOutError(
            f"Could not keep a copy of the RLO scene already at {destination.name}, "
            "so it was left alone and nothing was cut."
        ) from exc
    log.info("Kept %s as version %s before breaking out.", destination, record.version)


def _register_shot(plan: DeliveryPlan, conn: ShotGrid) -> Shot:
    """Create or re-cut the ShotGrid Shot the RLO's stage is built from."""
    try:
        if plan.sg_shot is None:
            return conn.create_shot(
                code=plan.code,
                sequence=plan.sequence,
                cut_in=plan.cut_in,
                cut_out=plan.cut_out,
            )
        if plan.recuts:
            return conn.set_shot_cut_range(
                plan.sg_shot, cut_in=plan.cut_in, cut_out=plan.cut_out
            )
    except (ShotGridError, ValueError) as exc:
        log.exception("ShotGrid refused the break-out of %s.", plan.code)
        raise BreakOutError(
            f"ShotGrid would not take shot {plan.code}: {exc} Nothing was cut."
        ) from exc
    return plan.sg_shot


def _restore_previs_session(previs_file: Path) -> None:
    try:
        mc.file(str(previs_file), open=True, force=True)
    except RuntimeError as exc:
        mc.file(rename="")
        log.exception("Could not reopen the previs file after break-out.")
        raise BreakOutError(
            f"Could not reopen {previs_file.name} after breaking out. The scene on "
            "screen is no longer your previs file — do not save it. Open "
            f"{previs_file} again to carry on."
        ) from exc


def _save_as_rlo(sg_shot: Shot, destination: Path) -> None:
    """Re-stage the sliced scene as the shot's RLO file and write it."""
    destination.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
    mc.file(rename=str(destination))
    build_shot_stage(sg_shot, populate=lambda: setup_environment(sg_shot))
    mc.file(save=True, force=True)


def _slice_scene_to_shot(
    plan: DeliveryPlan, shot: PrevisShot, state: PrevisState
) -> None:
    """Cut the open scene down to `shot`, retimed to start at `FRAME_START`."""
    _drop_other_shot_cameras(shot, state)
    _rename_primary(shot.primary, plan.code)

    # Cutting keys leaves each plug holding whatever the scene last evaluated, so
    # the scene sits on the shot's opening frame before anything is trimmed.
    mc.currentTime(shot.source_in)
    _trim_keys(shot.source_in - HANDLE_FRAMES, shot.source_out + HANDLE_FRAMES)
    _retime(FRAME_START - shot.source_in)
    _strip_previs_scaffold()
    _frame_playback(plan)


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


def _require_primary(shot: PrevisShot) -> None:
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
    curves = _anim_curves()
    _warn_referenced_animation(curves)
    if not curves:
        return
    for span in ((_FAR_PAST, keep_start - _EPS), (keep_end + _EPS, _FAR_FUTURE)):
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


def _warn_referenced_animation(curves: list[str]) -> None:
    """Maya refuses to trim or retime a curve that lives inside a reference, and
    does it silently."""
    referenced = [c for c in curves if mc.referenceQuery(c, isNodeReferenced=True)]
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


def _frame_playback(plan: DeliveryPlan) -> None:
    """Frame what actually travelled, handles included."""
    start, end = plan.cut_in - HANDLE_FRAMES, plan.cut_out + HANDLE_FRAMES
    mc.playbackOptions(
        animationStartTime=start, animationEndTime=end, minTime=start, maxTime=end
    )
    mc.currentTime(plan.cut_in)


__all__ = [
    "BreakOutError",
    "DeliveryPlan",
    "deliver",
    "is_broken_out",
    "plan_delivery",
]
