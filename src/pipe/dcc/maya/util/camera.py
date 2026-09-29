from __future__ import annotations

import maya.cmds as mc

_MASK_COLOR = (0.0, 0.0, 0.0)  # opaque black bars outside the film gate
_MASK_OPACITY = 1.0


def apply_gate_mask(camera_shape: str) -> None:
    # Overscan fit keeps the whole gate in view; fill fit crops it and hides the bars.
    mc.camera(
        camera_shape,
        edit=True,
        filmFit="overscan",
        displayFilmGate=True,
        displayResolution=False,
        displayGateMask=True,
    )
    mc.setAttr(f"{camera_shape}.displayGateMaskColor", *_MASK_COLOR, type="double3")  # type: ignore
    mc.setAttr(f"{camera_shape}.displayGateMaskOpacity", _MASK_OPACITY)  # type: ignore
