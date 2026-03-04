from __future__ import annotations

import logging
from dataclasses import dataclass

import maya.cmds as mc
from Qt.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QTabWidget,
    QWidget,
)
from shared.util import get_edit_path

from pipe.playblast_naming import playblast_date_folder
from pipe.util import Playblaster

from .struct import (
    HudDefinition,
    MPlayblastConfig,
    MShotPlayblastConfig,
    SaveLocation,
    dummy_shot,
)
from .ui import PlayblastDialog

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SequencerShotContext:
    node: str
    name: str
    camera: str
    cut_in: int
    cut_out: int
    cut_duration: int


class PrevisPlayblastDialog(PlayblastDialog):
    _sequencer_camera_value: QLabel
    _sequencer_name_value: QLabel
    _sequencer_range_value: QLabel
    _shot_camera: QComboBox

    SEQUENCER_TAB_INDEX: int

    class SAVE_LOCS(PlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            lambda: get_edit_path() / "previs" / playblast_date_folder(),
            Playblaster.PRESET.EDIT_SQ,
        )

    def __init__(self, parent) -> None:
        super().__init__(parent, windowTitle="SKD Previs Playblast")

    def _default_destination_enabled(self, location: SaveLocation) -> bool:
        return location.name == self.SAVE_LOCS.EDIT.name

    def _add_custom_tabs(self, tabs: QTabWidget) -> None:
        self.SEQUENCER_TAB_INDEX = tabs.count()
        tabs.addTab(
            self._build_sequencer_source_tab(),
            "Sequencer Playblast",
        )
        tabs.tabBar().setTabToolTip(
            self.SEQUENCER_TAB_INDEX,
            "Uses the camera sequencer shot under the current timeline frame.",
        )

    def _build_sequencer_source_tab(self) -> QWidget:
        sequencer_tab = QWidget()
        sequencer_layout = QGridLayout(sequencer_tab)

        sequencer_layout.addWidget(QLabel("Source"), 0, 0)
        sequencer_source_value = QLabel("Current Sequencer Shot")
        sequencer_source_value.setToolTip(
            "Uses the sequencer shot at the current timeline frame."
        )
        sequencer_layout.addWidget(sequencer_source_value, 0, 1)

        sequencer_layout.addWidget(QLabel("Shot"), 1, 0)
        self._sequencer_name_value = QLabel("-")
        self._sequencer_name_value.setToolTip(
            "Resolved sequencer shot name for the current frame."
        )
        sequencer_layout.addWidget(self._sequencer_name_value, 1, 1)

        sequencer_layout.addWidget(QLabel("Camera"), 2, 0)
        self._sequencer_camera_value = QLabel("-")
        self._sequencer_camera_value.setToolTip(
            "Resolved camera from the active sequencer shot."
        )
        sequencer_layout.addWidget(self._sequencer_camera_value, 2, 1)

        sequencer_layout.addWidget(QLabel("Frame Range"), 3, 0)
        self._sequencer_range_value = QLabel("-")
        self._sequencer_range_value.setToolTip(
            "Resolved frame range from the active sequencer shot."
        )
        sequencer_layout.addWidget(self._sequencer_range_value, 3, 1)

        return sequencer_tab

    def _build_shot_camera_widget(self) -> QWidget:
        self._shot_camera = QComboBox(self)
        self._shot_camera.addItems(self._available_custom_cameras())
        self._shot_camera.setToolTip(
            "Camera used for shot playblast output in Shot mode."
        )
        self._shot_camera.currentTextChanged.connect(self._on_source_settings_changed)
        self._set_default_shot_camera()
        return self._shot_camera

    def _validate_source_state(self, mode: str) -> str | None:
        if mode == "shot":
            if self._shot and self._shot.cut_out < self._shot.cut_in:
                return "Shot cut range is invalid (Cut Out must be >= Cut In)."
            if not str(self._shot_camera.currentText()).strip():
                return "Choose a camera for Shot Playblast."

        if mode == "sequencer":
            shot_context = self._resolve_current_sequencer_shot_context()
            if shot_context is None:
                return "No current sequencer shot was found. Move timeline to a shot or use another source mode."
            if not shot_context.camera:
                return "Current sequencer shot has no camera assigned."

        return None

    def _refresh_custom_ui_state(self) -> None:
        self._refresh_sequencer_context_fields()
        has_sequencer_context = self._has_sequencer_shot_context()
        self._source_tabs.setTabEnabled(
            self.SEQUENCER_TAB_INDEX,
            has_sequencer_context,
        )

    def _refresh_sequencer_context_fields(self) -> SequencerShotContext | None:
        shot_context = self._resolve_current_sequencer_shot_context()
        if shot_context is None:
            self._sequencer_name_value.setText("-")
            self._sequencer_camera_value.setText("-")
            self._sequencer_range_value.setText("-")
            return None

        self._sequencer_name_value.setText(shot_context.name)
        self._sequencer_camera_value.setText(shot_context.camera)
        self._sequencer_range_value.setText(
            f"{shot_context.cut_in} - {shot_context.cut_out}"
        )
        return shot_context

    @staticmethod
    def _active_camera_name() -> str:
        panel = PlayblastDialog._resolve_active_model_panel()
        if not panel:
            return ""
        try:
            camera = str(mc.modelEditor(panel, query=True, camera=True) or "")
        except Exception:
            return ""
        return camera.strip()

    @staticmethod
    def _camera_name_variants(camera_name: str) -> set[str]:
        if not camera_name:
            return set()
        variants = {camera_name, camera_name.split("|")[-1], camera_name.split(":")[-1]}
        if not mc.objExists(camera_name):
            return variants
        node_type = str(mc.nodeType(camera_name) or "")
        if node_type == "transform":
            shapes = (
                mc.listRelatives(
                    camera_name,
                    shapes=True,
                    type="camera",
                    fullPath=True,
                )
                or []
            )
            for shape in shapes:
                shape_name = str(shape)
                variants.add(shape_name)
                variants.add(shape_name.split("|")[-1])
                variants.add(shape_name.split(":")[-1])
        if node_type == "camera":
            parents = mc.listRelatives(camera_name, parent=True, fullPath=True) or []
            for parent in parents:
                parent_name = str(parent)
                variants.add(parent_name)
                variants.add(parent_name.split("|")[-1])
                variants.add(parent_name.split(":")[-1])
        return variants

    def _set_default_shot_camera(self) -> None:
        camera_name = self._active_camera_name()
        variants = self._camera_name_variants(camera_name)
        if not variants:
            return
        for index in range(self._shot_camera.count()):
            item_text = self._shot_camera.itemText(index)
            if item_text in variants:
                self._shot_camera.setCurrentIndex(index)
                return

    @staticmethod
    def _list_sequencer_shot_nodes() -> list[str]:
        shot_nodes = mc.sequenceManager(listShots=True)
        if isinstance(shot_nodes, str):
            candidate_nodes = [shot_nodes]
        elif isinstance(shot_nodes, (list, tuple)):
            candidate_nodes = [str(node) for node in shot_nodes]
        else:
            candidate_nodes = []
        return [
            shot_node
            for shot_node in candidate_nodes
            if mc.objExists(shot_node)
            and not bool(mc.shot(shot_node, query=True, mute=True))
        ]

    def _has_sequencer_shot_context(self) -> bool:
        return bool(self._list_sequencer_shot_nodes())

    def _resolve_current_sequencer_shot_context(self) -> SequencerShotContext | None:
        shot_node = self._resolve_current_shot_node()
        if not shot_node:
            return None

        try:
            shot_name = str(mc.shot(shot_node, query=True, shotName=True) or shot_node)
            shot_camera = str(mc.shot(shot_node, query=True, currentCamera=True) or "")
            cut_in = int(mc.shot(shot_node, query=True, startTime=True))
            cut_out = int(mc.shot(shot_node, query=True, endTime=True))
            cut_duration = int(mc.shot(shot_node, query=True, clipDuration=True))
        except Exception:
            return None

        if cut_out < cut_in:
            cut_out = cut_in
        if cut_duration < 0:
            cut_duration = 0

        return SequencerShotContext(
            node=shot_node,
            name=shot_name,
            camera=shot_camera,
            cut_in=cut_in,
            cut_out=cut_out,
            cut_duration=cut_duration,
        )

    def _resolve_current_shot_node(self) -> str | None:
        current_frame = int(mc.currentTime(query=True))

        for shot_node in self._list_sequencer_shot_nodes():
            if not mc.objExists(shot_node):
                continue
            try:
                shot_in = int(mc.shot(shot_node, query=True, startTime=True))
                shot_out = int(mc.shot(shot_node, query=True, endTime=True))
            except Exception:
                continue
            if shot_in <= current_frame <= shot_out:
                return shot_node
        return None

    def _hud_shot_label(self) -> str:
        mode = self._selected_source_mode()

        if mode == "shot" and self._shot is not None:
            return self._shot.code

        if mode == "sequencer":
            shot_context = self._resolve_current_sequencer_shot_context()
            if shot_context is not None:
                return shot_context.name

        if self._shot is not None:
            return self._shot.code

        return "Custom"

    def _build_shot_playblast_config(self) -> MShotPlayblastConfig:
        if self._shot is None:
            raise ValueError("No pipeline shot context was found.")

        shot_camera = str(self._shot_camera.currentText()).strip()
        output_name = self._resolve_output_name(self._shot.code)
        return MShotPlayblastConfig(
            camera=shot_camera,
            shot=self._shot,
            paths=self._paths_for_filename(output_name),
            use_sequencer=False,
        )

    def _build_sequencer_playblast_config(self) -> MShotPlayblastConfig:
        shot_context = self._resolve_current_sequencer_shot_context()
        if shot_context is None:
            raise ValueError("No current sequencer shot was found.")

        output_name = self._resolve_output_name(shot_context.name)
        return MShotPlayblastConfig(
            camera=shot_context.camera,
            shot=dummy_shot(
                code=shot_context.name,
                cut_in=shot_context.cut_in,
                cut_out=shot_context.cut_out,
                cut_duration=shot_context.cut_duration,
            ),
            paths=self._paths_for_filename(output_name),
            use_sequencer=False,
        )

    def _generate_config(self) -> MPlayblastConfig:
        mode = self._selected_source_mode()
        if mode == "shot":
            shot_config = self._build_shot_playblast_config()
        elif mode == "sequencer":
            shot_config = self._build_sequencer_playblast_config()
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
                    idle_refresh=True,
                ),
            ],
            dof=self.use_dof,
            hardware_fog=self.use_hardware_fog,
            lighting=self.use_lighting,
            shadows=self.use_shadows,
            shots=[shot_config],
            ssao=self.use_ssao,
        )
