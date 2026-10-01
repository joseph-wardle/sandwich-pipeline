from __future__ import annotations

import maya.api.OpenMaya as om
import maya.cmds as mc

_MASK_COLOR = (0.0, 0.0, 0.0)  # opaque black bars outside the film gate
_MASK_OPACITY = 1.0

_FACTORY_CLIP_CM = (0.1, 10000.0)
STARTUP_CLIP_CM = (1.0, 100000.0)

_NEAR_CLIP_PREF = "defaultCameraNearClipValue"
_FAR_CLIP_PREF = "defaultCameraFarClipValue"


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


def install_startup_camera_clip() -> None:
    """Give persp, top, front and side the show's clip range in every scene."""
    near, far = STARTUP_CLIP_CM
    mc.optionVar(floatValue=(_NEAR_CLIP_PREF, near))
    mc.optionVar(floatValue=(_FAR_CLIP_PREF, far))
    replace_factory_clip()
    om.MSceneMessage.addCallback(
        om.MSceneMessage.kAfterOpen, lambda _: replace_factory_clip()
    )


def replace_factory_clip() -> None:
    """Set the scene's startup cameras to the show's clip range, unless an artist
    already moved them off Maya's factory range."""
    for shape in mc.ls(cameras=True) or []:
        if not mc.camera(shape, query=True, startupCamera=True):
            continue
        # MFnCamera always speaks centimetres; getAttr follows the scene's unit.
        camera = om.MFnCamera(om.MSelectionList().add(shape).getDagPath(0))
        if (camera.nearClippingPlane, camera.farClippingPlane) == _FACTORY_CLIP_CM:
            camera.nearClippingPlane, camera.farClippingPlane = STARTUP_CLIP_CM
