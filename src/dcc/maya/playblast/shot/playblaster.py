from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

import maya.cmds as mc
from mayacapture.capture import capture  # type: ignore[import-not-found]

from core.hud import (
    ARTIST,
    TITLE,
    HudContent,
    labeled_line,
    line_date,
    line_shot,
)
from core.playblast import Playblaster
from core.util.users import resolve_artist_display_name
from dcc.maya.playblast.shot.config import MPlayblastConfig, MShotPlayblastConfig
from dcc.maya.util.selection import maintain_selection

if TYPE_CHECKING:
    from typing import Any

    from core.shotgrid import Shot

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
            start_frame=(cut_in - 5),
            end_frame=(cut_out + 5),
            format="image",
            compression="png",
            off_screen=True,
            # HUD bakes during encode (apply_hud in the base), not during capture.
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

        right_lines: list[str] = [line_date()]
        camera_line = _camera_focal_lines(shot_config)
        right_lines.extend(camera_line)

        return HudContent(
            left_lines=tuple(left_lines),
            right_lines=tuple(right_lines),
            frame_start=start_frame,
        )

    def playblast(self) -> None:
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
                    super()._do_playblast(
                        shot_config.shot,
                        shot_config.paths,
                        shot_config.tails,
                    )
                finally:
                    self._current_shot_config = None


def _camera_focal_lines(shot_config: MShotPlayblastConfig | None) -> list[str]:
    if shot_config is None or not shot_config.camera or shot_config.use_sequencer:
        return []
    camera_path = str(shot_config.camera)
    lines = [labeled_line(_LABEL_CAMERA, _short_camera_name(camera_path))]
    focal = _camera_focal_length(camera_path)
    if focal is not None:
        lines.append(labeled_line(_LABEL_FOCAL, f"{focal:.0f}mm"))
    return lines


def _short_camera_name(camera_path: str) -> str:
    return camera_path.rsplit("|", 1)[-1] or camera_path


def _camera_focal_length(camera_path: str) -> float | None:
    try:
        # mc.camera query is typed as a broad union; focalLength always returns a scalar.
        return float(
            cast("float", mc.camera(camera_path, query=True, focalLength=True))
        )
    except Exception:
        return None


__all__ = ["MPlayblaster"]
