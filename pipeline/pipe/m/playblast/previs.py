from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import maya.cmds as mc
from shared.util import get_edit_path

from pipe.util import Playblaster

from .struct import (
    HudDefinition,
    MPlayblastConfig,
    MShotDialogConfig,
    MShotPlayblastConfig,
    SaveLocation,
    dummy_shot,
)
from .ui import PlayblastDialog

log = logging.getLogger(__name__)


class PrevisPlayblastDialog(PlayblastDialog):
    _camera_shot_lookup: dict[str, str]
    _timeline_dialog_configs: list[MShotDialogConfig]
    _sequence_dialog_configs: list[MShotDialogConfig]
    _shot_dialog_configs: list[MShotDialogConfig]

    class SAVE_LOCS(PlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            get_edit_path() / "previs" / datetime.now().strftime("%m-%d-%y"),
            Playblaster.PRESET.EDIT_SQ,
        )

    def __init__(self, parent) -> None:
        shot_node_list: list[str] = mc.sequenceManager(listShots=True) or []  # type: ignore[assignment]
        sequencer_node = str(
            mc.sequenceManager(query=True, writableSequencer=True) or ""
        )

        # generate lookup table for matching cameras to shots
        self._camera_shot_lookup = {
            str(mc.shot(node, query=True, currentCamera=True)): str(
                mc.shot(node, query=True, shotName=True)
            )
            for node in shot_node_list
        }

        self._shot_dialog_configs = [
            MShotDialogConfig(
                id=shot_node,
                name=str(mc.shot(shot_node, query=True, shotName=True)),
                save_locs=[
                    (self.SAVE_LOCS.EDIT, True),
                    (self.SAVE_LOCS.CURRENT, False),
                    (self.SAVE_LOCS.CUSTOM, False),
                ],
            )
            for shot_node in shot_node_list
        ]
        self._sequence_dialog_configs = []
        if shot_node_list and sequencer_node and mc.objExists(sequencer_node):
            self._sequence_dialog_configs = [
                MShotDialogConfig(
                    id=sequencer_node,
                    name="Camera Sequencer",
                    save_locs=[
                        (self.SAVE_LOCS.EDIT, True),
                        (self.SAVE_LOCS.CURRENT, True),
                        (self.SAVE_LOCS.CUSTOM, False),
                    ],
                )
            ]

        self._timeline_dialog_configs = []
        if not shot_node_list:
            log.warning(
                "No camera sequencer shots detected. Falling back to active timeline export."
            )
            self._timeline_dialog_configs = [
                MShotDialogConfig(
                    id="timeline_fallback",
                    name="Active Camera Timeline",
                    save_locs=[
                        (self.SAVE_LOCS.EDIT, True),
                        (self.SAVE_LOCS.CURRENT, True),
                        (self.SAVE_LOCS.CUSTOM, False),
                    ],
                )
            ]

        super().__init__(
            parent,
            self._shot_dialog_configs
            + self._sequence_dialog_configs
            + self._timeline_dialog_configs,
            "Bobo Previs Playblast",
        )

    def _do_camera_shot_lookup(self) -> str:
        """Look up the current shot based off of the camera"""
        if not self._camera_shot_lookup:
            return "No shot data"
        panel: str = mc.getPanel(withLabel="CapturePanel")  # type: ignore[assignment]
        try:
            if panel:
                camera = (
                    str(mc.modelEditor(panel, query=True, camera=True)).split("|").pop()  # type: ignore[arg-type]
                )
                return self._camera_shot_lookup[camera]
        except KeyError:
            pass
        return "No shot data"

    @staticmethod
    def _scene_stem() -> str:
        scene_name = Path(mc.file(query=True, sceneName=True)).stem  # type: ignore[arg-type]
        return scene_name or "previs_playblast"

    @staticmethod
    def _timeline_range() -> tuple[int, int]:
        cut_in = int(mc.playbackOptions(minTime=True, query=True))
        cut_out = int(mc.playbackOptions(maxTime=True, query=True))
        if cut_out < cut_in:
            cut_out = cut_in
        return cut_in, cut_out

    def _sequencer_range(self, sequencer_node: str) -> tuple[int, int]:
        if not sequencer_node or not mc.objExists(sequencer_node):
            return self._timeline_range()
        try:
            cut_in = int(mc.getAttr(f"{sequencer_node}.minFrame"))
            cut_out = int(mc.getAttr(f"{sequencer_node}.maxFrame"))
        except Exception:
            return self._timeline_range()
        if cut_out < cut_in:
            cut_out = cut_in
        return cut_in, cut_out

    @staticmethod
    def _active_camera() -> str:
        focused_panel = str(mc.getPanel(withFocus=True) or "")
        if focused_panel and mc.getPanel(typeOf=focused_panel) == "modelPanel":
            try:
                return str(mc.modelEditor(focused_panel, query=True, camera=True))
            except Exception:
                pass

        sequencer_panel = str(mc.sequenceManager(query=True, modelPanel=True) or "")
        if sequencer_panel and mc.modelPanel(sequencer_panel, exists=True):
            try:
                return str(mc.modelEditor(sequencer_panel, query=True, camera=True))
            except Exception:
                pass

        visible_cameras = mc.ls(cameras=True, visible=True) or []
        if visible_cameras:
            return str(visible_cameras[0])
        return "persp"

    def _generate_config(self) -> MPlayblastConfig:
        seq_node = str(mc.sequenceManager(query=True, writableSequencer=True) or "")
        date = datetime.now().strftime("%m-%d-%y")
        shot_name = self._scene_stem()

        shots = [
            MShotPlayblastConfig(
                camera=str(mc.shot(config.id, query=True, currentCamera=True)),
                shot=dummy_shot(
                    clip_name := str(mc.shot(config.id, query=True, shotName=True)),
                    int(mc.shot(config.id, query=True, startTime=True)),
                    int(mc.shot(config.id, query=True, endTime=True)),
                    int(mc.shot(config.id, query=True, clipDuration=True)),
                ),
                paths=self.save_locations_to_paths(
                    config.id,
                    (sl[0] for sl in config.save_locs),
                    f"{clip_name}_{date}",
                ),
            )
            for config in self._shot_dialog_configs
            if self.is_shot_enabled(config.id)
        ]

        for config in self._sequence_dialog_configs:
            if not self.is_shot_enabled(config.id):
                continue
            seq_in, seq_out = self._sequencer_range(seq_node)
            shots.append(
                MShotPlayblastConfig(
                    camera=None,
                    shot=dummy_shot(
                        code=shot_name,
                        cut_in=seq_in,
                        cut_out=seq_out,
                        cut_duration=seq_out - seq_in,
                    ),
                    paths=self.save_locations_to_paths(
                        config.id,
                        (sl[0] for sl in config.save_locs),
                        f"{shot_name}_{date}",
                    ),
                    use_sequencer=True,
                )
            )

        for config in self._timeline_dialog_configs:
            if not self.is_shot_enabled(config.id):
                continue
            timeline_in, timeline_out = self._timeline_range()
            shots.append(
                MShotPlayblastConfig(
                    camera=self._active_camera(),
                    shot=dummy_shot(
                        code=shot_name,
                        cut_in=timeline_in,
                        cut_out=timeline_out,
                        cut_duration=timeline_out - timeline_in,
                    ),
                    paths=self.save_locations_to_paths(
                        config.id,
                        (sl[0] for sl in config.save_locs),
                        f"{shot_name}_{date}",
                    ),
                    use_sequencer=False,
                )
            )

        return MPlayblastConfig(
            builtin_huds=[
                PlayblastDialog.MAYA_HUDS.CAM_NAME,
                PlayblastDialog.MAYA_HUDS.CUR_FRAME,
                PlayblastDialog.MAYA_HUDS.FOCAL_LENGTH,
            ],
            custom_huds=[
                PlayblastDialog.CUSTOM_HUDS.FILENAME,
                PlayblastDialog.CUSTOM_HUDS.ARTIST,
                HudDefinition(
                    "Boboshot",
                    command=self._do_camera_shot_lookup,
                    section=7,
                    idle_refresh=True,
                ),
            ],
            dof=self.use_dof,
            hardware_fog=self.use_hardware_fog,
            lighting=self.use_lighting,
            shadows=self.use_shadows,
            shots=shots,
            ssao=self.use_ssao,
        )
