"""Shared helper for translating the dialog's viewport-options bools into the
keyword dict Maya's `capture()` expects.

Lives here (and not in `dcc.maya.playblast.shot.playblaster`) because both
`MComparePlayblaster` and `MSequencePlayblaster` need the same translation
without inheriting the rest of `MPlayblaster`'s single-shot machinery.
"""

from __future__ import annotations

from typing import Any

import maya.cmds as mc

# `hardwareRenderingGlobals` attrs that the playblast needs to mirror so the
# captured PNGs match the artist's interactive viewport for fog colour/density.
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


def apply_viewport_options(
    base_kwargs: dict[str, Any], options: dict[str, bool]
) -> dict[str, Any]:
    """Return a fresh kwargs dict combining `base_kwargs` with viewport flags.

    `options` keys (all optional, defaulting to `False`): `dof`, `hardware_fog`,
    `lighting`, `shadows`, `ssao`. Mirrors the kwargs assembly that
    `MPlayblaster.playblast` does for single-shot playblasts.
    """
    kwargs: dict[str, Any] = {
        "viewport_options": {},
        "viewport2_options": {},
        "camera_options": {},
    }
    kwargs.update(base_kwargs)

    if options.get("dof"):
        kwargs["camera_options"]["depthOfField"] = True
    if options.get("hardware_fog"):
        kwargs["viewport_options"]["fogging"] = True
        kwargs["viewport2_options"]["hwFogEnable"] = True
    if options.get("lighting"):
        kwargs["viewport_options"]["displayLights"] = "all"
    if options.get("shadows"):
        kwargs["viewport_options"]["shadows"] = True
    if options.get("ssao"):
        kwargs["viewport2_options"]["ssaoEnable"] = True

    panel = _resolve_active_model_panel()
    if panel:
        try:
            kwargs["viewport_options"]["twoSidedLighting"] = mc.modelEditor(
                panel, query=True, twoSidedLighting=True
            )
        except Exception:
            pass

    kwargs["viewport2_options"].update(
        {attr: mc.getAttr(f"hardwareRenderingGlobals.{attr}") for attr in _HW_FOG_ATTRS}
    )
    kwargs["viewport2_options"].update(
        {
            "enableTextureMaxRes": True,
            "maxHardwareLights": 16,
            "multiSampleEnable": True,
        }
    )
    return kwargs


def _resolve_active_model_panel() -> str:
    panel = str(mc.sequenceManager(query=True, modelPanel=True) or "")
    if panel and mc.modelPanel(panel, exists=True):
        return panel
    model_panels = mc.getPanel(type="modelPanel") or []
    if model_panels:
        return str(model_panels[0])
    return ""


__all__ = ["apply_viewport_options"]
