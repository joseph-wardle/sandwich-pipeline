from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

import maya.cmds as mc
from env_sg import DB_Config
from Qt.QtCore import QRegExp
from Qt.QtGui import QRegExpValidator
from Qt.QtWidgets import QComboBox, QGridLayout, QLabel, QSpinBox, QWidget
from shared.util import get_edit_path

from pipe.db import DB
from pipe.m.shotfile.anim import _find_usd_shotcam
from pipe.util import Playblaster, checkbox_callback_helper

from .struct import (
    HudDefinition,
    MPlayblastConfig,
    MShotDialogConfig,
    MShotPlayblastConfig,
    SaveLocation,
    dummy_shot,
)
from .ui import PlayblastDialog

if TYPE_CHECKING:
    from pipe.struct.db import Shot

log = logging.getLogger(__name__)


class AnimPlayblastDialog(PlayblastDialog):
    _conn: DB
    _shot: Shot | None

    class SAVE_LOCS(PlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            get_edit_path() / "anim" / datetime.now().strftime("%m-%d-%y"),
            Playblaster.PRESET.EDIT_SQ,
        )

    SG_ID = "sg"
    CUSTOM_ID = "custom"

    def __init__(self, parent) -> None:
        self._conn = DB.Get(DB_Config)
        try:
            code = str(mc.fileInfo("code", query=True)[0])
            self._shot = self._conn.get_shot_by_code(code)
        except Exception:
            self._shot = None

        self._shot_dialog_configs = [
            MShotDialogConfig(
                id=self.SG_ID,
                name="Shot (from SG)",
                save_locs=[
                    (self.SAVE_LOCS.EDIT, False),
                    (self.SAVE_LOCS.CURRENT, True),
                    (self.SAVE_LOCS.CUSTOM, False),
                ],
            ),
            MShotDialogConfig(
                id=self.CUSTOM_ID,
                name="Custom",
                save_locs=[
                    (self.SAVE_LOCS.EDIT, False),
                    (self.SAVE_LOCS.CURRENT, True),
                    (self.SAVE_LOCS.CUSTOM, False),
                ],
            ),
        ]
        super().__init__(parent, self._shot_dialog_configs, "LnD Anim Playblast")

    def _setup_ui(self) -> None:
        super()._setup_ui()
        self._disable_shotgrid_target_when_missing()
        self.add_context_widget(self._build_pass_settings_widget())
        self.add_context_widget(self._build_custom_shot_settings_widget())

    def _disable_shotgrid_target_when_missing(self) -> None:
        if not self._shot:
            shotgrid_toggle = self._enabled_shot_cbs[self.SG_ID]
            shotgrid_toggle.setChecked(False)
            shotgrid_toggle.setEnabled(False)

    def _build_pass_settings_widget(self) -> QWidget:
        pass_settings_widget = QWidget(self)
        pass_settings_layout = QGridLayout(pass_settings_widget)
        self._shot_pass = QComboBox(self)
        self._shot_pass.addItems(["Blocking #", "Polish #"])
        self._shot_pass.setEditable(True)
        self._shot_pass.setValidator(
            QRegExpValidator(QRegExp(r"^(?:Blocking|Polish) #\d+$"))
        )
        pass_settings_layout.addWidget(QLabel("Pass"), 0, 0)
        pass_settings_layout.addWidget(self._shot_pass, 0, 1, 1, 2)
        return pass_settings_widget

    def _build_custom_shot_settings_widget(self) -> QWidget:
        custom_settings_widget = QWidget(self)
        custom_settings_layout = QGridLayout(custom_settings_widget)

        self._custom_in = QSpinBox(self, maximum=10000, minimum=0, value=1001)
        self._custom_out = QSpinBox(self, maximum=10000, minimum=0, value=1100)
        custom_settings_layout.addWidget(QLabel("Custom In"), 1, 1)
        custom_settings_layout.addWidget(self._custom_in, 1, 2)
        custom_settings_layout.addWidget(QLabel("Custom Out"), 1, 3)
        custom_settings_layout.addWidget(self._custom_out, 1, 4)

        self._custom_camera = QComboBox(self)
        camera_names = self._get_available_camera_names()
        self._custom_camera.addItems(camera_names)
        self._custom_camera.setCurrentIndex(0)
        validator_pattern = self._build_camera_validator_pattern(camera_names)
        self._custom_camera.setValidator(QRegExpValidator(QRegExp(validator_pattern)))
        custom_settings_layout.addWidget(QLabel("Custom Camera"), 2, 1)
        custom_settings_layout.addWidget(self._custom_camera, 2, 2, 1, 2)

        custom_toggle = self._enabled_shot_cbs[self.CUSTOM_ID]
        custom_toggle.toggled.connect(
            checkbox_callback_helper(custom_toggle, custom_settings_widget)
        )
        return custom_settings_widget

    @staticmethod
    def _get_available_camera_names() -> list[str]:
        return mc.ls(cameras=True, visible=True) or mc.ls(cameras=True) or ["persp"]

    @staticmethod
    def _build_camera_validator_pattern(camera_names: list[str]) -> str:
        escaped_names = [re.escape(camera_name) for camera_name in camera_names]
        return f"^(?:{'|'.join(escaped_names)})$"

    def _dialog_config_for_id(self, dialog_id: str) -> MShotDialogConfig:
        for dialog_config in self._shot_dialog_configs:
            if dialog_config.id == dialog_id:
                return dialog_config
        raise ValueError(f"Missing dialog config for id: {dialog_id}")

    def _generate_config(self) -> MPlayblastConfig:
        timestamp = datetime.now().strftime("%m-%d-%y_%H:%M:%S")
        shots: list[MShotPlayblastConfig] = []

        if self.is_shot_enabled(self.SG_ID):
            assert self._shot is not None
            shotgrid_dialog_config = self._dialog_config_for_id(self.SG_ID)
            shots.append(
                MShotPlayblastConfig(
                    camera=self._get_shot_camera_path(),
                    shot=self._shot,
                    paths=self.save_locations_to_paths(
                        self.SG_ID,
                        (
                            save_location
                            for save_location, _ in shotgrid_dialog_config.save_locs
                        ),
                        f"{self._shot.code}_{timestamp}",
                    ),
                    tails=(5, 5),
                )
            )

        if self.is_shot_enabled(self.CUSTOM_ID):
            custom_dialog_config = self._dialog_config_for_id(self.CUSTOM_ID)
            output_name = (
                f"customPB_{self._shot.code}_{timestamp}"
                if self._shot
                else f"customPB_{timestamp}"
            )
            custom_in_frame = self._custom_in.value()
            custom_out_frame = self._custom_out.value()
            cut_duration = max(0, custom_out_frame - custom_in_frame)
            shots.append(
                MShotPlayblastConfig(
                    camera=self._custom_camera.currentText(),
                    shot=dummy_shot(
                        "custom",
                        custom_in_frame,
                        custom_out_frame,
                        cut_duration=cut_duration,
                    ),
                    paths=self.save_locations_to_paths(
                        self.CUSTOM_ID,
                        (
                            save_location
                            for save_location, _ in custom_dialog_config.save_locs
                        ),
                        output_name,
                    ),
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
                    "LnDshot",
                    command=lambda: (
                        self._shot.code if self._shot else "No shot code found"
                    ),
                    section=7,
                    event="SceneSaved",
                ),
                HudDefinition(
                    "LnDpass",
                    command=lambda: self._shot_pass.currentText(),
                    label="Pass:",
                    section=5,
                    event="SceneSaved",
                ),
            ],
            dof=self.use_dof,
            hardware_fog=self.use_hardware_fog,
            lighting=self.use_lighting,
            shadows=self.use_shadows,
            shots=shots,
            ssao=self.use_ssao,
        )

    def _get_shot_camera_path(self) -> str | None:
        """Resolve the USD shot camera in Maya, supporting both legacy and current hierarchies."""
        cam = _find_usd_shotcam()
        if cam:
            return cam
        log.warning("No USD shot camera found; falling back to legacy path.")
        return "|__mayaUsd__|shotCamParent|shotCam"
