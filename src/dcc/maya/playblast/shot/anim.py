from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path

import maya.cmds as mc
from Qt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from core.playblast import FFmpegPreset
from core.playblast.naming import build_edit_output_directory
from core.shot import maya_anim_stream, shot_owner_for
from core.versioning import current_version_label
from dcc.maya.playblast.shot.config import (
    MPlayblastConfig,
    MShotPlayblastConfig,
    SaveLocation,
)
from dcc.maya.playblast.shot.dialog import MPlayblastDialog
from dcc.maya.shotfile.anim import _find_usd_shotcam

log = logging.getLogger(__name__)


class AnimPlayblastDialog(MPlayblastDialog):
    _shot_camera_value: QLabel
    _shot_pass: QComboBox

    PASS_PATTERN = re.compile(r"^(?:Blocking|Polish) #\d+$")

    class SAVE_LOCS(MPlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            lambda: build_edit_output_directory("anim"),
            FFmpegPreset.EDIT_SQ,
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

    def _build_shot_playblast_config(self) -> MShotPlayblastConfig:
        if self._shot is None:
            raise ValueError("No pipeline shot context is available.")

        shot_output_name = self._resolve_output_name(self._shot.code or "")
        version_label, version_title = _resolve_anim_version(self._shot)
        return MShotPlayblastConfig(
            camera=self._get_shot_camera_path(),
            shot=self._shot,
            paths=self._paths_for_filename(shot_output_name),
            tails=(5, 5),
            use_sequencer=False,
            version_label=version_label,
            version_title=version_title,
        )

    def _generate_config(self) -> MPlayblastConfig:
        mode = self._selected_source_mode()
        if mode == "shot":
            shot_config = self._build_shot_playblast_config()
        else:
            shot_config = self._build_custom_playblast_config()

        pass_label = str(self._shot_pass.currentText()).strip() or None
        shot_config = replace(shot_config, pass_label=pass_label)

        return MPlayblastConfig(
            dof=self.use_dof,
            hardware_fog=self.use_hardware_fog,
            lighting=self.use_lighting,
            shadows=self.use_shadows,
            shots=[shot_config],
            ssao=self.use_ssao,
        )


def _resolve_anim_version(shot) -> tuple[str | None, str | None]:
    scene_raw = mc.file(query=True, sceneName=True)
    if not isinstance(scene_raw, str) or not scene_raw:
        return None, None
    scene_path = Path(scene_raw).expanduser().resolve()
    stream = maya_anim_stream(shot, owner=shot_owner_for(shot))
    return current_version_label(stream, scene_path)
