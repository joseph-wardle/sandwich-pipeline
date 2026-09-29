from __future__ import annotations

import maya.cmds as mc

SHOT_ASPECT = 16 / 9
_ASPECT_TOLERANCE = 0.01

_MASK_COLOR = (0.0, 0.0, 0.0)  # opaque black bars outside the film gate
_MASK_OPACITY = 1.0


def apply_gate_mask(camera_shape: str) -> None:
    mc.camera(
        camera_shape,
        edit=True,
        filmFit="overscan",
        displayFilmGate=True,
        displayResolution=False,
        displayGateMask=True,
        overscan=1.0,
    )
    mc.setAttr(f"{camera_shape}.displayGateMaskColor", *_MASK_COLOR, type="double3")  # type: ignore
    mc.setAttr(f"{camera_shape}.displayGateMaskOpacity", _MASK_OPACITY)  # type: ignore


def has_shot_aspect(camera_shape: str) -> bool:
    width = mc.getAttr(f"{camera_shape}.horizontalFilmAperture")
    height = mc.getAttr(f"{camera_shape}.verticalFilmAperture")
    return abs(width / height - SHOT_ASPECT) < _ASPECT_TOLERANCE
