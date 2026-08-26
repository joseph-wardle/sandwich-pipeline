"""What a Maya playblast asks of the viewport"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import maya.cmds as mc

# `hardwareRenderingGlobals` attrs the capture mirrors so the PNGs match the
# artist's interactive viewport for fog colour and falloff. Enabling fog is a
# separate flag; these only describe the fog that gets enabled.
_HW_FOG_ATTRS: tuple[str, ...] = (
    "hwFogAlpha",
    "hwFogFalloff",
    "hwFogDensity",
    "hwFogEnd",
    "hwFogColorR",
    "hwFogColorG",
    "hwFogColorB",
    "hwFogStart",
)

_MAX_HARDWARE_LIGHTS = 16


@dataclass(frozen=True)
class ViewportQuality:
    """Viewport features a capture turns on."""

    anti_alias: bool
    dof: bool
    hardware_fog: bool
    lighting: bool
    shadows: bool
    ssao: bool


def resolve_active_model_panel() -> str:
    """The model panel a capture should take its viewport settings from."""
    panel = str(mc.sequenceManager(query=True, modelPanel=True) or "")
    if panel and mc.modelPanel(panel, exists=True):
        return panel
    model_panels = mc.getPanel(type="modelPanel") or []
    if model_panels:
        return str(model_panels[0])
    return ""


def query_viewport_quality() -> ViewportQuality:
    """What the artist's viewport is currently doing, as playblast settings."""
    editor = resolve_active_model_panel()
    return ViewportQuality(
        anti_alias=_query_global_flag("multiSampleEnable"),
        dof=_query_dof(editor),
        hardware_fog=_query_fogging(editor),
        lighting=_query_lighting(editor),
        shadows=_query_shadows(editor),
        ssao=_query_global_flag("ssaoEnable"),
    )


def capture_kwargs(quality: ViewportQuality) -> dict[str, Any]:
    """`mayacapture` kwargs for `quality`."""
    viewport_options: dict[str, Any] = {
        "fogging": quality.hardware_fog,
        "shadows": quality.shadows,
    }
    if quality.lighting:
        viewport_options["displayLights"] = "all"

    panel = resolve_active_model_panel()
    if panel:
        try:
            viewport_options["twoSidedLighting"] = mc.modelEditor(
                panel, query=True, twoSidedLighting=True
            )
        except Exception:
            pass

    viewport2_options: dict[str, Any] = {
        attr: mc.getAttr(f"hardwareRenderingGlobals.{attr}") for attr in _HW_FOG_ATTRS
    }
    viewport2_options.update(
        {
            "enableTextureMaxRes": True,
            "hwFogEnable": quality.hardware_fog,
            "maxHardwareLights": _MAX_HARDWARE_LIGHTS,
            "multiSampleEnable": quality.anti_alias,
            "ssaoEnable": quality.ssao,
        }
    )

    return {
        "viewport_options": viewport_options,
        "viewport2_options": viewport2_options,
        "camera_options": {"depthOfField": quality.dof},
    }


def _query_fogging(panel: str) -> bool:
    if not panel:
        return False
    try:
        return bool(mc.modelEditor(panel, query=True, fogging=True))
    except Exception:
        return False


def _query_shadows(panel: str) -> bool:
    if not panel:
        return False
    try:
        return bool(mc.modelEditor(panel, query=True, shadows=True))
    except Exception:
        return False


def _query_global_flag(attr: str) -> bool:
    try:
        return bool(mc.getAttr(f"hardwareRenderingGlobals.{attr}"))
    except Exception:
        return False


def _query_lighting(panel: str) -> bool:
    if not panel:
        return False
    try:
        return mc.modelEditor(panel, query=True, displayLights=True) == "all"
    except Exception:
        return False


def _query_dof(panel: str) -> bool:
    if not panel:
        return False
    try:
        camera = str(mc.modelEditor(panel, query=True, camera=True))
        return bool(mc.camera(camera, query=True, depthOfField=True))
    except Exception:
        return False


__all__ = [
    "ViewportQuality",
    "capture_kwargs",
    "query_viewport_quality",
    "resolve_active_model_panel",
]
