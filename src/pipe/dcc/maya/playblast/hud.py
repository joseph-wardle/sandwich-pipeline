"""HUD lines a Maya playblast reads off the scene."""

from __future__ import annotations

import maya.cmds as mc

from pipe.core.hud import TimedText, labeled_line, timed_text

_LABEL_CAMERA = "Camera"
_LABEL_FOCAL = "Focal"


def camera_focal_lines(
    camera_path: str, start_frame: int, end_frame: int, *, name: str = ""
) -> list[str | TimedText]:
    """The `Camera:` and `Focal:` HUD lines for `camera_path`, or [] without one."""
    if not camera_path:
        return []
    lines: list[str | TimedText] = [
        labeled_line(_LABEL_CAMERA, name or short_camera_name(camera_path))
    ]
    focal = _focal_line(camera_path, start_frame, end_frame)
    if focal is not None:
        lines.append(focal)
    return lines


def short_camera_name(camera_path: str) -> str:
    return camera_path.rsplit("|", 1)[-1] or camera_path


def _focal_line(
    camera_path: str, start_frame: int, end_frame: int
) -> str | TimedText | None:
    """The `Focal: NNmm` HUD line, sampled per frame so a keyed lens reads
    correctly. `None` when the camera has no queryable focal length."""
    shape = _camera_shape(camera_path)
    if shape is None or start_frame > end_frame:
        return None
    try:
        millimeters = [
            round(float(mc.getAttr(f"{shape}.focalLength", time=frame)))
            for frame in range(start_frame, end_frame + 1)
        ]
    except Exception:
        return None
    return timed_text([labeled_line(_LABEL_FOCAL, f"{mm}mm") for mm in millimeters])


def _camera_shape(camera_path: str) -> str | None:
    """Resolve `camera_path` (transform or shape) to its camera shape node,
    the holder of the `focalLength` attribute."""
    if mc.objExists(camera_path) and mc.nodeType(camera_path) == "camera":
        return camera_path
    shapes = mc.listRelatives(camera_path, shapes=True, type="camera", fullPath=True)
    return shapes[0] if shapes else None


__all__ = ["camera_focal_lines", "short_camera_name"]
