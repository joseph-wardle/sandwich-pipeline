from __future__ import annotations

import copy
from typing import TYPE_CHECKING

import maya.cmds as mc
from mayacapture.capture import capture  # type: ignore[import-not-found]

from pipe.core.hud import (
    ARTIST,
    TITLE,
    HudContent,
    TimedText,
    labeled_line,
    line_date,
    line_shot,
    timed_text,
)
from pipe.core.playblast import Playblaster, PreviewClip
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.playblast.shot.config import MPlayblastConfig, MShotPlayblastConfig
from pipe.dcc.maya.util.selection import maintain_selection
from pipe.dcc.maya.util.time import scene_frame_rate

if TYPE_CHECKING:
    from typing import Any

    from pipe.core.shotgrid import Shot

# Anim/previs-specific labels.
_LABEL_PASS = "Pass"
_LABEL_CAMERA = "Camera"
_LABEL_FOCAL = "Focal"


class MPlayblaster(Playblaster):
    _config: MPlayblastConfig
    _current_shot_config: MShotPlayblastConfig | None
    _extra_kwargs: dict[str, Any]

    def __init__(self) -> None:
        self._extra_kwargs = {}
        self._current_shot_config = None

    def configure(self, config: MPlayblastConfig) -> MPlayblaster:
        self._config = config
        return self

    @staticmethod
    def _resolve_active_editor() -> str:
        panel = str(mc.sequenceManager(query=True, modelPanel=True) or "")
        if panel and mc.modelPanel(panel, exists=True):
            return panel

        model_panels = mc.getPanel(type="modelPanel") or []
        if model_panels:
            return str(model_panels[0])
        return ""

    def _write_images(self, shot: Shot, path: str) -> None:
        cut_in, cut_out = shot.frame_range
        head, tail = (
            self._current_shot_config.tails if self._current_shot_config else (0, 0)
        )
        active_editor = self._resolve_active_editor()
        if active_editor:
            self._extra_kwargs["viewport_options"].update(
                {
                    "twoSidedLighting": mc.modelEditor(
                        active_editor, query=True, twoSidedLighting=True
                    ),
                }
            )

        self._extra_kwargs["viewport2_options"].update(
            {
                **{
                    k: mc.getAttr(f"hardwareRenderingGlobals.{k}")
                    for k in (
                        "hwFogAlpha",
                        "hwFogFalloff",
                        "hwFogDensity",
                        "hwFogEnd",
                        "hwFogColorR",
                        "hwFogColorG",
                        "hwFogColorB",
                        "hwFogStart",
                    )
                },
                "enableTextureMaxRes": True,
                "maxHardwareLights": 16,
                "multiSampleEnable": True,
            }
        )

        width, height = self.resolution
        capture(
            width=width,
            height=height,
            filename=path,
            start_frame=(cut_in - head),
            end_frame=(cut_out + tail),
            format="image",
            compression="png",
            off_screen=True,
            # HUD burns onto the frames afterward (in the base), not during capture.
            show_ornaments=False,
            overwrite=True,
            maintain_aspect_ratio=False,
            viewer=0,
            **self._extra_kwargs,
        )

    def _hud_content(self, shot: Shot, start_frame: int) -> HudContent:
        shot_config = self._current_shot_config

        left_lines: list[str] = [labeled_line(ARTIST, resolve_artist_display_name())]
        if shot_config is not None and shot_config.version_title:
            left_lines.append(labeled_line(TITLE, shot_config.version_title))
        if shot_config is not None and shot_config.pass_label:
            left_lines.append(labeled_line(_LABEL_PASS, shot_config.pass_label))
        left_lines.append(
            line_shot(
                shot.code or "",
                version=shot_config.version_label if shot_config else None,
                unsaved=bool(mc.file(query=True, modified=True)),
            )
        )

        end_frame = start_frame
        if shot_config is not None:
            end_frame = shot.frame_range[1] + shot_config.tails[1]

        right_lines: list[str | TimedText] = [line_date()]
        right_lines.extend(_camera_focal_lines(shot_config, start_frame, end_frame))

        return HudContent(
            left_lines=tuple(left_lines),
            right_lines=tuple(right_lines),
            frame_start=start_frame,
        )

    def playblast(self) -> list[PreviewClip]:
        self.fps = scene_frame_rate()
        with maintain_selection():
            mc.select(clear=True)

            global_kwargs: dict[str, Any] = {
                "viewport_options": {},
                "viewport2_options": {},
                "camera_options": {},
            }

            if self._config.dof:
                global_kwargs["camera_options"].update({"depthOfField": True})

            if self._config.hardware_fog:
                global_kwargs["viewport_options"].update({"fogging": True})
                global_kwargs["viewport2_options"].update({"hwFogEnable": True})

            if self._config.lighting:
                global_kwargs["viewport_options"].update({"displayLights": "all"})

            if self._config.shadows:
                global_kwargs["viewport_options"].update({"shadows": True})

            if self._config.ssao:
                global_kwargs["viewport2_options"].update({"ssaoEnable": True})

            clips: list[PreviewClip] = []
            for shot_config in self._config.shots:
                self._extra_kwargs = copy.deepcopy(global_kwargs)
                if shot_config.use_sequencer:
                    self._extra_kwargs["use_camera_sequencer"] = True
                else:
                    self._extra_kwargs["camera"] = shot_config.camera

                # Stashed so `_hud_content` can read per-shot inputs when the
                # base calls it back up the stack.
                self._current_shot_config = shot_config
                try:
                    clip = super()._do_playblast(
                        shot_config.shot,
                        tails=shot_config.tails,
                    )
                finally:
                    self._current_shot_config = None
                clips.append(clip)
            return clips


def _camera_focal_lines(
    shot_config: MShotPlayblastConfig | None, start_frame: int, end_frame: int
) -> list[str | TimedText]:
    if shot_config is None or not shot_config.camera or shot_config.use_sequencer:
        return []
    camera_path = str(shot_config.camera)
    lines: list[str | TimedText] = [
        labeled_line(_LABEL_CAMERA, _short_camera_name(camera_path))
    ]
    focal = _focal_line(camera_path, start_frame, end_frame)
    if focal is not None:
        lines.append(focal)
    return lines


def _short_camera_name(camera_path: str) -> str:
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


__all__ = ["MPlayblaster"]
