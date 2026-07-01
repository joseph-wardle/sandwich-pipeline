"""The previs monitor: one model panel pinned to the active shot's primary camera.

Binding is opt-in — nothing follows the playhead until an artist picks a monitor —
and is stored as a user preference, so the choice survives files and sessions but
stays per-workstation. The monitor wears a filled gate mask, giving letterboxed
framing while every other viewport stays free for animating.
"""

from __future__ import annotations

import logging
from typing import Callable, cast

import maya.cmds as mc

from pipe.dcc.maya.util.optionvar import BoolOptionVar, StringOptionVar

log = logging.getLogger(__name__)

_BOUND_PANEL = StringOptionVar("previs.monitorPanel", "")
_CLEAN_VIEW = BoolOptionVar("previs.monitorClean", False)
_PICK_CONTEXT = "previsMonitorPick"

_MASK_COLOR = (0.0, 0.0, 0.0)  # opaque black bars outside the resolution gate
_MASK_OPACITY = 1.0


def get_monitor() -> str | None:
    """The bound model panel, or None when unset or no longer in the UI."""
    panel = _BOUND_PANEL.value
    return panel if panel in (mc.getPanel(type="modelPanel") or []) else None


def set_monitor(panel: str) -> None:
    _BOUND_PANEL.value = panel
    # A freshly bound panel honors the current clean-view preference.
    _apply_clean(panel, _CLEAN_VIEW.value)
    log.info("Previs monitor bound to %s.", panel)


def clean_view() -> bool:
    """Whether the monitor strips chrome and rig controls down to a render-like frame."""
    return _CLEAN_VIEW.value


def set_clean_view(on: bool) -> None:
    """Persist the clean-view preference and apply it to the bound monitor, if any."""
    _CLEAN_VIEW.value = on
    panel = get_monitor()
    if panel is not None:
        _apply_clean(panel, on)


def pick_monitor(on_bound: Callable[[str], None] | None = None) -> None:
    """Enter a one-shot tool; the next viewport click binds that panel as the monitor."""
    previous_ctx = mc.currentCtx()
    if mc.draggerContext(_PICK_CONTEXT, exists=True):
        mc.deleteUI(_PICK_CONTEXT)
    mc.draggerContext(
        _PICK_CONTEXT,
        pressCommand=lambda *_: _bind_under_pointer(previous_ctx, on_bound),
        cursor="crossHair",
    )
    mc.setToolTo(_PICK_CONTEXT)
    mc.inViewMessage(
        assistMessage="Click a viewport to make it the previs monitor",
        position="midCenter",
        fade=True,
    )


def look_through(camera_shape: str) -> None:
    """Point the monitor at `camera_shape` with the gate mask on. No-op if unbound."""
    panel = get_monitor()
    if panel is None:
        return
    if (mc.lookThru(panel, query=True) or "") == camera_shape:
        return
    mc.lookThru(panel, camera_shape)
    _apply_gate(camera_shape)


def _bind_under_pointer(
    previous_ctx: str, on_bound: Callable[[str], None] | None
) -> None:
    panel = cast(str, mc.getPanel(underPointer=True))
    if panel in (mc.getPanel(type="modelPanel") or []):
        set_monitor(panel)
        if on_bound:
            on_bound(panel)
    mc.setToolTo(previous_ctx)


def _apply_clean(panel: str, on: bool) -> None:
    # Clean view hides rig controls, construction aids, and viewport chrome, leaving
    # only the rendered geometry and gate mask. Work view shows them all again, so the
    # toggle is its own inverse — no snapshot of the panel's prior state to keep.
    #
    # headsUpDisplay is pinned on, never hidden: Maya draws the camera's gate mask in
    # the same 2D ornament layer as the HUD, so headsUpDisplay=False strips the bars.
    # Hiding cameras only drops their icons — the look-through gate is unaffected.
    visible = not on
    mc.modelEditor(
        panel,
        edit=True,
        headsUpDisplay=True,
        cameras=visible,
        grid=visible,
        manipulators=visible,
        nurbsCurves=visible,
        locators=visible,
        joints=visible,
        handles=visible,
        deformers=visible,
        dimensions=visible,
        pivots=visible,
        controlVertices=visible,
        hulls=visible,
    )


def _apply_gate(camera_shape: str) -> None:
    # filmFit="overscan" fits the whole frame inside the viewport, so the mask bars
    # fill the slack on whichever axis — letterbox in tall ports, pillarbox in wide.
    # (Fill scales the frame to fill the window instead, leaving no bars.)
    mc.camera(
        camera_shape,
        edit=True,
        filmFit="overscan",
        displayResolution=True,
        displayGateMask=True,
        overscan=1.0,
    )
    mc.setAttr(f"{camera_shape}.displayGateMaskColor", *_MASK_COLOR, type="double3")  # type: ignore
    mc.setAttr(f"{camera_shape}.displayGateMaskOpacity", _MASK_OPACITY)  # type: ignore
