"""The previs viewport-capture primitive."""

from __future__ import annotations

from typing import Any

import maya.cmds as mc
from mayacapture.capture import capture  # type: ignore[import-not-found]

from pipe.dcc.maya.previs.cameras import resolve_camera_node

CAPTURE_WIDTH = 1280
CAPTURE_HEIGHT = 720

# `hardwareRenderingGlobals` attrs that the capture mirrors so the PNGs match the
# artist's interactive viewport for fog colour/density.
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


def capture_cut(
    filename: str,
    camera: str,
    start_frame: int,
    end_frame: int,
) -> None:
    """Capture one camera's `[start_frame, end_frame]` to PNGs under `filename`."""
    capture(
        width=CAPTURE_WIDTH,
        height=CAPTURE_HEIGHT,
        filename=filename,
        start_frame=start_frame,
        end_frame=end_frame,
        camera=resolve_camera_node(camera),
        format="image",
        compression="png",
        off_screen=True,
        show_ornaments=False,
        overwrite=True,
        maintain_aspect_ratio=False,
        viewer=0,
        **_viewport_kwargs(),
    )


def _viewport_kwargs() -> dict[str, Any]:
    """Viewport settings for a previs capture."""

    viewport_options: dict[str, Any] = {}
    viewport2_options: dict[str, Any] = {
        attr: mc.getAttr(f"hardwareRenderingGlobals.{attr}") for attr in _HW_FOG_ATTRS
    }
    viewport2_options.update(
        {
            "enableTextureMaxRes": True,
            "maxHardwareLights": 16,
            "multiSampleEnable": True,
        }
    )

    panel = _resolve_active_model_panel()
    if panel:
        try:
            viewport_options["twoSidedLighting"] = mc.modelEditor(
                panel, query=True, twoSidedLighting=True
            )
        except Exception:
            pass

    return {
        "viewport_options": viewport_options,
        "viewport2_options": viewport2_options,
        "camera_options": {},
    }


def _resolve_active_model_panel() -> str:
    panel = str(mc.sequenceManager(query=True, modelPanel=True) or "")
    if panel and mc.modelPanel(panel, exists=True):
        return panel
    model_panels = mc.getPanel(type="modelPanel") or []
    if model_panels:
        return str(model_panels[0])
    return ""


__all__ = ["CAPTURE_WIDTH", "CAPTURE_HEIGHT", "capture_cut"]
