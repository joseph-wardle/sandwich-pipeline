from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import hou
from Qt import QtCore, QtWidgets
from shared.util import get_edit_path

from pipe.glui.dialogs import DialogButtons
from pipe.playblast_naming import resolve_versioned_playblast_basename
from pipe.playblast_shotgrid import (
    UPLOAD_TARGET_REVIEW,
    UPLOAD_TARGET_VERSION_ONLY,
    list_recent_review_playlists,
)

from .paths import build_edit_output_directory

if TYPE_CHECKING:
    from pipe.db import DB
    from pipe.struct.db import Shot

log = logging.getLogger(__name__)


SOURCE_MODE = Literal["shot", "custom"]

DEPARTMENTS = ("anim", "comp", "fx", "lighting", "previs")


@dataclass(frozen=True)
class DestinationOption:
    name: str
    tooltip: str


class HPlayblastDialog(QtWidgets.QDialog, DialogButtons):
    SHOT_TAB_INDEX = 0
    CUSTOM_TAB_INDEX = 1

    DESTINATION_EDIT = "Send to Edit"
    DESTINATION_CURRENT = "Current Folder"
    DESTINATION_CUSTOM = "Custom Folder"
    DESTINATION_ORDER = (
        DESTINATION_EDIT,
        DESTINATION_CURRENT,
        DESTINATION_CUSTOM,
    )

    CURRENT_VIEWPORT_CAMERA_TOKEN = "__current_viewport_camera__"

    _conn: DB
    _custom_camera: QtWidgets.QComboBox
    _custom_folder_field: QtWidgets.QLineEdit
    _custom_folder_row: QtWidgets.QWidget
    _custom_in: QtWidgets.QSpinBox
    _custom_out: QtWidgets.QSpinBox
    _default_shot_code: str
    _dept_combo: QtWidgets.QComboBox
    _destination_checkboxes: dict[str, QtWidgets.QCheckBox]
    _destination_path_labels: dict[str, QtWidgets.QLabel]
    _main_layout: QtWidgets.QVBoxLayout
    _shot: Shot | None
    _shot_camera_value: QtWidgets.QLabel
    _shot_code_value: QtWidgets.QLabel
    _shot_range_value: QtWidgets.QLabel
    _shotgrid_description_field: QtWidgets.QLineEdit
    _shotgrid_description_row: QtWidgets.QWidget
    _shotgrid_review_combo: QtWidgets.QComboBox
    _shotgrid_review_refresh_button: QtWidgets.QPushButton
    _shotgrid_review_row: QtWidgets.QWidget
    _shotgrid_upload_checkbox: QtWidgets.QCheckBox
    _shotgrid_upload_review_checkbox: QtWidgets.QCheckBox
    _shotgrid_upload_target_row: QtWidgets.QWidget
    _shotgrid_upload_version_checkbox: QtWidgets.QCheckBox
    _shotgrid_review_lazy_load_attempted: bool
    _shotgrid_review_load_error: str | None
    _source_tabs: QtWidgets.QTabWidget
    _validation_label: QtWidgets.QLabel

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        conn: "DB",
        default_shot_code: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._default_shot_code = (default_shot_code or "").strip()
        self._shot = self._resolve_shot_context(self._default_shot_code)
        self._destination_checkboxes = {}
        self._destination_path_labels = {}
        self._shotgrid_review_lazy_load_attempted = False
        self._shotgrid_review_load_error = None

        self._init_buttons(True, "Playblast Shot", "Cancel")
        self.setWindowTitle("Houdini Playblast")

        self._setup_ui()
        self._wire_ui_signals()
        self._set_default_source_tab()
        self._update_ui_state()

    @property
    def shot_code(self) -> str:
        return self._shot_code_value.text().strip()

    @property
    def department(self) -> str:
        return str(self._dept_combo.currentText()).strip()

    @property
    def selected_source_mode(self) -> SOURCE_MODE:
        if self._source_tabs.currentIndex() == self.SHOT_TAB_INDEX:
            return "shot"
        return "custom"

    @property
    def upload_to_shotgrid(self) -> bool:
        return self._shotgrid_upload_checkbox.isChecked()

    @property
    def shotgrid_upload_target(self) -> str:
        if self._is_shotgrid_review_upload_enabled():
            return UPLOAD_TARGET_REVIEW
        return UPLOAD_TARGET_VERSION_ONLY

    @property
    def shotgrid_review_playlist_id(self) -> int | None:
        if self.shotgrid_upload_target != UPLOAD_TARGET_REVIEW:
            return None
        return self._selected_shotgrid_review_playlist_id()

    @property
    def shotgrid_review_load_error(self) -> str | None:
        return self._shotgrid_review_load_error

    @property
    def shotgrid_description(self) -> str:
        return self._shotgrid_description_field.text().strip()

    @property
    def custom_frame_range(self) -> tuple[int, int]:
        return (self._custom_in.value(), self._custom_out.value())

    @property
    def custom_camera_path(self) -> str | None:
        camera_data = self._custom_camera.currentData()
        camera_token = str(camera_data or "").strip()
        if not camera_token or camera_token == self.CURRENT_VIEWPORT_CAMERA_TOKEN:
            return None
        return camera_token

    @property
    def custom_shot_code(self) -> str:
        scene_stem = self._scene_stem()
        if scene_stem:
            return scene_stem
        return "custom"

    def resolve_output_bases_by_destination(self) -> dict[str, Path]:
        selected_destination_dirs = self._resolved_destination_directories(
            include_unselected=False
        )
        if not selected_destination_dirs:
            return {}

        output_basename = self._resolved_output_basename(selected_destination_dirs)
        if not output_basename:
            return {}

        return {
            destination_name: destination_dir / output_basename
            for destination_name, destination_dir in selected_destination_dirs.items()
        }

    def _setup_ui(self) -> None:
        self._main_layout = QtWidgets.QVBoxLayout(self)
        self._build_header_section()
        self._build_export_setup_section()
        self._build_buttons()

    def _wire_ui_signals(self) -> None:
        self._source_tabs.currentChanged.connect(self._on_ui_input_changed)
        self._dept_combo.currentTextChanged.connect(self._on_ui_input_changed)

        self._shotgrid_upload_checkbox.toggled.connect(self._on_ui_input_changed)
        self._shotgrid_upload_version_checkbox.toggled.connect(
            self._on_ui_input_changed
        )
        self._shotgrid_upload_review_checkbox.toggled.connect(self._on_ui_input_changed)
        self._shotgrid_review_combo.currentIndexChanged.connect(
            self._on_ui_input_changed
        )
        self._shotgrid_review_refresh_button.clicked.connect(
            self._on_refresh_shotgrid_reviews_clicked
        )
        self._custom_folder_field.textChanged.connect(self._on_ui_input_changed)
        self._custom_camera.currentTextChanged.connect(self._on_ui_input_changed)
        self._custom_out.valueChanged.connect(self._on_ui_input_changed)

        for checkbox in self._destination_checkboxes.values():
            checkbox.toggled.connect(self._on_ui_input_changed)

        self._custom_in.valueChanged.connect(self._on_custom_in_changed)

    def _build_header_section(self) -> None:
        title_label = self._build_title_label()
        subtitle_label = self._build_subtitle_label()
        self._main_layout.addWidget(title_label)
        self._main_layout.addWidget(subtitle_label)

    @staticmethod
    def _build_title_label() -> QtWidgets.QLabel:
        title = QtWidgets.QLabel("Houdini Playblast")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setToolTip("Playblast export tool for Houdini viewport output.")
        return title

    @staticmethod
    def _build_subtitle_label() -> QtWidgets.QLabel:
        subtitle = QtWidgets.QLabel(
            "Choose source mode, choose destinations, then export"
        )
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setToolTip(
            "Workflow: choose Shot or Custom source, choose destinations, then export."
        )
        return subtitle

    def _build_export_setup_section(self) -> None:
        export_setup_group = QtWidgets.QGroupBox("1. Export Setup")
        export_setup_layout = QtWidgets.QVBoxLayout(export_setup_group)

        export_setup_layout.addWidget(self._build_export_source_section())
        export_setup_layout.addWidget(self._build_destination_section())
        export_setup_layout.addWidget(self._build_validation_label())

        self._main_layout.addWidget(export_setup_group)

    def _build_export_source_section(self) -> QtWidgets.QGroupBox:
        export_source_group = QtWidgets.QGroupBox("")
        export_source_layout = QtWidgets.QVBoxLayout(export_source_group)

        self._source_tabs = self._build_source_tabs()
        export_source_layout.addWidget(self._source_tabs)
        return export_source_group

    def _build_source_tabs(self) -> QtWidgets.QTabWidget:
        source_tabs = QtWidgets.QTabWidget()
        source_tabs.addTab(self._build_shot_source_tab(), "Shot Playblast")
        source_tabs.addTab(self._build_custom_source_tab(), "Custom Playblast")
        source_tabs.setToolTip(
            "Choose source mode: shot metadata from pipeline context or manual custom settings."
        )
        self._apply_source_tab_tooltips(source_tabs)
        return source_tabs

    def _apply_source_tab_tooltips(self, source_tabs: QtWidgets.QTabWidget) -> None:
        tab_bar = source_tabs.tabBar()
        tab_bar.setTabToolTip(
            self.SHOT_TAB_INDEX,
            "Uses detected shot context and ShotGrid cut range for this file.",
        )
        tab_bar.setTabToolTip(
            self.CUSTOM_TAB_INDEX,
            "Uses manual camera and frame range for non-shot testing or exploratory output.",
        )

    def _build_shot_source_tab(self) -> QtWidgets.QWidget:
        shot_tab = QtWidgets.QWidget()
        shot_layout = QtWidgets.QGridLayout(shot_tab)

        self._add_shot_source_mode_row(shot_layout)
        self._add_shot_code_row(shot_layout)
        self._add_shot_camera_row(shot_layout)
        self._add_shot_range_row(shot_layout)
        self._add_shotgrid_upload_row(shot_layout)
        self._add_shotgrid_upload_options_row(shot_layout)
        self._add_shotgrid_review_row(shot_layout)
        self._add_shotgrid_description_row(shot_layout)
        return shot_tab

    def _add_shot_source_mode_row(self, layout: QtWidgets.QGridLayout) -> None:
        layout.addWidget(QtWidgets.QLabel("Source"), 0, 0)
        source_value = QtWidgets.QLabel("Pipeline Shot Context")
        source_value.setToolTip("Shot mode uses shot context detected from the scene.")
        layout.addWidget(source_value, 0, 1)

    def _add_shot_code_row(self, layout: QtWidgets.QGridLayout) -> None:
        layout.addWidget(QtWidgets.QLabel("Shot"), 1, 0)
        self._shot_code_value = self._build_value_label("Detected shot code.")
        layout.addWidget(self._shot_code_value, 1, 1)

    def _add_shot_camera_row(self, layout: QtWidgets.QGridLayout) -> None:
        layout.addWidget(QtWidgets.QLabel("Camera"), 2, 0)
        self._shot_camera_value = self._build_value_label(
            "Viewport camera currently used by capture."
        )
        layout.addWidget(self._shot_camera_value, 2, 1)

    def _add_shot_range_row(self, layout: QtWidgets.QGridLayout) -> None:
        layout.addWidget(QtWidgets.QLabel("Frame Range"), 3, 0)
        self._shot_range_value = self._build_value_label(
            "ShotGrid cut range for the detected shot."
        )
        layout.addWidget(self._shot_range_value, 3, 1)

    def _add_shotgrid_upload_row(self, layout: QtWidgets.QGridLayout) -> None:
        layout.addWidget(QtWidgets.QLabel("ShotGrid"), 4, 0)
        self._shotgrid_upload_checkbox = QtWidgets.QCheckBox("Upload to ShotGrid")
        self._shotgrid_upload_checkbox.setToolTip(
            "When enabled, this shot playblast will also upload to ShotGrid."
        )
        layout.addWidget(self._shotgrid_upload_checkbox, 4, 1)

    def _add_shotgrid_upload_options_row(self, layout: QtWidgets.QGridLayout) -> None:
        self._shotgrid_upload_target_row = QtWidgets.QWidget()
        options_layout = QtWidgets.QHBoxLayout(self._shotgrid_upload_target_row)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.addWidget(QtWidgets.QLabel("Upload Options"))

        self._shotgrid_upload_version_checkbox = QtWidgets.QCheckBox(
            "Upload as new shot version"
        )
        self._shotgrid_upload_version_checkbox.setChecked(True)
        self._shotgrid_upload_version_checkbox.setToolTip(
            "Create a new ShotGrid Version for this shot upload."
        )
        options_layout.addWidget(self._shotgrid_upload_version_checkbox)

        self._shotgrid_upload_review_checkbox = QtWidgets.QCheckBox(
            "Upload to review for dailies"
        )
        self._shotgrid_upload_review_checkbox.setChecked(False)
        self._shotgrid_upload_review_checkbox.setToolTip(
            "Also link the uploaded Version to a review playlist."
        )
        options_layout.addWidget(self._shotgrid_upload_review_checkbox)

        options_layout.addStretch()
        layout.addWidget(self._shotgrid_upload_target_row, 5, 0, 1, 2)

    def _add_shotgrid_review_row(self, layout: QtWidgets.QGridLayout) -> None:
        self._shotgrid_review_row = QtWidgets.QWidget()
        review_layout = QtWidgets.QHBoxLayout(self._shotgrid_review_row)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.addWidget(QtWidgets.QLabel("Review"))

        self._shotgrid_review_combo = QtWidgets.QComboBox()
        self._shotgrid_review_combo.setToolTip(
            "Select the ShotGrid review playlist to link this Version to."
        )
        review_layout.addWidget(self._shotgrid_review_combo)

        self._shotgrid_review_refresh_button = QtWidgets.QPushButton("Refresh")
        self._shotgrid_review_refresh_button.setToolTip(
            "Reload the recent ShotGrid review playlist options."
        )
        review_layout.addWidget(self._shotgrid_review_refresh_button)

        self._set_review_combo_placeholder("No reviews loaded yet.")
        layout.addWidget(self._shotgrid_review_row, 6, 0, 1, 2)

    def _add_shotgrid_description_row(self, layout: QtWidgets.QGridLayout) -> None:
        self._shotgrid_description_row = QtWidgets.QWidget()
        description_layout = QtWidgets.QHBoxLayout(self._shotgrid_description_row)
        description_layout.setContentsMargins(0, 0, 0, 0)
        description_layout.addWidget(QtWidgets.QLabel("Description"))

        self._shotgrid_description_field = QtWidgets.QLineEdit()
        self._shotgrid_description_field.setPlaceholderText(
            "Optional ShotGrid version description"
        )
        self._shotgrid_description_field.setToolTip(
            "Optional notes for the ShotGrid Version description."
        )
        description_layout.addWidget(self._shotgrid_description_field)
        layout.addWidget(self._shotgrid_description_row, 7, 0, 1, 2)

    @staticmethod
    def _build_value_label(tooltip: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel("-")
        label.setToolTip(tooltip)
        return label

    def _build_custom_source_tab(self) -> QtWidgets.QWidget:
        custom_tab = QtWidgets.QWidget()
        custom_layout = QtWidgets.QGridLayout(custom_tab)

        self._add_custom_source_mode_row(custom_layout)
        self._add_custom_frame_range_row(custom_layout)
        self._add_custom_camera_row(custom_layout)
        return custom_tab

    def _add_custom_source_mode_row(self, layout: QtWidgets.QGridLayout) -> None:
        layout.addWidget(QtWidgets.QLabel("Source"), 0, 0)
        source_value = QtWidgets.QLabel("Manual Custom Settings")
        source_value.setToolTip(
            "Custom mode is intended for testing and non-shot scene playblasts."
        )
        layout.addWidget(source_value, 0, 1, 1, 3)

    def _add_custom_frame_range_row(self, layout: QtWidgets.QGridLayout) -> None:
        timeline_in, timeline_out = self._timeline_range()
        self._custom_in = QtWidgets.QSpinBox(self, minimum=-100000, maximum=100000)
        self._custom_out = QtWidgets.QSpinBox(self, minimum=-100000, maximum=100000)
        self._custom_in.setValue(timeline_in)
        self._custom_out.setValue(timeline_out)
        self._custom_out.setMinimum(self._custom_in.value())
        self._custom_in.setToolTip("Custom start frame for this playblast.")
        self._custom_out.setToolTip("Custom end frame for this playblast.")

        layout.addWidget(QtWidgets.QLabel("Custom In"), 1, 0)
        layout.addWidget(self._custom_in, 1, 1)
        layout.addWidget(QtWidgets.QLabel("Custom Out"), 1, 2)
        layout.addWidget(self._custom_out, 1, 3)

    def _add_custom_camera_row(self, layout: QtWidgets.QGridLayout) -> None:
        layout.addWidget(QtWidgets.QLabel("Camera"), 2, 0)
        self._custom_camera = QtWidgets.QComboBox()
        self._populate_custom_camera_options()
        self._custom_camera.setToolTip("Camera used for custom mode playblast capture.")
        layout.addWidget(self._custom_camera, 2, 1, 1, 3)

    def _build_destination_section(self) -> QtWidgets.QGroupBox:
        destination_group = QtWidgets.QGroupBox("Save Destinations")
        destination_layout = QtWidgets.QVBoxLayout(destination_group)

        destination_layout.addWidget(self._build_edit_department_row())
        for destination_option in self._destination_options():
            destination_layout.addWidget(
                self._build_destination_option_row(destination_option)
            )

        self._align_destination_checkboxes()
        self._custom_folder_row = self._build_custom_folder_row()
        destination_layout.addWidget(self._custom_folder_row)
        return destination_group

    def _build_edit_department_row(self) -> QtWidgets.QWidget:
        department_row = QtWidgets.QWidget()
        department_layout = QtWidgets.QHBoxLayout(department_row)
        department_layout.setContentsMargins(0, 0, 0, 0)

        department_layout.addWidget(QtWidgets.QLabel("Edit Department"))
        self._dept_combo = QtWidgets.QComboBox()
        self._dept_combo.addItems(DEPARTMENTS)
        self._dept_combo.setToolTip(
            "Department subfolder used for Send to Edit output paths."
        )
        department_layout.addWidget(self._dept_combo)
        department_layout.addStretch()
        return department_row

    def _build_destination_option_row(
        self,
        destination_option: DestinationOption,
    ) -> QtWidgets.QWidget:
        row_widget = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        destination_toggle = QtWidgets.QCheckBox(destination_option.name)
        destination_toggle.setChecked(destination_option.name == self.DESTINATION_EDIT)
        destination_toggle.setToolTip(destination_option.tooltip)
        self._destination_checkboxes[destination_option.name] = destination_toggle
        row_layout.addWidget(destination_toggle)

        path_label = QtWidgets.QLabel("")
        path_label.setToolTip(f"Resolved output path for {destination_option.name}.")
        path_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._destination_path_labels[destination_option.name] = path_label
        row_layout.addWidget(path_label)
        row_layout.addStretch()
        return row_widget

    def _build_custom_folder_row(self) -> QtWidgets.QWidget:
        custom_folder_row = QtWidgets.QWidget()
        custom_folder_layout = QtWidgets.QHBoxLayout(custom_folder_row)
        custom_folder_layout.setContentsMargins(24, 0, 0, 0)

        custom_folder_layout.addWidget(QtWidgets.QLabel("Custom Folder Path"))
        self._custom_folder_field = QtWidgets.QLineEdit()
        self._custom_folder_field.setText(str(get_edit_path()))
        self._custom_folder_field.setToolTip(
            "Directory used when Custom Folder destination is enabled."
        )
        custom_folder_layout.addWidget(self._custom_folder_field)

        browse_button = QtWidgets.QPushButton("Browse")
        browse_button.setToolTip("Choose a custom output directory.")
        browse_button.clicked.connect(self._browse_custom_folder)
        custom_folder_layout.addWidget(browse_button)
        return custom_folder_row

    def _build_validation_label(self) -> QtWidgets.QLabel:
        self._validation_label = QtWidgets.QLabel()
        self._validation_label.setStyleSheet("color: #b00020;")
        self._validation_label.setToolTip(
            "Validation feedback. Export is disabled until this message is cleared."
        )
        self._validation_label.setVisible(False)
        return self._validation_label

    def _build_buttons(self) -> None:
        ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setToolTip("Run playblast with the current settings.")

        cancel_button = self.buttons.button(QtWidgets.QDialogButtonBox.Cancel)
        if cancel_button is not None:
            cancel_button.setToolTip("Close without exporting.")

        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._main_layout.addWidget(self.buttons)

    @staticmethod
    def _destination_options() -> tuple[DestinationOption, ...]:
        return (
            DestinationOption(
                HPlayblastDialog.DESTINATION_EDIT,
                "Export playblast movie to the edit dailies folder.",
            ),
            DestinationOption(
                HPlayblastDialog.DESTINATION_CURRENT,
                "Export playblast movie next to the current HIP scene file.",
            ),
            DestinationOption(
                HPlayblastDialog.DESTINATION_CUSTOM,
                "Export playblast movie to a manually selected folder.",
            ),
        )

    def _resolve_shot_context(self, shot_code: str) -> Shot | None:
        if not shot_code:
            return None
        try:
            return self._conn.get_shot_by_code(shot_code)
        except Exception:
            return None

    def _set_default_source_tab(self) -> None:
        has_shot_context = self._shot is not None
        self._source_tabs.setTabEnabled(self.SHOT_TAB_INDEX, has_shot_context)
        default_index = (
            self.SHOT_TAB_INDEX if has_shot_context else self.CUSTOM_TAB_INDEX
        )
        self._source_tabs.setCurrentIndex(default_index)

    def _align_destination_checkboxes(self) -> None:
        destination_column_width = max(
            (
                checkbox.sizeHint().width()
                for checkbox in self._destination_checkboxes.values()
            ),
            default=0,
        )
        for checkbox in self._destination_checkboxes.values():
            checkbox.setFixedWidth(destination_column_width)

    def _update_ui_state(self) -> None:
        self._refresh_shot_context_fields()
        self._sync_custom_folder_row_visibility()
        if (
            self._is_shotgrid_upload_requested()
            and self._is_shotgrid_review_upload_enabled()
        ):
            self._ensure_shotgrid_reviews_loaded_lazily()
        self._sync_shotgrid_upload_options_visibility()
        self._sync_shotgrid_review_visibility()
        self._sync_shotgrid_description_visibility()
        self._refresh_destination_path_labels()
        self._update_action_state()

    def _refresh_shot_context_fields(self) -> None:
        if self._shot is None:
            self._shot_code_value.setText(self._default_shot_code or "-")
            self._shot_range_value.setText("-")
        else:
            self._shot_code_value.setText(self._shot.code)
            self._shot_range_value.setText(
                f"{self._shot.cut_in} - {self._shot.cut_out}"
            )

        self._shot_camera_value.setText(self._current_viewport_camera_label())

    def _sync_custom_folder_row_visibility(self) -> None:
        show_custom_folder_row = self._is_destination_selected(self.DESTINATION_CUSTOM)
        self._custom_folder_row.setVisible(show_custom_folder_row)
        self._custom_folder_field.setEnabled(show_custom_folder_row)

    def _sync_shotgrid_description_visibility(self) -> None:
        show_description = (
            self.selected_source_mode == "shot" and self.upload_to_shotgrid
        )
        self._shotgrid_description_row.setVisible(show_description)
        self._shotgrid_description_field.setEnabled(show_description)

    def _sync_shotgrid_upload_options_visibility(self) -> None:
        show_options = self._is_shotgrid_upload_requested()
        self._shotgrid_upload_target_row.setVisible(show_options)
        self._shotgrid_upload_version_checkbox.setEnabled(show_options)
        self._shotgrid_upload_review_checkbox.setEnabled(show_options)

    def _sync_shotgrid_review_visibility(self) -> None:
        show_review = (
            self._is_shotgrid_upload_requested()
            and self._is_shotgrid_review_upload_enabled()
        )
        self._shotgrid_review_row.setVisible(show_review)
        self._shotgrid_review_combo.setEnabled(show_review)
        self._shotgrid_review_refresh_button.setEnabled(show_review)

    def _is_shotgrid_upload_requested(self) -> bool:
        return self.selected_source_mode == "shot" and self.upload_to_shotgrid

    def _is_shotgrid_version_upload_enabled(self) -> bool:
        return self._shotgrid_upload_version_checkbox.isChecked()

    def _is_shotgrid_review_upload_enabled(self) -> bool:
        return self._shotgrid_upload_review_checkbox.isChecked()

    def _selected_shotgrid_review_playlist_id(self) -> int | None:
        selected = self._shotgrid_review_combo.currentData()
        if isinstance(selected, int) and selected > 0:
            return selected
        return None

    def _set_review_combo_placeholder(self, label: str) -> None:
        previous_signal_state = self._shotgrid_review_combo.blockSignals(True)
        try:
            self._shotgrid_review_combo.clear()
            self._shotgrid_review_combo.addItem(label, None)
            self._shotgrid_review_combo.setCurrentIndex(0)
        finally:
            self._shotgrid_review_combo.blockSignals(previous_signal_state)

    def _ensure_shotgrid_reviews_loaded_lazily(self) -> None:
        if self._shotgrid_review_lazy_load_attempted:
            return
        self._load_shotgrid_reviews(force_refresh=False)

    def _load_shotgrid_reviews(self, *, force_refresh: bool) -> None:
        if self._shotgrid_review_lazy_load_attempted and not force_refresh:
            return
        self._shotgrid_review_lazy_load_attempted = True
        previous_playlist_id = self._selected_shotgrid_review_playlist_id()

        try:
            review_options = list_recent_review_playlists(conn=self._conn, limit=10)
        except Exception as exc:
            self._shotgrid_review_load_error = str(exc).strip() or type(exc).__name__
            log.exception(
                "Could not load ShotGrid review playlists for shot '%s'",
                self._shot_code_value.text().strip() or "<unknown>",
            )
            self._set_review_combo_placeholder("Could not load reviews. Click Refresh.")
            return

        self._shotgrid_review_load_error = None
        previous_signal_state = self._shotgrid_review_combo.blockSignals(True)
        try:
            self._shotgrid_review_combo.clear()

            if not review_options:
                self._shotgrid_review_combo.addItem("No recent reviews found.", None)
                self._shotgrid_review_combo.setCurrentIndex(0)
                return

            selected_index = 0
            for index, option in enumerate(review_options):
                label = f"{option.display_name} (#{option.playlist_id})"
                self._shotgrid_review_combo.addItem(label, option.playlist_id)
                if (
                    previous_playlist_id is not None
                    and option.playlist_id == previous_playlist_id
                ):
                    selected_index = index

            self._shotgrid_review_combo.setCurrentIndex(selected_index)
        finally:
            self._shotgrid_review_combo.blockSignals(previous_signal_state)

    def _refresh_destination_path_labels(self) -> None:
        preview_paths = self._preview_output_paths_by_destination()
        for destination_name, path_label in self._destination_path_labels.items():
            preview_path = preview_paths.get(destination_name, "")
            if preview_path:
                path_label.setText(f"-> {preview_path}")
                continue

            if self._is_missing_custom_path_preview(destination_name):
                path_label.setText("-> (select custom folder)")
                continue

            path_label.setText("->")

    def _is_missing_custom_path_preview(self, destination_name: str) -> bool:
        return (
            destination_name == self.DESTINATION_CUSTOM
            and self._is_destination_selected(self.DESTINATION_CUSTOM)
        )

    def _update_action_state(self) -> None:
        ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button is None:
            return

        ok_button.setText(self._action_button_text())
        validation_error = self._validate_target_destination_state()
        ok_button.setEnabled(validation_error is None)
        self._validation_label.setText(validation_error or "")
        self._validation_label.setVisible(validation_error is not None)

    def _validate_target_destination_state(self) -> str | None:
        source_error = self._validate_source_state()
        if source_error:
            return source_error

        destination_error = self._validate_destination_state()
        if destination_error:
            return destination_error

        output_prefix_error = self._validate_output_prefix_state()
        if output_prefix_error:
            return output_prefix_error

        shotgrid_upload_error = self._validate_shotgrid_upload_state()
        if shotgrid_upload_error:
            return shotgrid_upload_error

        return None

    def _validate_shotgrid_upload_state(self) -> str | None:
        if not self._is_shotgrid_upload_requested():
            return None

        if (
            not self._is_shotgrid_version_upload_enabled()
            and not self._is_shotgrid_review_upload_enabled()
        ):
            return (
                "Select at least one ShotGrid upload option: 'Upload as new shot "
                "version' or 'Upload to review for dailies'."
            )

        if (
            self._is_shotgrid_review_upload_enabled()
            and self._selected_shotgrid_review_playlist_id() is None
        ):
            if self._shotgrid_review_load_error:
                if self._is_shotgrid_version_upload_enabled():
                    return None
                return (
                    "Could not load ShotGrid reviews. Click Refresh, or disable "
                    "'Upload to review for dailies'."
                )
            return (
                "Select a ShotGrid review before exporting, or disable 'Upload to "
                "review for dailies'."
            )

        return None

    def _validate_source_state(self) -> str | None:
        if self.selected_source_mode == "shot":
            if self._shot is None:
                return (
                    "No shot context is available. Switch to Custom Playblast or open a "
                    "pipeline shot scene."
                )
            return None

        if self._custom_out.value() < self._custom_in.value():
            return "Custom Out must be greater than or equal to Custom In."
        if not str(self._custom_camera.currentText()).strip():
            return "Choose a camera for Custom Playblast."
        return None

    def _validate_destination_state(self) -> str | None:
        if not self._resolved_destination_directories(include_unselected=False):
            return "Select at least one save destination."

        if (
            self._is_destination_selected(self.DESTINATION_CUSTOM)
            and self._custom_directory() is None
        ):
            return "Custom Folder path is required when Custom Folder destination is enabled."
        return None

    def _validate_output_prefix_state(self) -> str | None:
        if self._output_prefix_for_selected_mode():
            return None
        return "Could not determine a valid output prefix for this playblast."

    def _action_button_text(self) -> str:
        if self.selected_source_mode == "shot":
            return "Playblast Shot"
        return "Playblast Custom"

    def _preview_output_paths_by_destination(self) -> dict[str, str]:
        selected_destination_dirs = self._resolved_destination_directories(
            include_unselected=False
        )
        if not selected_destination_dirs:
            return {}

        output_basename = self._resolved_output_basename(selected_destination_dirs)
        if not output_basename:
            return {name: str(path) for name, path in selected_destination_dirs.items()}

        return {
            destination_name: str(destination_path / output_basename)
            for destination_name, destination_path in selected_destination_dirs.items()
        }

    def _resolved_output_basename(
        self, destination_dirs: dict[str, Path]
    ) -> str | None:
        output_prefix = self._output_prefix_for_selected_mode()
        if not output_prefix or not destination_dirs:
            return None

        try:
            return resolve_versioned_playblast_basename(
                output_prefix,
                destination_dirs.values(),
            )
        except Exception:
            return None

    def _resolved_destination_directories(
        self,
        *,
        include_unselected: bool,
    ) -> dict[str, Path]:
        directories: dict[str, Path] = {}
        for destination_name in self.DESTINATION_ORDER:
            if not include_unselected and not self._is_destination_selected(
                destination_name
            ):
                continue

            destination_directory = self._resolved_destination_directory(
                destination_name
            )
            if destination_directory is None:
                continue
            directories[destination_name] = destination_directory

        return directories

    def _is_destination_selected(self, destination_name: str) -> bool:
        destination_checkbox = self._destination_checkboxes.get(destination_name)
        return bool(destination_checkbox and destination_checkbox.isChecked())

    def _resolved_destination_directory(self, destination_name: str) -> Path | None:
        if destination_name == self.DESTINATION_EDIT:
            return build_edit_output_directory(self.department)
        if destination_name == self.DESTINATION_CURRENT:
            return self._current_scene_directory()
        if destination_name == self.DESTINATION_CUSTOM:
            return self._custom_directory()
        return None

    def _output_prefix_for_selected_mode(self) -> str:
        if self.selected_source_mode == "shot":
            return self.shot_code

        scene_stem = self._scene_stem()
        if scene_stem:
            return f"customPB_{scene_stem}"
        return "customPB"

    def _current_scene_directory(self) -> Path:
        try:
            return Path(hou.hipFile.path()).expanduser().resolve().parent
        except Exception:
            return Path.cwd()

    def _custom_directory(self) -> Path | None:
        custom_path_text = self._custom_folder_field.text().strip()
        if not custom_path_text:
            return None
        return Path(custom_path_text).expanduser()

    def _browse_custom_folder(self) -> None:
        start_directory = str(self._custom_directory() or get_edit_path())
        selected_directory = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select Custom Playblast Folder",
            start_directory,
        )
        if selected_directory:
            self._custom_folder_field.setText(selected_directory)

    def _populate_custom_camera_options(self) -> None:
        self._custom_camera.clear()
        self._custom_camera.addItem(
            "Current Viewport Camera",
            self.CURRENT_VIEWPORT_CAMERA_TOKEN,
        )

        for camera_path in self._available_camera_paths():
            self._custom_camera.addItem(camera_path, camera_path)

    @staticmethod
    def _available_camera_paths() -> list[str]:
        object_context = hou.node("/obj")
        if object_context is None:
            return []

        camera_paths: list[str] = []
        object_nodes = [object_context, *object_context.allSubChildren()]
        for node in object_nodes:
            try:
                if node.type().category() != hou.objNodeTypeCategory():
                    continue
                if node.type().name() not in {"cam", "camera"}:
                    continue
            except Exception:
                continue
            camera_paths.append(node.path())

        return sorted(set(camera_paths))

    @staticmethod
    def _timeline_range() -> tuple[int, int]:
        try:
            range_start, range_end = hou.playbar.playbackRange()
            start = int(round(range_start))
            end = int(round(range_end))
        except Exception:
            current_frame = int(round(hou.frame()))
            start = current_frame
            end = current_frame

        if end < start:
            end = start
        return start, end

    @staticmethod
    def _scene_stem() -> str:
        try:
            return Path(hou.hipFile.path()).stem.strip()
        except Exception:
            return ""

    @staticmethod
    def _current_viewport_camera_label() -> str:
        try:
            scene_viewer: Any = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if scene_viewer is None:
                return "Current Viewport Camera"

            viewport = scene_viewer.curViewport()
            if viewport is None:
                return "Current Viewport Camera"

            camera_node = viewport.camera()
            if camera_node is None:
                return "Current Viewport Camera"
            return camera_node.path()
        except Exception:
            return "Current Viewport Camera"

    def _on_custom_in_changed(self, in_frame: int) -> None:
        self._custom_out.setMinimum(in_frame)
        self._update_ui_state()

    def _on_ui_input_changed(self, *_args) -> None:
        self._update_ui_state()

    def _on_refresh_shotgrid_reviews_clicked(self) -> None:
        self._load_shotgrid_reviews(force_refresh=True)
        self._update_ui_state()
