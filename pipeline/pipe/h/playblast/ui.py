from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import hou
from Qt import QtCore, QtWidgets
from shared.util import get_edit_path

from pipe.glui.dialogs import DialogButtons
from pipe.playblast_naming import resolve_versioned_playblast_basename

from .paths import build_edit_output_directory

if TYPE_CHECKING:
    from pipe.db import DB
    from pipe.struct.db import Shot


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
    _shotgrid_upload_checkbox: QtWidgets.QCheckBox
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

        self._init_buttons(True, "Playblast Shot", "Cancel")
        self.setWindowTitle("Houdini Playblast")

        self._setup_ui()
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
        return (
            "shot"
            if self._source_tabs.currentIndex() == self.SHOT_TAB_INDEX
            else "custom"
        )

    @property
    def upload_to_shotgrid(self) -> bool:
        return self._shotgrid_upload_checkbox.isChecked()

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

    def resolve_output_base_paths(self) -> tuple[Path | None, Path | None]:
        """Backward-compatible output helper used by the current launch tool."""
        output_by_destination = self.resolve_output_bases_by_destination()
        if not output_by_destination:
            return None, None

        primary_output = output_by_destination.get(self.DESTINATION_EDIT)
        if primary_output is None:
            primary_output = next(iter(output_by_destination.values()))

        custom_output = output_by_destination.get(self.DESTINATION_CUSTOM)
        return primary_output, custom_output

    @property
    def output_base_path(self) -> Path | None:
        output_base, _ = self.resolve_output_base_paths()
        return output_base

    @property
    def custom_output_base_path(self) -> Path | None:
        _, custom_output = self.resolve_output_base_paths()
        return custom_output

    def resolve_output_bases_by_destination(self) -> dict[str, Path]:
        selected_dirs = self._selected_destination_directories_by_name()
        if not selected_dirs:
            return {}

        output_prefix = self._output_prefix_for_selected_mode()
        if not output_prefix:
            return {}

        output_basename = resolve_versioned_playblast_basename(
            output_prefix,
            selected_dirs.values(),
        )
        return {
            destination_name: destination_dir / output_basename
            for destination_name, destination_dir in selected_dirs.items()
        }

    def resolve_selected_output_bases(self) -> list[Path]:
        output_by_destination = self.resolve_output_bases_by_destination()
        return [
            output_by_destination[destination_name]
            for destination_name in self.DESTINATION_ORDER
            if destination_name in output_by_destination
        ]

    def _setup_ui(self) -> None:
        self._main_layout = QtWidgets.QVBoxLayout(self)
        self._build_header_section()
        self._build_export_setup_section()
        self._build_buttons()

    def _build_header_section(self) -> None:
        title = QtWidgets.QLabel("Houdini Playblast")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setToolTip("Playblast export tool for Houdini viewport output.")

        subtitle = QtWidgets.QLabel(
            "Choose source mode, choose destinations, then export"
        )
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setToolTip(
            "Workflow: choose Shot or Custom source, choose destinations, then export."
        )

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_export_setup_section(self) -> None:
        setup_group = QtWidgets.QGroupBox("1. Export Setup")
        setup_layout = QtWidgets.QVBoxLayout(setup_group)

        setup_layout.addWidget(self._build_export_source_section())
        setup_layout.addWidget(self._build_destination_section())

        self._validation_label = QtWidgets.QLabel()
        self._validation_label.setStyleSheet("color: #b00020;")
        self._validation_label.setToolTip(
            "Validation feedback. Export is disabled until this message is cleared."
        )
        self._validation_label.setVisible(False)
        setup_layout.addWidget(self._validation_label)

        self._main_layout.addWidget(setup_group)

    def _build_export_source_section(self) -> QtWidgets.QGroupBox:
        source_group = QtWidgets.QGroupBox("")
        source_layout = QtWidgets.QVBoxLayout(source_group)

        self._source_tabs = QtWidgets.QTabWidget()
        self._source_tabs.addTab(self._build_shot_source_tab(), "Shot Playblast")
        self._source_tabs.addTab(self._build_custom_source_tab(), "Custom Playblast")
        self._source_tabs.currentChanged.connect(self._on_source_mode_changed)
        self._source_tabs.setToolTip(
            "Choose source mode: shot metadata from pipeline context or manual custom settings."
        )

        tab_bar = self._source_tabs.tabBar()
        tab_bar.setTabToolTip(
            self.SHOT_TAB_INDEX,
            "Uses detected shot context and ShotGrid cut range for this file.",
        )
        tab_bar.setTabToolTip(
            self.CUSTOM_TAB_INDEX,
            "Uses manual camera and frame range for non-shot testing or exploratory output.",
        )

        source_layout.addWidget(self._source_tabs)
        return source_group

    def _build_shot_source_tab(self) -> QtWidgets.QWidget:
        shot_tab = QtWidgets.QWidget()
        shot_layout = QtWidgets.QGridLayout(shot_tab)

        shot_layout.addWidget(QtWidgets.QLabel("Source"), 0, 0)
        shot_source_value = QtWidgets.QLabel("Pipeline Shot Context")
        shot_source_value.setToolTip(
            "Shot mode uses shot context detected from the Houdini scene."
        )
        shot_layout.addWidget(shot_source_value, 0, 1)

        shot_layout.addWidget(QtWidgets.QLabel("Shot"), 1, 0)
        self._shot_code_value = QtWidgets.QLabel("-")
        self._shot_code_value.setToolTip("Detected shot code.")
        shot_layout.addWidget(self._shot_code_value, 1, 1)

        shot_layout.addWidget(QtWidgets.QLabel("Camera"), 2, 0)
        self._shot_camera_value = QtWidgets.QLabel("-")
        self._shot_camera_value.setToolTip(
            "Viewport camera currently used by the Houdini playblast capture."
        )
        shot_layout.addWidget(self._shot_camera_value, 2, 1)

        shot_layout.addWidget(QtWidgets.QLabel("Frame Range"), 3, 0)
        self._shot_range_value = QtWidgets.QLabel("-")
        self._shot_range_value.setToolTip("ShotGrid cut range for the detected shot.")
        shot_layout.addWidget(self._shot_range_value, 3, 1)

        shot_layout.addWidget(QtWidgets.QLabel("ShotGrid"), 4, 0)
        self._shotgrid_upload_checkbox = QtWidgets.QCheckBox("Upload to ShotGrid")
        self._shotgrid_upload_checkbox.setToolTip(
            "When enabled, this shot playblast will also upload to ShotGrid."
        )
        self._shotgrid_upload_checkbox.toggled.connect(self._on_shotgrid_upload_toggled)
        shot_layout.addWidget(self._shotgrid_upload_checkbox, 4, 1)

        self._shotgrid_description_row = QtWidgets.QWidget()
        shotgrid_description_layout = QtWidgets.QHBoxLayout(
            self._shotgrid_description_row
        )
        shotgrid_description_layout.setContentsMargins(0, 0, 0, 0)
        shotgrid_description_layout.addWidget(QtWidgets.QLabel("Description"))
        self._shotgrid_description_field = QtWidgets.QLineEdit()
        self._shotgrid_description_field.setPlaceholderText(
            "Optional ShotGrid version description"
        )
        self._shotgrid_description_field.setToolTip(
            "Optional notes for the ShotGrid Version description."
        )
        shotgrid_description_layout.addWidget(self._shotgrid_description_field)
        shot_layout.addWidget(self._shotgrid_description_row, 5, 0, 1, 2)
        self._sync_shotgrid_description_visibility()
        return shot_tab

    def _build_custom_source_tab(self) -> QtWidgets.QWidget:
        custom_tab = QtWidgets.QWidget()
        custom_layout = QtWidgets.QGridLayout(custom_tab)

        custom_layout.addWidget(QtWidgets.QLabel("Source"), 0, 0)
        custom_source_value = QtWidgets.QLabel("Manual Custom Settings")
        custom_source_value.setToolTip(
            "Custom mode is intended for testing and non-shot scene playblasts."
        )
        custom_layout.addWidget(custom_source_value, 0, 1, 1, 3)

        timeline_in, timeline_out = self._timeline_range()
        self._custom_in = QtWidgets.QSpinBox(self, minimum=-100000, maximum=100000)
        self._custom_out = QtWidgets.QSpinBox(self, minimum=-100000, maximum=100000)
        self._custom_in.setValue(timeline_in)
        self._custom_out.setValue(timeline_out)
        self._custom_out.setMinimum(self._custom_in.value())
        self._custom_in.setToolTip("Custom start frame for this playblast.")
        self._custom_out.setToolTip("Custom end frame for this playblast.")
        self._custom_in.valueChanged.connect(self._on_custom_in_changed)
        self._custom_out.valueChanged.connect(self._on_custom_settings_changed)

        custom_layout.addWidget(QtWidgets.QLabel("Custom In"), 1, 0)
        custom_layout.addWidget(self._custom_in, 1, 1)
        custom_layout.addWidget(QtWidgets.QLabel("Custom Out"), 1, 2)
        custom_layout.addWidget(self._custom_out, 1, 3)

        custom_layout.addWidget(QtWidgets.QLabel("Camera"), 2, 0)
        self._custom_camera = QtWidgets.QComboBox()
        self._populate_custom_camera_options()
        self._custom_camera.setToolTip("Camera used for custom mode playblast capture.")
        self._custom_camera.currentTextChanged.connect(self._on_custom_settings_changed)
        custom_layout.addWidget(self._custom_camera, 2, 1, 1, 3)

        return custom_tab

    def _build_destination_section(self) -> QtWidgets.QGroupBox:
        destination_group = QtWidgets.QGroupBox("Save Destinations")
        destination_layout = QtWidgets.QVBoxLayout(destination_group)

        department_row = QtWidgets.QWidget()
        department_row_layout = QtWidgets.QHBoxLayout(department_row)
        department_row_layout.setContentsMargins(0, 0, 0, 0)
        department_row_layout.addWidget(QtWidgets.QLabel("Edit Department"))
        self._dept_combo = QtWidgets.QComboBox()
        self._dept_combo.addItems(DEPARTMENTS)
        self._dept_combo.setToolTip(
            "Department subfolder used for Send to Edit output paths."
        )
        self._dept_combo.currentTextChanged.connect(self._on_department_changed)
        department_row_layout.addWidget(self._dept_combo)
        department_row_layout.addStretch()
        destination_layout.addWidget(department_row)

        for destination in self._destination_options():
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            destination_toggle = QtWidgets.QCheckBox(destination.name)
            destination_toggle.setChecked(destination.name == self.DESTINATION_EDIT)
            destination_toggle.setToolTip(destination.tooltip)
            destination_toggle.toggled.connect(self._on_destination_toggled)
            self._destination_checkboxes[destination.name] = destination_toggle
            row_layout.addWidget(destination_toggle)

            destination_path_label = QtWidgets.QLabel("")
            destination_path_label.setToolTip(
                f"Resolved output path for {destination.name}."
            )
            destination_path_label.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse
            )
            self._destination_path_labels[destination.name] = destination_path_label
            row_layout.addWidget(destination_path_label)
            row_layout.addStretch()
            destination_layout.addWidget(row_widget)

        self._align_destination_checkboxes()
        self._custom_folder_row = self._build_custom_folder_row()
        destination_layout.addWidget(self._custom_folder_row)
        return destination_group

    def _build_custom_folder_row(self) -> QtWidgets.QWidget:
        custom_path_row = QtWidgets.QWidget()
        custom_path_layout = QtWidgets.QHBoxLayout(custom_path_row)
        custom_path_layout.setContentsMargins(24, 0, 0, 0)

        custom_path_layout.addWidget(QtWidgets.QLabel("Custom Folder Path"))
        self._custom_folder_field = QtWidgets.QLineEdit()
        self._custom_folder_field.setText(str(get_edit_path()))
        self._custom_folder_field.setToolTip(
            "Directory used when Custom Folder destination is enabled."
        )
        self._custom_folder_field.textChanged.connect(self._on_custom_path_changed)
        custom_path_layout.addWidget(self._custom_folder_field)

        browse_button = QtWidgets.QPushButton("Browse")
        browse_button.setToolTip("Choose a custom output directory.")
        browse_button.clicked.connect(self._browse_custom_folder)
        custom_path_layout.addWidget(browse_button)
        return custom_path_row

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
        self._source_tabs.setCurrentIndex(
            self.SHOT_TAB_INDEX if has_shot_context else self.CUSTOM_TAB_INDEX
        )

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
        is_custom_destination_selected = self._is_destination_selected(
            self.DESTINATION_CUSTOM
        )
        self._custom_folder_row.setVisible(is_custom_destination_selected)
        self._custom_folder_field.setEnabled(is_custom_destination_selected)

    def _sync_shotgrid_description_visibility(self) -> None:
        show_description = (
            self.selected_source_mode == "shot" and self.upload_to_shotgrid
        )
        self._shotgrid_description_row.setVisible(show_description)
        self._shotgrid_description_field.setEnabled(show_description)

    def _refresh_destination_path_labels(self) -> None:
        preview_paths = self._preview_output_bases_by_destination()
        for destination_name, path_label in self._destination_path_labels.items():
            preview_path = preview_paths.get(destination_name, "")
            if preview_path:
                path_label.setText(f"-> {preview_path}")
            elif (
                destination_name == self.DESTINATION_CUSTOM
                and self._is_destination_selected(self.DESTINATION_CUSTOM)
            ):
                path_label.setText("-> (select custom folder)")
            else:
                path_label.setText("->")

    def _update_action_state(self) -> None:
        ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button is None:
            return

        ok_button.setText(self._action_button_text())
        validation_error = self._validate_state()
        ok_button.setEnabled(validation_error is None)
        self._validation_label.setText(validation_error or "")
        self._validation_label.setVisible(validation_error is not None)

    def _validate_state(self) -> str | None:
        mode = self.selected_source_mode

        if mode == "shot":
            if self._shot is None:
                return (
                    "No shot context is available. Switch to Custom Playblast or open a "
                    "pipeline shot scene."
                )

        if mode == "custom":
            if self._custom_out.value() < self._custom_in.value():
                return "Custom Out must be greater than or equal to Custom In."

            if not str(self._custom_camera.currentText()).strip():
                return "Choose a camera for Custom Playblast."

        if not self._selected_destination_directories_by_name():
            return "Select at least one save destination."

        if (
            self._is_destination_selected(self.DESTINATION_CUSTOM)
            and self._custom_directory() is None
        ):
            return "Custom Folder path is required when Custom Folder destination is enabled."

        output_prefix = self._output_prefix_for_selected_mode()
        if not output_prefix:
            return "Could not determine a valid output prefix for this playblast."

        return None

    def _action_button_text(self) -> str:
        if self.selected_source_mode == "shot":
            return "Playblast Shot"
        return "Playblast Custom"

    def _output_prefix_for_selected_mode(self) -> str:
        if self.selected_source_mode == "shot":
            return self.shot_code

        scene_stem = self._scene_stem()
        if scene_stem:
            return f"customPB_{scene_stem}"
        return "customPB"

    def _preview_output_bases_by_destination(self) -> dict[str, str]:
        output_prefix = self._output_prefix_for_selected_mode()
        if not output_prefix:
            return {}

        destination_dirs = self._destination_directories_for_preview()
        if not destination_dirs:
            return {}

        try:
            output_basename = resolve_versioned_playblast_basename(
                output_prefix,
                destination_dirs.values(),
            )
        except Exception:
            return {name: str(path) for name, path in destination_dirs.items()}

        return {
            destination_name: str(destination_path / output_basename)
            for destination_name, destination_path in destination_dirs.items()
        }

    def _destination_directories_for_preview(self) -> dict[str, Path]:
        directories: dict[str, Path] = {}
        for destination_name in self.DESTINATION_ORDER:
            destination_directory = self._resolved_destination_directory(
                destination_name
            )
            if destination_directory is None:
                continue
            directories[destination_name] = destination_directory
        return directories

    def _selected_destination_directories_by_name(self) -> dict[str, Path]:
        directories: dict[str, Path] = {}
        for destination_name in self.DESTINATION_ORDER:
            if not self._is_destination_selected(destination_name):
                continue
            destination_directory = self._resolved_destination_directory(
                destination_name
            )
            if destination_directory is None:
                continue
            directories[destination_name] = destination_directory
        return directories

    def _is_destination_selected(self, destination_name: str) -> bool:
        checkbox = self._destination_checkboxes.get(destination_name)
        return bool(checkbox and checkbox.isChecked())

    def _resolved_destination_directory(self, destination_name: str) -> Path | None:
        if destination_name == self.DESTINATION_EDIT:
            return build_edit_output_directory(self.department)

        if destination_name == self.DESTINATION_CURRENT:
            return self._current_scene_directory()

        if destination_name == self.DESTINATION_CUSTOM:
            return self._custom_directory()

        return None

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

        cameras: list[str] = []
        object_nodes = [object_context, *object_context.allSubChildren()]
        for node in object_nodes:
            try:
                if node.type().category() != hou.objNodeTypeCategory():
                    continue
                if node.type().name() not in {"cam", "camera"}:
                    continue
            except Exception:
                continue
            cameras.append(node.path())

        return sorted(set(cameras))

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
            scene_viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
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

    def _on_source_mode_changed(self, _index: int) -> None:
        self._update_ui_state()

    def _on_destination_toggled(self, _checked: bool) -> None:
        self._update_ui_state()

    def _on_custom_path_changed(self, _path: str) -> None:
        self._update_ui_state()

    def _on_custom_in_changed(self, in_frame: int) -> None:
        self._custom_out.setMinimum(in_frame)
        self._update_ui_state()

    def _on_custom_settings_changed(self, *_args) -> None:
        self._update_ui_state()

    def _on_department_changed(self, _department: str) -> None:
        self._update_ui_state()

    def _on_shotgrid_upload_toggled(self, _enabled: bool) -> None:
        self._update_ui_state()
