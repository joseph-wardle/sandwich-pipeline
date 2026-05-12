from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING

import maya.cmds as mc
from mayacapture.capture import capture  # type: ignore[import-not-found]

from dcc.maya.playblast.hud import applied_hud
from dcc.maya.playblast.shot.config import MPlayblastConfig
from dcc.maya.util.selection import maintain_selection
from core.playblast import Playblaster

if TYPE_CHECKING:
    from typing import Any

    from core.shotgrid import Shot

log = logging.getLogger(__name__)


class MPlayblaster(Playblaster):
    _config: MPlayblastConfig
    _extra_kwargs: dict[str, Any]

    def __init__(self) -> None:
        self._extra_kwargs = {}

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
        """Maya implementation of playblasting image frames"""
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

        capture(
            width=1280,
            height=720,
            filename=path,
            start_frame=(cut_in - 5),
            end_frame=(cut_out + 5),
            format="image",
            compression="png",
            off_screen=True,
            show_ornaments=True,
            overwrite=True,
            maintain_aspect_ratio=False,
            viewer=0,
            **self._extra_kwargs,
        )

    def playblast(self) -> None:
        with (
            applied_hud(self._config.builtin_huds, self._config.custom_huds),
            maintain_selection(),
        ):
            mc.select(clear=True)

            # assemble kwargs from config options
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

            # iterate over shots and playblast
            for shot_config in self._config.shots:
                # assemble shot-specific kwargs
                self._extra_kwargs = copy.deepcopy(global_kwargs)
                if shot_config.use_sequencer:
                    self._extra_kwargs["use_camera_sequencer"] = True
                else:
                    self._extra_kwargs["camera"] = shot_config.camera

                super()._do_playblast(
                    shot_config.shot,
                    shot_config.paths,
                    shot_config.tails,
                )


__all__ = ["MPlayblaster"]
