from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import maya.cmds as mc
from Qt import QtCore
from Qt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shared.util import get_edit_path

from pipe.util import Playblaster

from .struct import (
    HudDefinition,
    MPlayblastConfig,
    MShotPlayblastConfig,
    SaveLocation,
    dummy_shot,
)
from .ui import PlayblastDialog


@dataclass(frozen=True)
class SequencerShotContext:
    node: str
    name: str
    camera: str
    cut_in: int
    cut_out: int
    cut_duration: int


class PrevisPlayblastDialog(PlayblastDialog):
    _context_banner: QLabel
    _custom_camera: QComboBox
    _custom_folder_row: QWidget
    _custom_in: QSpinBox
    _custom_out: QSpinBox
    _destination_checkboxes: dict[str, QCheckBox]
    _destination_path_labels: dict[str, QLabel]
    _save_locations_by_name: dict[str, SaveLocation]
    _sequencer_shot_nodes: list[str]
    _source_tabs: QTabWidget
    _shot_camera_value: QLabel
    _shot_name_value: QLabel
    _shot_range_value: QLabel
    _validation_label: QLabel

    SHOT_TAB_INDEX = 0
    CUSTOM_TAB_INDEX = 1

    class SAVE_LOCS(PlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            get_edit_path() / "previs" / datetime.now().strftime("%m-%d-%y"),
            Playblaster.PRESET.EDIT_SQ,
        )

    def __init__(self, parent) -> None:
        self._sequencer_shot_nodes = [
            str(shot_node)
            for shot_node in (mc.sequenceManager(listShots=True) or [])
            if mc.objExists(shot_node)
            and not bool(mc.shot(shot_node, query=True, mute=True))
        ]
        self._destination_checkboxes = {}
        self._destination_path_labels = {}
        self._save_locations_by_name = {
            location.name: location for location in self._destination_locations()
        }
        super().__init__(parent, [], "Bobo Previs Playblast")

    def _setup_ui(self) -> None:
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)
        self._main_layout = QVBoxLayout()
        self._central_widget.setLayout(self._main_layout)

        self._build_header_section()
        self._build_targets_section()
        self._build_render_options_section()
        self._build_buttons()
        self._set_default_source_tab()
        self._update_ui_state()

    def _build_header_section(self) -> None:
        title = QLabel("Bobo Previs Playblast")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)

        subtitle = QLabel("Choose source mode, choose destinations, then export.")
        subtitle.setStyleSheet("color: #666;")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_targets_section(self) -> None:
        setup_group = QGroupBox("1. Export Setup")
        setup_layout = QVBoxLayout(setup_group)

        self._context_banner = QLabel()
        self._context_banner.setWordWrap(True)
        self._context_banner.setStyleSheet(
            "padding: 6px; border: 1px solid #dcdcdc; background: #f7f7f7;"
        )
        setup_layout.addWidget(self._context_banner)

        setup_layout.addWidget(self._build_export_source_section())
        setup_layout.addWidget(self._build_destination_section())

        self._validation_label = QLabel()
        self._validation_label.setStyleSheet("color: #b00020;")
        self._validation_label.setVisible(False)
        setup_layout.addWidget(self._validation_label)

        self._main_layout.addWidget(setup_group)

    def _build_export_source_section(self) -> QGroupBox:
        source_group = QGroupBox("Export Source")
        source_layout = QVBoxLayout(source_group)

        self._source_tabs = QTabWidget()
        self._source_tabs.addTab(
            self._build_shot_source_tab(), "Shot Playblast (Current Sequencer Shot)"
        )
        self._source_tabs.addTab(
            self._build_custom_source_tab(),
            "Custom Playblast (Testing / Non-shot File)",
        )
        self._source_tabs.currentChanged.connect(self._on_source_mode_changed)
        source_layout.addWidget(self._source_tabs)
        return source_group

    def _build_shot_source_tab(self) -> QWidget:
        shot_tab = QWidget()
        shot_layout = QGridLayout(shot_tab)

        shot_layout.addWidget(QLabel("Source"), 0, 0)
        shot_layout.addWidget(QLabel("Current Sequencer Shot"), 0, 1)

        shot_layout.addWidget(QLabel("Shot"), 1, 0)
        self._shot_name_value = QLabel("-")
        shot_layout.addWidget(self._shot_name_value, 1, 1)

        shot_layout.addWidget(QLabel("Camera"), 2, 0)
        self._shot_camera_value = QLabel("-")
        shot_layout.addWidget(self._shot_camera_value, 2, 1)

        shot_layout.addWidget(QLabel("Frame Range"), 3, 0)
        self._shot_range_value = QLabel("-")
        shot_layout.addWidget(self._shot_range_value, 3, 1)
        return shot_tab

    def _build_custom_source_tab(self) -> QWidget:
        custom_tab = QWidget()
        custom_layout = QGridLayout(custom_tab)

        timeline_in, timeline_out = self._timeline_range()
        self._custom_in = QSpinBox(self, minimum=0, maximum=10000, value=timeline_in)
        self._custom_out = QSpinBox(self, minimum=0, maximum=10000, value=timeline_out)
        self._custom_out.setMinimum(self._custom_in.value())
        self._custom_in.valueChanged.connect(self._on_custom_in_changed)
        self._custom_out.valueChanged.connect(self._on_source_settings_changed)

        custom_layout.addWidget(QLabel("Custom In"), 0, 0)
        custom_layout.addWidget(self._custom_in, 0, 1)
        custom_layout.addWidget(QLabel("Custom Out"), 0, 2)
        custom_layout.addWidget(self._custom_out, 0, 3)

        camera_list = self._available_custom_cameras()
        self._custom_camera = QComboBox(self)
        self._custom_camera.addItems(camera_list)
        self._custom_camera.currentTextChanged.connect(self._on_source_settings_changed)
        custom_layout.addWidget(QLabel("Custom Camera"), 1, 0)
        custom_layout.addWidget(self._custom_camera, 1, 1, 1, 3)
        return custom_tab

    def _build_destination_section(self) -> QGroupBox:
        destination_group = QGroupBox("Save Destinations")
        destination_layout = QVBoxLayout(destination_group)

        help_label = QLabel("Select one or more destinations.")
        help_label.setStyleSheet("color: #666;")
        destination_layout.addWidget(help_label)

        for save_location in self._destination_locations():
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            destination_toggle = QCheckBox(save_location.name)
            destination_toggle.setChecked(
                self._default_destination_enabled(save_location)
            )
            destination_toggle.toggled.connect(self._on_destination_changed)
            self._destination_checkboxes[save_location.name] = destination_toggle
            row_layout.addWidget(destination_toggle)

            path_label = QLabel("")
            path_label.setStyleSheet("color: #666;")
            self._destination_path_labels[save_location.name] = path_label
            row_layout.addWidget(path_label)
            row_layout.addStretch()
            destination_layout.addWidget(row_widget)

        self._custom_folder_row = self._build_destination_path_row()
        destination_layout.addWidget(self._custom_folder_row)
        return destination_group

    def _build_destination_path_row(self) -> QWidget:
        custom_path_row = QWidget()
        custom_path_layout = QHBoxLayout(custom_path_row)
        custom_path_layout.setContentsMargins(24, 0, 0, 0)

        custom_path_layout.addWidget(QLabel("Custom Folder Path"))

        self._custom_folder_field = QLineEdit()
        self._custom_folder_field.setText(self._default_custom_folder_path())
        self._custom_folder_field.textChanged.connect(self._on_custom_path_changed)
        custom_path_layout.addWidget(self._custom_folder_field)

        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self._set_custom_folder)
        custom_path_layout.addWidget(browse_button)
        return custom_path_row

    def _build_render_options_section(self) -> None:
        options_group = QGroupBox("2. Viewport Options")
        options_layout = QVBoxLayout(options_group)
        options_layout.addWidget(
            self._build_viewport_options_widget(self._resolve_active_model_panel())
        )
        self._main_layout.addWidget(options_group)

    def _build_buttons(self) -> None:
        self._init_buttons(has_cancel_button=True, ok_name="Playblast Shot")
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.do_export)
        self._main_layout.addWidget(self.buttons)

    def _set_default_source_tab(self) -> None:
        has_shot_context = bool(self._sequencer_shot_nodes)
        self._source_tabs.setTabEnabled(self.SHOT_TAB_INDEX, has_shot_context)
        default_index = (
            self.SHOT_TAB_INDEX if has_shot_context else self.CUSTOM_TAB_INDEX
        )
        self._source_tabs.setCurrentIndex(default_index)

    @staticmethod
    def _destination_locations() -> list[SaveLocation]:
        return [
            PrevisPlayblastDialog.SAVE_LOCS.EDIT,
            PrevisPlayblastDialog.SAVE_LOCS.CURRENT,
            PrevisPlayblastDialog.SAVE_LOCS.CUSTOM,
        ]

    def _default_destination_enabled(self, location: SaveLocation) -> bool:
        return location.name == self.SAVE_LOCS.EDIT.name

    @staticmethod
    def _timeline_range() -> tuple[int, int]:
        cut_in = int(mc.playbackOptions(minTime=True, query=True))
        cut_out = int(mc.playbackOptions(maxTime=True, query=True))
        if cut_out < cut_in:
            cut_out = cut_in
        return cut_in, cut_out

    @staticmethod
    def _available_custom_cameras() -> list[str]:
        return [
            str(c)
            for c in (
                mc.ls(cameras=True, visible=True) or mc.ls(cameras=True) or ["persp"]
            )
        ]

    def _is_shot_mode_selected(self) -> bool:
        return self._source_tabs.currentIndex() == self.SHOT_TAB_INDEX

    def _selected_destination_locations(self) -> list[SaveLocation]:
        selected: list[SaveLocation] = []
        for location in self._destination_locations():
            toggle = self._destination_checkboxes.get(location.name)
            if toggle and toggle.isChecked():
                selected.append(location)
        return selected

    def _is_custom_destination_selected(self) -> bool:
        custom_checkbox = self._destination_checkboxes.get(self.SAVE_LOCS.CUSTOM.name)
        return bool(custom_checkbox and custom_checkbox.isChecked())

    def _paths_for_filename(
        self, filename: str
    ) -> dict[Playblaster.PRESET, list[str | Path]]:
        paths: dict[Playblaster.PRESET, list[str | Path]] = defaultdict(list)
        for location in self._selected_destination_locations():
            destination_dir = self._resolved_destination_path(location).strip()
            if not destination_dir:
                continue
            paths[location.preset].append(str(Path(destination_dir) / filename))
        return paths

    def _resolved_destination_path(self, location: SaveLocation) -> str:
        if location.name == self.SAVE_LOCS.CUSTOM.name:
            return self._custom_folder_field.text().strip()
        return str(location.path)

    def _refresh_destination_path_labels(self) -> None:
        for location_name, path_label in self._destination_path_labels.items():
            location = self._save_locations_by_name[location_name]
            path_label.setText(f"-> {self._resolved_destination_path(location)}")

    def _refresh_context_banner(self) -> None:
        shot_context = self._resolve_current_sequencer_shot_context()
        if self._is_shot_mode_selected():
            if shot_context is None:
                current_frame = int(mc.currentTime(query=True))
                banner_text = (
                    "No current sequencer shot found at frame "
                    f"{current_frame}. Move the timeline into a shot or switch to Custom Playblast."
                )
                self._shot_name_value.setText("-")
                self._shot_camera_value.setText("-")
                self._shot_range_value.setText("-")
            else:
                banner_text = (
                    f"Detected shot: {shot_context.name} | "
                    f"Camera: {shot_context.camera} | "
                    f"Range: {shot_context.cut_in}-{shot_context.cut_out}"
                )
                self._shot_name_value.setText(shot_context.name)
                self._shot_camera_value.setText(shot_context.camera)
                self._shot_range_value.setText(
                    f"{shot_context.cut_in} - {shot_context.cut_out}"
                )
        else:
            if shot_context is None:
                banner_text = "No shot context detected. Custom Playblast is active."
            else:
                banner_text = (
                    f"Detected shot: {shot_context.name}. "
                    "Custom Playblast is active and will use manual camera/range settings."
                )
        self._context_banner.setText(banner_text)

    def _sync_custom_path_row_visibility(self) -> None:
        is_visible = self._is_custom_destination_selected()
        self._custom_folder_row.setVisible(is_visible)
        self._custom_folder_field.setEnabled(is_visible)

    def _validate_target_destination_state(self) -> str | None:
        if self._is_shot_mode_selected():
            shot_context = self._resolve_current_sequencer_shot_context()
            if shot_context is None:
                return "No current sequencer shot was found. Move timeline to a shot or use Custom Playblast."
            if not shot_context.camera:
                return "Current sequencer shot has no camera assigned."
        else:
            if self._custom_out.value() < self._custom_in.value():
                return "Custom Out must be greater than or equal to Custom In."
            if not str(self._custom_camera.currentText()).strip():
                return "Choose a camera for Custom Playblast."

        if not self._selected_destination_locations():
            return "Select at least one save destination."

        if (
            self._is_custom_destination_selected()
            and not self._custom_folder_field.text().strip()
        ):
            return "Custom Folder path is required when Custom Folder destination is enabled."

        return None

    def _update_action_state(self) -> None:
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is None:
            return

        ok_button.setText(
            "Playblast Shot" if self._is_shot_mode_selected() else "Playblast Custom"
        )
        validation_error = self._validate_target_destination_state()
        ok_button.setEnabled(validation_error is None)
        self._validation_label.setText(validation_error or "")
        self._validation_label.setVisible(validation_error is not None)

    def _update_ui_state(self) -> None:
        self._sync_custom_path_row_visibility()
        self._refresh_destination_path_labels()
        self._refresh_context_banner()
        self._update_action_state()

    def _on_source_mode_changed(self, _index: int) -> None:
        self._update_ui_state()

    def _on_destination_changed(self, _checked: bool) -> None:
        self._update_ui_state()

    def _on_custom_path_changed(self, _path: str) -> None:
        self._update_ui_state()

    def _on_custom_in_changed(self, in_frame: int) -> None:
        self._custom_out.setMinimum(in_frame)
        self._update_ui_state()

    def _on_source_settings_changed(self, *_args) -> None:
        self._update_ui_state()

    def _refresh_summary(self, *_args) -> None:
        self._update_ui_state()

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
        if not self._sequencer_shot_nodes:
            return None

        current_frame = int(mc.currentTime(query=True))
        for shot_node in self._sequencer_shot_nodes:
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

    @staticmethod
    def _scene_stem() -> str:
        scene_name = Path(str(mc.file(query=True, sceneName=True) or "")).stem
        return scene_name or "previs_playblast"

    def _hud_shot_label(self) -> str:
        shot_context = self._resolve_current_sequencer_shot_context()
        if shot_context:
            return shot_context.name
        return "Custom"

    def _validate_config(self, config: MPlayblastConfig) -> str | None:
        validation_error = self._validate_target_destination_state()
        if validation_error:
            return validation_error
        return super()._validate_config(config)

    def _generate_config(self) -> MPlayblastConfig:
        validation_error = self._validate_target_destination_state()
        if validation_error:
            raise ValueError(validation_error)

        date_suffix = datetime.now().strftime("%m-%d-%y")
        if self._is_shot_mode_selected():
            shot_context = self._resolve_current_sequencer_shot_context()
            if shot_context is None:
                raise ValueError("No current sequencer shot was found.")

            output_name = f"{shot_context.name}_{date_suffix}"
            shot_config = MShotPlayblastConfig(
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
        else:
            custom_in = self._custom_in.value()
            custom_out = self._custom_out.value()
            custom_code = self._scene_stem()
            output_name = f"{custom_code}_custom_{date_suffix}"
            shot_config = MShotPlayblastConfig(
                camera=str(self._custom_camera.currentText()),
                shot=dummy_shot(
                    code=custom_code,
                    cut_in=custom_in,
                    cut_out=custom_out,
                    cut_duration=max(0, custom_out - custom_in),
                ),
                paths=self._paths_for_filename(output_name),
                use_sequencer=False,
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
