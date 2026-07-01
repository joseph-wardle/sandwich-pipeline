"""Camera operations and rig queries for the previs sequencer."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import cast

import maya.cmds as mc
from pipe.core.util.paths import get_previs_path

from .state import PrevisShot, PrevisState

log = logging.getLogger(__name__)

RIG_PATH = get_previs_path() / "rigs/skdShotCam_v01.mb"

# Controls on the standard previs camera rig.
RIG_CONTROLS: tuple[str, ...] = (
    "world_CTRL",
    "main_CTRL",
    "dolly_CTRL",
    "tilt_pan_CTRL",
    "ClippingPlane_CTRL",
    "focusDistance_CTRL",
)

_AUTO_NAME_PREFIX = "cam_"
_AUTO_NAME_RE = re.compile(rf"^{_AUTO_NAME_PREFIX}(\d{{3}})$")


def next_camera_namespace() -> str:
    """Return `cam_NNN` one past the highest existing auto-named namespace."""
    highest = 0
    for ns in mc.namespaceInfo(":", listOnlyNamespaces=True, recurse=False) or []:
        match = _AUTO_NAME_RE.match(ns.lstrip(":"))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{_AUTO_NAME_PREFIX}{highest + 1:03d}"


def add_new_rig_reference() -> str:
    if not RIG_PATH.exists():
        raise FileNotFoundError(f"Previs rig not found: {RIG_PATH}")
    ns = next_camera_namespace()
    mc.file(str(RIG_PATH), reference=True, namespace=ns)
    return ns


def duplicate_primary(shot: PrevisShot) -> str | None:
    """Reference rig + copy every keyed control from `shot.primary`. Returns new namespace."""
    if not shot.primary:
        log.warning("Cannot duplicate: shot %s has no primary camera.", shot.id)
        return None
    new_ns = add_new_rig_reference()
    for control in RIG_CONTROLS:
        src = f"{shot.primary}:{control}"
        dst = f"{new_ns}:{control}"
        if not (mc.objExists(src) and mc.objExists(dst)):
            continue
        if mc.copyKey(src):
            mc.pasteKey(dst, option="replace")
    return new_ns


def camera_shape_for_namespace(ns: str) -> str | None:
    shapes = mc.ls(f"{ns}:*", type="camera", long=True) or []
    return shapes[0] if shapes else None


def resolve_camera_node(namespace: str) -> str:
    """Return the camera shape node path under `namespace`, raising if absent.

    Used at playblast time: `mayacapture.capture(camera=...)` calls
    `mc.objExists` on the value, which fails for namespace strings — the
    namespace is a container, not a node. Shape paths (`<ns>:<camera_shape>`)
    are what Maya's lookup actually accepts.
    """
    shape = camera_shape_for_namespace(namespace)
    if not shape:
        raise RuntimeError(f"No camera shape found under namespace ':{namespace}'.")
    return shape


def focal_length(namespace: str) -> float | None:
    """Return the focal length (mm) of the camera shape under `namespace`,
    or `None` if the namespace has no camera shape or the query fails."""
    try:
        shape = resolve_camera_node(namespace)
        return float(cast(float, mc.camera(shape, query=True, focalLength=True)))
    except Exception:
        return None


def camera_animation_range(namespace: str) -> tuple[float, float] | None:
    """Earliest and latest keyframe time across every rig control, or None when unkeyed.

    Used at publish time to bake the camera's actual animation into USD —
    the sequencer panel no longer reads this for layout (durations are stored
    explicitly on `PrevisShot.durations`).
    """
    all_times: list[float] = []
    for control in RIG_CONTROLS:
        plug = f"{namespace}:{control}"
        if not mc.objExists(plug):
            continue
        raw = mc.keyframe(plug, query=True, timeChange=True) or []
        if raw:
            all_times.extend(cast(list[float], raw))
    if not all_times:
        return None
    return (float(min(all_times)), float(max(all_times)))


def compute_animation_hash(camera_namespace: str) -> str:
    """SHA1 over every (control, attr, time, value) tuple under `camera_namespace`.

    Stored on each shot at publish; the panel re-hashes the live primary and
    compares to flag "current" vs "modified".
    """
    parts: list[str] = []
    for control in RIG_CONTROLS:
        plug = f"{camera_namespace}:{control}"
        if not mc.objExists(plug):
            continue
        for attr in mc.listAttr(plug, keyable=True) or []:
            target = f"{plug}.{attr}"
            count = mc.keyframe(target, query=True, keyframeCount=True) or 0
            if count <= 0:
                continue
            # `mc.keyframe` stubs union list/scalar; count>0 guarantees a list.
            times = cast(list[float], mc.keyframe(target, query=True, timeChange=True))
            values = cast(
                list[float], mc.keyframe(target, query=True, valueChange=True)
            )
            parts.append(f"{control}.{attr}|{list(zip(times, values))}")
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()


def find_scene_cameras_outside_state(state: PrevisState) -> list[str]:
    """Camera-bearing namespaces in the scene that aren't already tracked by `state`."""
    in_state: set[str] = {ns for shot in state.shots for ns in shot.all_cameras}
    candidates: set[str] = set()
    for cam_shape in mc.ls(type="camera", long=True) or []:
        leaf = cam_shape.rsplit("|", 1)[-1]
        if ":" not in leaf:
            continue
        ns = leaf.split(":")[0]
        if ns and ns not in in_state:
            candidates.add(ns)
    return sorted(candidates)


def is_live(namespace: str) -> bool:
    """True if `namespace` currently exists in the scene (i.e. not orphaned)."""
    return bool(namespace) and bool(mc.namespace(exists=f":{namespace}"))


def find_orphan_cameras(state: PrevisState) -> list[tuple[str, str]]:
    """`(shot_id, namespace)` for every camera in `state` whose namespace is gone from the scene."""
    orphans: list[tuple[str, str]] = []
    for shot in state.shots:
        for ns in shot.all_cameras:
            if not is_live(ns):
                orphans.append((shot.id, ns))
    return orphans


def rename_camera(old_ns: str, new_ns: str) -> bool:
    if not mc.namespace(exists=f":{old_ns}"):
        return False
    if mc.namespace(exists=f":{new_ns}"):
        return False
    mc.namespace(rename=(old_ns, new_ns))
    return True


def remove_camera_from_shot(shot: PrevisShot, namespace: str) -> None:
    """Drop `namespace` from the shot's schema. The scene node itself is left untouched."""
    if shot.primary == namespace:
        shot.primary = ""
    if namespace in shot.alternates:
        shot.alternates.remove(namespace)
    shot.durations.pop(namespace, None)
