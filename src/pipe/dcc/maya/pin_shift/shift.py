"""Pin shift: insert or remove time at a pivot frame without deforming surrounding animation.

Every affected time-driven curve is pinned at the pivot with a protective key, then:

* **insert** (`amount > 0`): the pivot pose is held flat across the new gap and every
  later key shifts later, so motion before the pivot and after the gap is preserved
  bit-for-bit.
* **remove** (`amount < 0`): the keys in `(pivot, pivot + gap]` are cut and the tail is
  pulled back, splicing the timeline at the pivot.

Only time-driven curves move. Set-driven keys (`animCurveU*`) are driven by another
attribute, not time, and are never touched. This module is pure scene math: it owns no
undo chunk and shows no UI — the dialog wraps it in `undo_chunk` and reports the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import maya.cmds as mc

# The only curve kinds a time shift may touch. Set-driven keys (`animCurveUL/UA/UU/UT`)
# are indexed by a driver attribute rather than time, so shifting their keys would corrupt
# the driven relationship.
TIME_DRIVEN_CURVE_TYPES: tuple[str, ...] = (
    "animCurveTL",
    "animCurveTA",
    "animCurveTU",
    "animCurveTT",
)

_EPS = 0.001  # `pivot + _EPS` selects keys strictly after the pivot, leaving the pin in place.
_FAR_FUTURE = 1_000_000.0


@dataclass(frozen=True)
class PinShiftResult:
    curves_shifted: int
    keys_deleted: int
    skipped: list[str]


def affected_curves() -> list[str]:
    """Time-driven curves driving the selection, or every one in the scene when nothing is selected."""
    selection = mc.ls(selection=True) or []
    if selection:
        # The stub mistypes `keyframe -name` as a scalar; it returns curve-node names.
        named = mc.keyframe(*selection, query=True, name=True) or []
        candidates = cast("list[str]", named)
    else:
        candidates = mc.ls(type=list(TIME_DRIVEN_CURVE_TYPES)) or []
    # Dedupe (one curve can be reached twice) and drop any set-driven key the selection
    # query may have returned alongside the time-driven ones.
    return [
        curve
        for curve in dict.fromkeys(candidates)
        if mc.nodeType(curve) in TIME_DRIVEN_CURVE_TYPES
    ]


def pin_shift(pivot: int, amount: int, curves: list[str]) -> PinShiftResult:
    """Insert (`amount > 0`) or remove (`amount < 0`) `amount` frames of time at `pivot` across `curves`."""
    editable, skipped = _partition_editable(curves)
    if amount > 0:
        shifted, deleted = _insert_hold(pivot, amount, editable), 0
    elif amount < 0:
        shifted, deleted = _remove_interior(pivot, -amount, editable)
    else:
        shifted, deleted = 0, 0
    return PinShiftResult(curves_shifted=shifted, keys_deleted=deleted, skipped=skipped)


def _partition_editable(curves: list[str]) -> tuple[list[str], list[str]]:
    """Split into (editable, skipped); referenced or locked curves cannot be retimed in place."""
    editable: list[str] = []
    skipped: list[str] = []
    for curve in curves:
        referenced = bool(mc.referenceQuery(curve, isNodeReferenced=True))
        # `lockNode -query` returns a one-element list; the stub flattens it to a bool.
        locked = cast("list[bool]", mc.lockNode(curve, query=True))[0]
        if referenced or locked:
            skipped.append(curve)
        else:
            editable.append(curve)
    return editable, skipped


def _insert_hold(pivot: int, frames: int, curves: list[str]) -> int:
    """Hold each curve's pivot pose flat for `frames`, then resume its motion `frames` later. Returns the count shifted."""
    resume = pivot + frames
    shifted = 0
    for curve in curves:
        if not _has_keys_after(curve, pivot):
            continue  # nothing past the pivot to delay; a hold here would only add stray keys
        mc.setKeyframe(curve, insert=True, time=pivot)
        # Capture the pivot pose before flattening, so the resume key inherits the pivot's
        # original tangents and the delayed motion continues exactly where it left off.
        mc.copyKey(curve, time=(pivot,))
        mc.keyframe(
            curve,
            edit=True,
            relative=True,
            timeChange=frames,
            time=(pivot + _EPS, _FAR_FUTURE),
        )
        mc.pasteKey(curve, time=(resume,), option="merge")
        # Flat out at the pivot and flat in at the resume key make the span between them a
        # constant hold at the pivot value.
        mc.keyTangent(curve, edit=True, time=(pivot,), outTangentType="flat")
        mc.keyTangent(curve, edit=True, time=(resume,), inTangentType="flat")
        shifted += 1
    return shifted


def _remove_interior(pivot: int, gap: int, curves: list[str]) -> tuple[int, int]:
    """Cut each curve's keys in `(pivot, pivot + gap]` and pull the tail back by `gap`. Returns (curves shifted, keys deleted)."""
    tail_start = pivot + gap
    shifted = 0
    deleted = 0
    for curve in curves:
        if not _has_keys_after(curve, pivot):
            continue
        mc.setKeyframe(curve, insert=True, time=pivot)  # protect the pre-pivot segment
        deleted += mc.cutKey(curve, time=(pivot + _EPS, tail_start), clear=True) or 0
        mc.keyframe(
            curve,
            edit=True,
            relative=True,
            timeChange=-gap,
            time=(tail_start + _EPS, _FAR_FUTURE),
        )
        shifted += 1
    return shifted, deleted


def _has_keys_after(curve: str, pivot: int) -> bool:
    count = (
        mc.keyframe(
            curve, query=True, time=(pivot + _EPS, _FAR_FUTURE), keyframeCount=True
        )
        or 0
    )
    return count > 0
