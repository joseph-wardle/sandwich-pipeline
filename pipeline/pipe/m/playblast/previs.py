from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import maya.cmds as mc
from env_sg import DB_Config
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

from pipe.db import DB
from pipe.playblast_naming import (
    playblast_date_folder,
    resolve_versioned_playblast_basename,
)
from pipe.playblast_shotgrid import resolve_preferred_upload_movie_path
from pipe.util import Playblaster

from .struct import (
    HudDefinition,
    MPlayblastConfig,
    MShotPlayblastConfig,
    SaveLocation,
    dummy_shot,
)
from .ui import PlayblastDialog

if TYPE_CHECKING:
    from pipe.struct.db import Shot

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
    _custom_camera: QComboBox
    _custom_folder_row: QWidget
    _custom_in: QSpinBox
    _custom_out: QSpinBox
    _destination_checkboxes: dict[str, QCheckBox]
    _destination_path_labels: dict[str, QLabel]
    _save_locations_by_name: dict[str, SaveLocation]
    _sequencer_camera_value: QLabel
    _sequencer_name_value: QLabel
    _sequencer_range_value: QLabel
    _shot: Shot | None
    _shot_camera: QComboBox
    _shot_code_value: QLabel
    _shotgrid_description_field: QLineEdit
    _shotgrid_description_row: QWidget
    _shotgrid_upload_checkbox: QCheckBox
    _shot_range_value: QLabel
    _source_tabs: QTabWidget
    _validation_label: QLabel

    SHOT_TAB_INDEX = 0
    SEQUENCER_TAB_INDEX = 1
    CUSTOM_TAB_INDEX = 2

    SOURCE_MODE = Literal["shot", "sequencer", "custom"]

    class SAVE_LOCS(PlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            lambda: get_edit_path() / "previs" / playblast_date_folder(),
            Playblaster.PRESET.EDIT_SQ,
        )

    def __init__(self, parent) -> None:
        self._shot = self._resolve_pipeline_shot_context()
        self._destination_checkboxes = {}
        self._destination_path_labels = {}
        self._save_locations_by_name = {
            location.name: location for location in self._destination_locations()
        }
        super().__init__(parent, [], "SKD Previs Playblast")

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
        title = QLabel("SKD Previs Playblast")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)

        subtitle = QLabel("Choose source mode, choose destinations, then export")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setToolTip(
            "Playblast flow: choose the source mode, choose destinations, then export."
        )

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_targets_section(self) -> None:
        setup_group = QGroupBox("1. Export Setup")
        setup_layout = QVBoxLayout(setup_group)

        setup_layout.addWidget(self._build_export_source_section())
        setup_layout.addWidget(self._build_destination_section())

        self._validation_label = QLabel()
        self._validation_label.setStyleSheet("color: #b00020;")
        self._validation_label.setToolTip(
            "Validation feedback. Export stays disabled until all required inputs are valid."
        )
        self._validation_label.setVisible(False)
        setup_layout.addWidget(self._validation_label)

        self._main_layout.addWidget(setup_group)

    def _build_export_source_section(self) -> QGroupBox:
        source_group = QGroupBox("")
        source_layout = QVBoxLayout(source_group)

        self._source_tabs = QTabWidget()
        self._source_tabs.addTab(self._build_shot_source_tab(), "Shot Playblast")
        self._source_tabs.addTab(
            self._build_sequencer_source_tab(),
            "Sequencer Playblast",
        )
        self._source_tabs.addTab(
            self._build_custom_source_tab(),
            "Custom Playblast",
        )
        self._source_tabs.currentChanged.connect(self._on_source_mode_changed)
        self._source_tabs.setToolTip(
            "Select how the playblast source is resolved: pipeline shot metadata, sequencer shot, or manual custom settings."
        )

        source_tab_bar = self._source_tabs.tabBar()
        source_tab_bar.setTabToolTip(
            self.SHOT_TAB_INDEX,
            "Uses shot code metadata from the current Maya file (fileInfo 'code') and ShotGrid cut range.",
        )
        source_tab_bar.setTabToolTip(
            self.SEQUENCER_TAB_INDEX,
            "Uses the camera sequencer shot under the current timeline frame.",
        )
        source_tab_bar.setTabToolTip(
            self.CUSTOM_TAB_INDEX,
            "Uses manual camera and manual frame range, independent of shot metadata.",
        )

        source_layout.addWidget(self._source_tabs)
        return source_group

    def _build_shot_source_tab(self) -> QWidget:
        shot_tab = QWidget()
        shot_layout = QGridLayout(shot_tab)

        shot_layout.addWidget(QLabel("Source"), 0, 0)
        shot_source_value = QLabel("Pipeline Shot File")
        shot_source_value.setToolTip(
            "Source is derived from this Maya scene's pipeline shot metadata."
        )
        shot_layout.addWidget(shot_source_value, 0, 1)

        shot_layout.addWidget(QLabel("Shot"), 1, 0)
        self._shot_code_value = QLabel("-")
        self._shot_code_value.setToolTip("Resolved pipeline shot code.")
        shot_layout.addWidget(self._shot_code_value, 1, 1)

        shot_layout.addWidget(QLabel("Camera"), 2, 0)
        self._shot_camera = QComboBox(self)
        self._shot_camera.addItems(self._available_custom_cameras())
        self._shot_camera.setToolTip(
            "Camera used for shot playblast output in Shot mode."
        )
        self._shot_camera.currentTextChanged.connect(self._on_source_settings_changed)
        shot_layout.addWidget(self._shot_camera, 2, 1)

        shot_layout.addWidget(QLabel("Frame Range"), 3, 0)
        self._shot_range_value = QLabel("-")
        self._shot_range_value.setToolTip(
            "Resolved cut range from ShotGrid for the detected pipeline shot."
        )
        shot_layout.addWidget(self._shot_range_value, 3, 1)

        shot_layout.addWidget(QLabel("ShotGrid"), 4, 0)
        self._shotgrid_upload_checkbox = QCheckBox("Upload to ShotGrid")
        self._shotgrid_upload_checkbox.setChecked(False)
        self._shotgrid_upload_checkbox.setToolTip(
            "When enabled, this Shot playblast will also create a ShotGrid Version and upload the movie."
        )
        self._shotgrid_upload_checkbox.toggled.connect(self._on_shotgrid_upload_toggled)
        shot_layout.addWidget(self._shotgrid_upload_checkbox, 4, 1)

        self._shotgrid_description_row = QWidget()
        shotgrid_description_layout = QHBoxLayout(self._shotgrid_description_row)
        shotgrid_description_layout.setContentsMargins(0, 0, 0, 0)
        shotgrid_description_layout.addWidget(QLabel("Description"))
        self._shotgrid_description_field = QLineEdit()
        self._shotgrid_description_field.setPlaceholderText(
            "Optional ShotGrid version description"
        )
        self._shotgrid_description_field.setToolTip(
            "Optional notes saved to the ShotGrid Version description when upload is enabled."
        )
        shotgrid_description_layout.addWidget(self._shotgrid_description_field)
        shot_layout.addWidget(self._shotgrid_description_row, 5, 0, 1, 2)

        self._set_default_shot_camera()
        self._sync_shotgrid_description_visibility()
        return shot_tab

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

    def _build_custom_source_tab(self) -> QWidget:
        custom_tab = QWidget()
        custom_layout = QGridLayout(custom_tab)

        timeline_in, timeline_out = self._timeline_range()
        self._custom_in = QSpinBox(self, minimum=0, maximum=10000, value=timeline_in)
        self._custom_out = QSpinBox(self, minimum=0, maximum=10000, value=timeline_out)
        self._custom_out.setMinimum(self._custom_in.value())
        self._custom_in.setToolTip("Start frame for custom playblast.")
        self._custom_out.setToolTip("End frame for custom playblast.")
        self._custom_in.valueChanged.connect(self._on_custom_in_changed)
        self._custom_out.valueChanged.connect(self._on_source_settings_changed)

        custom_layout.addWidget(QLabel("Custom In"), 0, 0)
        custom_layout.addWidget(self._custom_in, 0, 1)
        custom_layout.addWidget(QLabel("Custom Out"), 0, 2)
        custom_layout.addWidget(self._custom_out, 0, 3)

        self._custom_camera = QComboBox(self)
        self._custom_camera.addItems(self._available_custom_cameras())
        self._custom_camera.setToolTip("Camera used for custom playblast output.")
        self._custom_camera.currentTextChanged.connect(self._on_source_settings_changed)
        custom_layout.addWidget(QLabel("Custom Camera"), 1, 0)
        custom_layout.addWidget(self._custom_camera, 1, 1, 1, 3)
        return custom_tab

    def _build_destination_section(self) -> QGroupBox:
        destination_group = QGroupBox("Save Destinations")
        destination_layout = QVBoxLayout(destination_group)

        for save_location in self._destination_locations():
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            destination_toggle = QCheckBox(save_location.name)
            destination_toggle.setChecked(
                self._default_destination_enabled(save_location)
            )
            destination_toggle.setToolTip(f"Enable export to {save_location.name}.")
            destination_toggle.toggled.connect(self._on_destination_changed)
            self._destination_checkboxes[save_location.name] = destination_toggle
            row_layout.addWidget(destination_toggle)

            path_label = QLabel("")
            path_label.setToolTip(
                f"Resolved output directory for {save_location.name}."
            )
            self._destination_path_labels[save_location.name] = path_label
            row_layout.addWidget(path_label)
            row_layout.addStretch()
            destination_layout.addWidget(row_widget)

        self._align_destination_path_columns()
        self._custom_folder_row = self._build_destination_path_row()
        destination_layout.addWidget(self._custom_folder_row)
        return destination_group

    def _align_destination_path_columns(self) -> None:
        destination_column_width = max(
            (
                checkbox.sizeHint().width()
                for checkbox in self._destination_checkboxes.values()
            ),
            default=0,
        )
        for checkbox in self._destination_checkboxes.values():
            checkbox.setFixedWidth(destination_column_width)

    def _build_destination_path_row(self) -> QWidget:
        custom_path_row = QWidget()
        custom_path_layout = QHBoxLayout(custom_path_row)
        custom_path_layout.setContentsMargins(24, 0, 0, 0)

        custom_path_layout.addWidget(QLabel("Custom Folder Path"))

        self._custom_folder_field = QLineEdit()
        self._custom_folder_field.setText(self._default_custom_folder_path())
        self._custom_folder_field.setToolTip(
            "Directory used when Custom Folder destination is enabled."
        )
        self._custom_folder_field.textChanged.connect(self._on_custom_path_changed)
        custom_path_layout.addWidget(self._custom_folder_field)

        browse_button = QPushButton("Browse")
        browse_button.setToolTip("Choose a custom output directory.")
        browse_button.clicked.connect(self._set_custom_folder)
        custom_path_layout.addWidget(browse_button)
        return custom_path_row

    def _build_render_options_section(self) -> None:
        options_group = QGroupBox("2. Viewport Options")
        options_layout = QVBoxLayout(options_group)
        options_layout.addWidget(
            self._build_viewport_options_widget(self._resolve_active_model_panel())
        )
        self._apply_viewport_option_tooltips()
        self._main_layout.addWidget(options_group)

    def _build_buttons(self) -> None:
        self._init_buttons(has_cancel_button=True, ok_name="Playblast Shot")
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.do_export)

        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setToolTip(
                "Start playblast with the current source and destination selections."
            )

        cancel_button = self.buttons.button(QDialogButtonBox.Cancel)
        if cancel_button is not None:
            cancel_button.setToolTip("Close without exporting.")

        self._main_layout.addWidget(self.buttons)

    def _apply_viewport_option_tooltips(self) -> None:
        self._use_lighting.setToolTip("Use viewport lighting for playblast capture.")
        self._use_shadows.setToolTip("Render viewport shadows in playblast.")
        self._use_ssao.setToolTip(
            "Enable viewport anti-aliasing (SSAO/multi-sample setting)."
        )
        self._use_hardware_fog.setToolTip(
            "Include hardware fog from viewport settings."
        )
        self._use_dof.setToolTip("Include camera depth of field in playblast.")

    def _set_default_source_tab(self) -> None:
        self._refresh_source_tab_availability()

        self._source_tabs.setCurrentIndex(self._default_source_tab_index())

    def _refresh_source_tab_availability(self) -> None:
        has_shot_context = self._shot is not None
        has_sequencer_context = self._has_sequencer_shot_context()

        self._source_tabs.setTabEnabled(self.SHOT_TAB_INDEX, has_shot_context)
        self._source_tabs.setTabEnabled(
            self.SEQUENCER_TAB_INDEX,
            has_sequencer_context,
        )

        selected_mode = self._selected_source_mode()
        if selected_mode == "shot" and not has_shot_context:
            self._source_tabs.setCurrentIndex(self._default_source_tab_index())
        if selected_mode == "sequencer" and not has_sequencer_context:
            self._source_tabs.setCurrentIndex(self._default_source_tab_index())

    def _default_source_tab_index(self) -> int:
        has_shot_context = self._shot is not None
        has_sequencer_context = self._has_sequencer_shot_context()

        if has_shot_context:
            return self.SHOT_TAB_INDEX
        if has_sequencer_context:
            return self.SEQUENCER_TAB_INDEX
        return self.CUSTOM_TAB_INDEX

    @staticmethod
    def _resolve_pipeline_shot_context() -> Shot | None:
        try:
            conn = DB.Get(DB_Config)
        except Exception:
            return None

        try:
            code = str(mc.fileInfo("code", query=True)[0]).strip()
        except Exception:
            return None

        if not code:
            return None

        try:
            return conn.get_shot_by_code(code)
        except Exception:
            return None

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

    @staticmethod
    def _set_combo_to_camera(combo: QComboBox, camera_name: str) -> None:
        variants = PrevisPlayblastDialog._camera_name_variants(camera_name)
        if not variants:
            return

        for index in range(combo.count()):
            item_text = combo.itemText(index)
            if item_text in variants:
                combo.setCurrentIndex(index)
                return

    def _set_default_shot_camera(self) -> None:
        self._set_combo_to_camera(self._shot_camera, self._active_camera_name())

    @staticmethod
    def _list_sequencer_shot_nodes() -> list[str]:
        return [
            str(shot_node)
            for shot_node in (mc.sequenceManager(listShots=True) or [])
            if mc.objExists(shot_node)
            and not bool(mc.shot(shot_node, query=True, mute=True))
        ]

    def _has_sequencer_shot_context(self) -> bool:
        return bool(self._list_sequencer_shot_nodes())

    @staticmethod
    def _destination_locations() -> list[SaveLocation]:
        return [
            PrevisPlayblastDialog.SAVE_LOCS.EDIT,
            PrevisPlayblastDialog.SAVE_LOCS.CURRENT,
            PrevisPlayblastDialog.SAVE_LOCS.CUSTOM,
        ]

    def _default_destination_enabled(self, location: SaveLocation) -> bool:
        return location.name == self.SAVE_LOCS.EDIT.name

    def _selected_source_mode(self) -> SOURCE_MODE:
        current_index = self._source_tabs.currentIndex()
        if current_index == self.SHOT_TAB_INDEX:
            return "shot"
        if current_index == self.SEQUENCER_TAB_INDEX:
            return "sequencer"
        return "custom"

    def _selected_shot_camera(self) -> str:
        return str(self._shot_camera.currentText()).strip()

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

    @staticmethod
    def _final_movie_path_for_base(
        output_base: str | Path,
        preset: Playblaster.PRESET,
    ) -> Path:
        return Path(str(output_base) + f".{preset.ext}")

    def _final_movie_paths_for_location(
        self,
        shot_config: MShotPlayblastConfig,
        location: SaveLocation,
    ) -> list[Path]:
        destination_dir = Path(self._resolved_destination_path(location)).expanduser()
        resolved_destination_dir = destination_dir.resolve()

        matching_paths: list[Path] = []
        for output_base in shot_config.paths.get(location.preset, []):
            resolved_output_base = Path(str(output_base)).expanduser().resolve()
            if resolved_output_base.parent != resolved_destination_dir:
                continue
            matching_paths.append(
                self._final_movie_path_for_base(resolved_output_base, location.preset)
            )
        return matching_paths

    def _ordered_final_movie_paths_for_upload(
        self,
        shot_config: MShotPlayblastConfig,
    ) -> list[Path]:
        """Return deterministic output path order for upload path resolution."""

        ordered_paths: list[Path] = []
        seen_paths: set[Path] = set()

        for location in self._destination_locations():
            for output_path in self._final_movie_paths_for_location(
                shot_config, location
            ):
                if output_path in seen_paths:
                    continue
                seen_paths.add(output_path)
                ordered_paths.append(output_path)

        for preset, output_bases in shot_config.paths.items():
            for output_base in output_bases:
                output_path = self._final_movie_path_for_base(output_base, preset)
                resolved_output_path = output_path.expanduser().resolve()
                if resolved_output_path in seen_paths:
                    continue
                seen_paths.add(resolved_output_path)
                ordered_paths.append(resolved_output_path)

        return ordered_paths

    def _preferred_edit_movie_paths_for_upload(
        self,
        shot_config: MShotPlayblastConfig,
    ) -> list[Path]:
        edit_location = self._save_locations_by_name.get(self.SAVE_LOCS.EDIT.name)
        if edit_location is None:
            return []
        return self._final_movie_paths_for_location(shot_config, edit_location)

    def _resolve_shotgrid_upload_movie_path(
        self,
        config: MPlayblastConfig,
    ) -> Path | None:
        """Resolve upload movie path with stable preference ordering.

        Preference order:
        1) valid `Send to Edit` output
        2) first valid output from the deterministic export order
        """
        if not config.shots:
            return None

        shot_config = config.shots[0]
        preferred_paths = self._preferred_edit_movie_paths_for_upload(shot_config)
        output_paths = self._ordered_final_movie_paths_for_upload(shot_config)
        return resolve_preferred_upload_movie_path(
            output_paths,
            preferred_paths=preferred_paths,
        )

    def _selected_destination_directories(self) -> list[Path]:
        directories: list[Path] = []
        for location in self._selected_destination_locations():
            destination_dir = self._resolved_destination_path(location).strip()
            if destination_dir:
                directories.append(Path(destination_dir))
        return directories

    def _resolve_output_name(self, prefix: str) -> str:
        return resolve_versioned_playblast_basename(
            prefix,
            self._selected_destination_directories(),
        )

    def _resolved_destination_path(self, location: SaveLocation) -> str:
        if location.name == self.SAVE_LOCS.CUSTOM.name:
            return self._custom_folder_field.text().strip()
        return str(location.path)

    def _refresh_destination_path_labels(self) -> None:
        for location_name, path_label in self._destination_path_labels.items():
            location = self._save_locations_by_name[location_name]
            path_label.setText(f"-> {self._resolved_destination_path(location)}")

    def _refresh_shot_context_fields(self) -> None:
        if self._shot is None:
            self._shot_code_value.setText("-")
            self._shot_range_value.setText("-")
            return

        self._shot_code_value.setText(self._shot.code)
        self._shot_range_value.setText(f"{self._shot.cut_in} - {self._shot.cut_out}")

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

    def _sync_custom_path_row_visibility(self) -> None:
        is_visible = self._is_custom_destination_selected()
        self._custom_folder_row.setVisible(is_visible)
        self._custom_folder_field.setEnabled(is_visible)

    def _sync_shotgrid_description_visibility(self) -> None:
        show_description = self._is_shotgrid_upload_requested()
        self._shotgrid_description_row.setVisible(show_description)
        self._shotgrid_description_field.setEnabled(show_description)

    def _is_shotgrid_upload_requested(self) -> bool:
        return self._shotgrid_upload_checkbox.isChecked()

    def _shotgrid_upload_description(self) -> str:
        return self._shotgrid_description_field.text().strip()

    def _validate_target_destination_state(self) -> str | None:
        mode = self._selected_source_mode()

        if mode == "shot":
            if self._shot is None:
                return (
                    "No pipeline shot context was found. Open a pipeline shot file "
                    "or switch to Sequencer or Custom Playblast."
                )
            if self._shot.cut_out < self._shot.cut_in:
                return "Shot cut range is invalid (Cut Out must be >= Cut In)."
            if not self._selected_shot_camera():
                return "Choose a camera for Shot Playblast."

        if mode == "sequencer":
            shot_context = self._resolve_current_sequencer_shot_context()
            if shot_context is None:
                return "No current sequencer shot was found. Move timeline to a shot or use another source mode."
            if not shot_context.camera:
                return "Current sequencer shot has no camera assigned."

        if mode == "custom":
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

    def _action_button_text(self) -> str:
        mode = self._selected_source_mode()
        if mode == "shot":
            return "Playblast Shot"
        if mode == "sequencer":
            return "Playblast Sequencer"
        return "Playblast Custom"

    def _update_action_state(self) -> None:
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is None:
            return

        ok_button.setText(self._action_button_text())
        validation_error = self._validate_target_destination_state()
        ok_button.setEnabled(validation_error is None)
        self._validation_label.setText(validation_error or "")
        self._validation_label.setVisible(validation_error is not None)

    def _update_ui_state(self) -> None:
        self._refresh_source_tab_availability()
        self._refresh_shot_context_fields()
        self._refresh_sequencer_context_fields()
        self._sync_custom_path_row_visibility()
        self._sync_shotgrid_description_visibility()
        self._refresh_destination_path_labels()
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

    def _on_shotgrid_upload_toggled(self, _enabled: bool) -> None:
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

    @staticmethod
    def _scene_stem() -> str:
        scene_name = Path(str(mc.file(query=True, sceneName=True) or "")).stem
        return scene_name or "previs_playblast"

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

    def _validate_config(self, config: MPlayblastConfig) -> str | None:
        validation_error = self._validate_target_destination_state()
        if validation_error:
            return validation_error
        return super()._validate_config(config)

    def _build_shot_playblast_config(self) -> MShotPlayblastConfig:
        if self._shot is None:
            raise ValueError("No pipeline shot context was found.")

        shot_camera = self._selected_shot_camera()
        if not shot_camera:
            raise ValueError("Choose a camera for Shot Playblast.")

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

    def _build_custom_playblast_config(self) -> MShotPlayblastConfig:
        custom_in = self._custom_in.value()
        custom_out = self._custom_out.value()
        custom_code = self._scene_stem()
        output_name = self._resolve_output_name(f"{custom_code}_custom")

        return MShotPlayblastConfig(
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

    def _generate_config(self) -> MPlayblastConfig:
        validation_error = self._validate_target_destination_state()
        if validation_error:
            raise ValueError(validation_error)

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
