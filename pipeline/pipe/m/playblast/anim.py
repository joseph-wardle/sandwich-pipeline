from __future__ import annotations

import logging
import re

from Qt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QWidget,
)
from shared.util import get_edit_path

from pipe.m.shotfile.anim import _find_usd_shotcam
from pipe.playblast_naming import playblast_date_folder
from pipe.util import Playblaster

from .struct import (
    HudDefinition,
    MPlayblastConfig,
    MShotPlayblastConfig,
    SaveLocation,
)
from .ui import PlayblastDialog

log = logging.getLogger(__name__)


class AnimPlayblastDialog(PlayblastDialog):
    _shot_camera_value: QLabel
    _shot_pass: QComboBox

    PASS_PATTERN = re.compile(r"^(?:Blocking|Polish) #\d+$")

    class SAVE_LOCS(PlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            lambda: get_edit_path() / "anim" / playblast_date_folder(),
            Playblaster.PRESET.EDIT_SQ,
        )

    def __init__(self, parent) -> None:
        super().__init__(parent, windowTitle="SKD Anim Playblast")

    def _build_extra_source_options(self) -> QWidget | None:
        pass_row = QWidget()
        pass_layout = QHBoxLayout(pass_row)
        pass_layout.setContentsMargins(0, 0, 0, 0)

        pass_layout.addWidget(QLabel("Pass"))

        self._shot_pass = QComboBox(self)
        self._shot_pass.addItems(["Blocking #1", "Polish #1"])
        self._shot_pass.setEditable(True)
        self._shot_pass.setToolTip(
            "Pass text shown in the HUD for shot exports. Format: Blocking #<n> or Polish #<n>."
        )
        self._shot_pass.currentTextChanged.connect(self._on_source_settings_changed)
        pass_layout.addWidget(self._shot_pass)
        pass_layout.addStretch()

        return pass_row

    def _build_shot_camera_widget(self) -> QWidget:
        self._shot_camera_value = QLabel("-")
        self._shot_camera_value.setToolTip("Resolved shot camera path.")
        return self._shot_camera_value

    def _validate_source_state(self, mode: str) -> str | None:
        if mode == "shot":
            if not self._get_shot_camera_path():
                return "Could not resolve a shot camera path for this shot."
            pass_text = str(self._shot_pass.currentText()).strip()
            if not self.PASS_PATTERN.fullmatch(pass_text):
                return "Pass must be formatted like 'Blocking #1' or 'Polish #1'."
        return None

    def _refresh_custom_ui_state(self) -> None:
        if self._shot is None:
            self._shot_camera_value.setText("-")
        else:
            self._shot_camera_value.setText(self._get_shot_camera_path() or "-")

    def _get_shot_camera_path(self) -> str | None:
        """Resolve the USD shot camera in Maya, supporting both legacy and current hierarchies."""
        camera_path = _find_usd_shotcam()
        if camera_path:
            return camera_path

        log.warning("No USD shot camera found; falling back to legacy path.")
        return "|__mayaUsd__|shotCamParent|shotCam"

    def _hud_shot_label(self) -> str:
        if self._shot is not None:
            return self._shot.code
        return "No shot code found"

    def _build_shot_playblast_config(self) -> MShotPlayblastConfig:
        if self._shot is None:
            raise ValueError("No pipeline shot context is available.")

        shot_output_name = self._resolve_output_name(self._shot.code)
        return MShotPlayblastConfig(
            camera=self._get_shot_camera_path(),
            shot=self._shot,
            paths=self._paths_for_filename(shot_output_name),
            tails=(5, 5),
            use_sequencer=False,
        )

    def _generate_config(self) -> MPlayblastConfig:
        mode = self._selected_source_mode()
        if mode == "shot":
            shot_config = self._build_shot_playblast_config()
        else:
            shot_config = self._build_custom_playblast_config()

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
                    "SKD_shot",
                    command=self._hud_shot_label,
                    section=7,
                    event="SceneSaved",
                ),
                HudDefinition(
                    "SKD_pass",
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
            shots=[shot_config],
            ssao=self.use_ssao,
        )
