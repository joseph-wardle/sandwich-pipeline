from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import maya.cmds as mc
from Qt import QtCore, QtWidgets
from Qt.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.glui.dialogs import ButtonPair, MessageDialog
from dcc.maya.assetfile import read_asset_metadata
from dcc.maya.playblast.shot.config import SaveLocation
from dcc.maya.playblast.turnaround.config import (
    DEFAULT_FRAMES_PER_PASS,
    TurnaroundPlayblastConfig,
    resolve_turnaround_review_roots,
)
from dcc.maya.playblast.turnaround.playblaster import MTurnaroundPlayblaster
from core.playblast import FFmpegPreset
from core.playblast.naming import next_versioned_basename
from core.playblast.tempdir import resolve_playblast_tempdir
from core.playblast.shotgrid import (
    PlayblastEntity,
    PlayblastUploadIntent,
    run_playblast_upload,
)
from core.playblast.ui import ReviewPlaylistCombo
from core.shotgrid import normalize_display_name
from core.users import resolve_artist_display_name

log = logging.getLogger(__name__)


def _scene_path() -> Path | None:
    raw_path = mc.file(query=True, sceneName=True)
    if not isinstance(raw_path, str) or not raw_path:
        return None
    return Path(raw_path)


def _scene_render_directory() -> str | Path:
    scene_path = _scene_path()
    if scene_path is None:
        return ""
    return scene_path.parent / "render"


class AssetTurnaroundDialog(ButtonPair, QtWidgets.QMainWindow):
    """Small Maya UI for asset review turnarounds."""

    playblaster = MTurnaroundPlayblaster()

    class SAVE_LOCS:
        CURRENT = SaveLocation(
            "Render Folder",
            _scene_render_directory,
            FFmpegPreset.WEB,
        )
        CUSTOM = SaveLocation("Custom Folder", "", FFmpegPreset.WEB)

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SKD Asset Turnaround")
        self._asset_metadata = self._read_asset_metadata()
        self._review_roots = resolve_turnaround_review_roots()
        self._destination_checkboxes: dict[str, QCheckBox] = {}
        self._destination_path_labels: dict[str, QLabel] = {}
        self._save_locations_by_name = {
            location.name: location for location in self._destination_locations()
        }

        self._setup_ui()
        self.SAVE_LOCS.CUSTOM._path = lambda: self._custom_folder_field.text().strip()
        self._update_ui_state()

    def _setup_ui(self) -> None:
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)

        self._main_layout = QVBoxLayout()
        self._central_widget.setLayout(self._main_layout)

        self._build_header_section()
        self._build_targets_section()
        self._build_viewport_options_section()
        self._build_buttons()

    def _build_header_section(self) -> None:
        title = QLabel(self.windowTitle())
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)

        subtitle = QLabel("Capture one shaded + wireframe turnaround review movie")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_targets_section(self) -> None:
        setup_group = QGroupBox("1. Export Setup")
        setup_layout = QVBoxLayout(setup_group)

        setup_layout.addWidget(self._build_source_section())
        setup_layout.addWidget(self._build_destination_section())

        self._validation_label = QLabel()
        self._validation_label.setStyleSheet("color: #b00020;")
        self._validation_label.setVisible(False)
        setup_layout.addWidget(self._validation_label)

        self._main_layout.addWidget(setup_group)

    def _build_source_section(self) -> QGroupBox:
        source_group = QGroupBox("")
        source_layout = QGridLayout(source_group)

        source_layout.addWidget(QLabel("Source"), 0, 0)
        source_value = QLabel("Pipeline Asset File")
        source_value.setToolTip(
            "Uses the current Maya asset scene and current selection."
        )
        source_layout.addWidget(source_value, 0, 1, 1, 2)

        source_layout.addWidget(QLabel("Asset"), 1, 0)
        self._asset_value = QLabel("-")
        self._asset_value.setToolTip("Resolved asset display name from scene metadata.")
        source_layout.addWidget(self._asset_value, 1, 1, 1, 2)

        source_layout.addWidget(QLabel("Review Root"), 2, 0)
        self._review_root_value = QLabel("-")
        self._review_root_value.setToolTip(
            "Uses the current selection when available, otherwise falls back to visible geometry."
        )
        source_layout.addWidget(self._review_root_value, 2, 1)

        self._refresh_selection_button = QPushButton("Refresh Selection")
        self._refresh_selection_button.setToolTip(
            "Refresh the turnaround review roots from the current Maya selection."
        )
        self._refresh_selection_button.clicked.connect(
            self._on_refresh_selection_clicked
        )
        source_layout.addWidget(self._refresh_selection_button, 2, 2)

        source_layout.addWidget(QLabel("Passes"), 3, 0)
        self._passes_value = QLabel("-")
        self._passes_value.setToolTip(
            "Summary of the shaded and wireframe pass lengths."
        )
        source_layout.addWidget(self._passes_value, 3, 1, 1, 2)

        source_layout.addWidget(QLabel("ShotGrid"), 4, 0)
        self._shotgrid_upload_checkbox = QCheckBox("Upload to ShotGrid")
        self._shotgrid_upload_checkbox.setChecked(False)
        self._shotgrid_upload_checkbox.setToolTip(
            "Create a new Asset Version in ShotGrid and upload the turnaround movie."
        )
        self._shotgrid_upload_checkbox.toggled.connect(self._on_settings_changed)
        source_layout.addWidget(self._shotgrid_upload_checkbox, 4, 1, 1, 2)

        self._shotgrid_upload_target_row = self._build_shotgrid_upload_target_row()
        source_layout.addWidget(self._shotgrid_upload_target_row, 5, 0, 1, 3)

        self._build_shotgrid_review_row()
        source_layout.addWidget(self._shotgrid_review_combo, 6, 0, 1, 3)

        self._shotgrid_description_row = QWidget()
        shotgrid_description_layout = QHBoxLayout(self._shotgrid_description_row)
        shotgrid_description_layout.setContentsMargins(0, 0, 0, 0)
        shotgrid_description_layout.addWidget(QLabel("Description"))
        self._shotgrid_description_field = QLineEdit()
        self._shotgrid_description_field.setPlaceholderText(
            "Optional ShotGrid version description"
        )
        self._shotgrid_description_field.textChanged.connect(self._on_settings_changed)
        shotgrid_description_layout.addWidget(self._shotgrid_description_field)
        source_layout.addWidget(self._shotgrid_description_row, 7, 0, 1, 3)

        return source_group

    def _build_shotgrid_upload_target_row(self) -> QWidget:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("Upload Options"))

        self._shotgrid_upload_version_checkbox = QCheckBox(
            "Upload as new asset version"
        )
        self._shotgrid_upload_version_checkbox.setChecked(True)
        self._shotgrid_upload_version_checkbox.toggled.connect(
            self._on_settings_changed
        )
        row_layout.addWidget(self._shotgrid_upload_version_checkbox)

        self._shotgrid_upload_review_checkbox = QCheckBox(
            "Upload to review for dailies"
        )
        self._shotgrid_upload_review_checkbox.setChecked(False)
        self._shotgrid_upload_review_checkbox.toggled.connect(self._on_settings_changed)
        row_layout.addWidget(self._shotgrid_upload_review_checkbox)

        row_layout.addStretch()
        return row_widget

    def _build_shotgrid_review_row(self) -> None:
        self._shotgrid_review_combo = ReviewPlaylistCombo(
            self, log_context=self._asset_display_name() or "<unknown>"
        )
        self._shotgrid_review_combo.selection_changed.connect(self._on_settings_changed)

    def _build_destination_section(self) -> QGroupBox:
        destination_group = QGroupBox("Save Destinations")
        destination_layout = QVBoxLayout(destination_group)

        for location in self._destination_locations():
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            toggle = QCheckBox(location.name)
            toggle.setChecked(self._default_destination_enabled(location))
            toggle.toggled.connect(self._on_settings_changed)
            self._destination_checkboxes[location.name] = toggle
            row_layout.addWidget(toggle)

            path_label = QLabel("")
            self._destination_path_labels[location.name] = path_label
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
        self._custom_folder_field.textChanged.connect(self._on_settings_changed)
        custom_path_layout.addWidget(self._custom_folder_field)

        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self._set_custom_folder)
        custom_path_layout.addWidget(browse_button)
        return custom_path_row

    def _build_viewport_options_section(self) -> None:
        options_group = QGroupBox("2. Viewport Options")
        options_layout = QHBoxLayout(options_group)

        self._use_default_material = self._build_option_checkbox(
            "Use Default Material",
            True,
            "Use Maya's default material instead of scene shaders for a cleaner model review.",
        )
        options_layout.addWidget(self._use_default_material)

        self._use_shadows = self._build_option_checkbox(
            "Use Shadows",
            True,
            "Render Viewport 2.0 shadows in the shaded pass.",
        )
        options_layout.addWidget(self._use_shadows)

        self._use_anti_aliasing = self._build_option_checkbox(
            "Use Anti-aliasing",
            True,
            "Enable Viewport 2.0 multi-sample and SSAO settings.",
        )
        options_layout.addWidget(self._use_anti_aliasing)

        self._include_wireframe_pass = self._build_option_checkbox(
            "Include Wireframe Pass",
            True,
            "Append a second full turntable pass with wireframe-on-shaded enabled.",
        )
        options_layout.addWidget(self._include_wireframe_pass)

        self._main_layout.addWidget(options_group)

    def _build_option_checkbox(
        self,
        label: str,
        checked: bool,
        tooltip: str,
    ) -> QCheckBox:
        checkbox = QCheckBox(label)
        checkbox.setChecked(checked)
        checkbox.setToolTip(tooltip)
        checkbox.toggled.connect(self._on_settings_changed)
        return checkbox

    def _build_buttons(self) -> None:
        self._init_buttons(has_cancel_button=True, ok_name="Create Turnaround")
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.do_export)
        self._main_layout.addWidget(self.buttons)

    @staticmethod
    def _default_custom_folder_path() -> str:
        return str(resolve_playblast_tempdir())

    def _destination_locations(self) -> list[SaveLocation]:
        return [self.SAVE_LOCS.CURRENT, self.SAVE_LOCS.CUSTOM]

    def _default_destination_enabled(self, location: SaveLocation) -> bool:
        return location.name == self.SAVE_LOCS.CURRENT.name

    def _selected_destination_locations(self) -> list[SaveLocation]:
        selected: list[SaveLocation] = []
        for location in self._destination_locations():
            toggle = self._destination_checkboxes.get(location.name)
            if toggle and toggle.isChecked():
                selected.append(location)
        return selected

    def _is_custom_destination_selected(self) -> bool:
        toggle = self._destination_checkboxes.get(self.SAVE_LOCS.CUSTOM.name)
        return bool(toggle and toggle.isChecked())

    def _resolved_destination_path(self, location: SaveLocation) -> str:
        if location.name == self.SAVE_LOCS.CUSTOM.name:
            return self._custom_folder_field.text().strip()
        return str(location.path)

    def _refresh_destination_path_labels(self) -> None:
        for location_name, path_label in self._destination_path_labels.items():
            location = self._save_locations_by_name[location_name]
            path_label.setText(f"-> {self._resolved_destination_path(location)}")

    def _paths_for_filename(
        self, filename: str
    ) -> dict[FFmpegPreset, list[str | Path]]:
        paths: dict[FFmpegPreset, list[str | Path]] = defaultdict(list)
        for location in self._selected_destination_locations():
            destination_dir = self._resolved_destination_path(location).strip()
            if not destination_dir:
                continue
            paths[location.preset].append(str(Path(destination_dir) / filename))
        return paths

    def _selected_destination_directories(self) -> list[Path]:
        directories: list[Path] = []
        for location in self._selected_destination_locations():
            destination_dir = self._resolved_destination_path(location).strip()
            if destination_dir:
                directories.append(Path(destination_dir))
        return directories

    def _resolve_output_name(self, prefix: str) -> str:
        return next_versioned_basename(
            prefix,
            self._selected_destination_directories(),
        )

    def _read_asset_metadata(self):
        try:
            return read_asset_metadata()
        except Exception:
            log.exception(
                "Could not resolve asset metadata from the current Maya scene."
            )
            return None

    def _asset_display_name(self) -> str:
        if self._asset_metadata and self._asset_metadata.asset:
            return self._asset_metadata.asset.display_name
        if self._asset_metadata and self._asset_metadata.display_name:
            return self._asset_metadata.display_name
        scene_path = _scene_path()
        if scene_path is not None:
            return scene_path.stem
        return "turnaround"

    def _shotgrid_asset_display_name(self) -> str | None:
        if self._asset_metadata and self._asset_metadata.asset:
            return self._asset_metadata.asset.display_name
        if self._asset_metadata and self._asset_metadata.display_name:
            return self._asset_metadata.display_name
        return None

    def _asset_filename_token(self) -> str:
        if self._asset_metadata and self._asset_metadata.asset:
            return self._asset_metadata.asset.name
        if self._asset_metadata and self._asset_metadata.name:
            return self._asset_metadata.name
        return normalize_display_name(self._asset_display_name()) or "turnaround"

    def _refresh_context(self) -> None:
        self._asset_metadata = self._read_asset_metadata()
        self._review_roots = resolve_turnaround_review_roots()

    def _refresh_context_fields(self) -> None:
        self._asset_value.setText(self._asset_display_name() or "-")
        summary = self._review_roots.summary
        self._review_root_value.setText(
            f"{summary} ({self._review_roots.source_label})"
        )
        self._passes_value.setText(self._pass_summary_text())

    def _pass_summary_text(self) -> str:
        shaded_summary = f"{DEFAULT_FRAMES_PER_PASS} shaded"
        if self._include_wireframe_pass.isChecked():
            return f"{shaded_summary} + {DEFAULT_FRAMES_PER_PASS} wireframe"
        return shaded_summary

    def _sync_custom_path_row_visibility(self) -> None:
        is_visible = self._is_custom_destination_selected()
        self._custom_folder_row.setVisible(is_visible)
        self._custom_folder_field.setEnabled(is_visible)

    def _sync_shotgrid_upload_target_visibility(self) -> None:
        show_target = self._is_shotgrid_upload_requested()
        self._shotgrid_upload_target_row.setVisible(show_target)

    def _sync_shotgrid_review_visibility(self) -> None:
        show_review = (
            self._is_shotgrid_upload_requested()
            and self._is_shotgrid_review_upload_enabled()
        )
        self._shotgrid_review_combo.setVisible(show_review)
        self._shotgrid_review_combo.set_combo_enabled(show_review)

    def _sync_shotgrid_description_visibility(self) -> None:
        show_description = self._is_shotgrid_upload_requested()
        self._shotgrid_description_row.setVisible(show_description)
        self._shotgrid_description_field.setEnabled(show_description)

    def _is_shotgrid_upload_requested(self) -> bool:
        return self._shotgrid_upload_checkbox.isChecked()

    def _is_shotgrid_version_upload_enabled(self) -> bool:
        return self._shotgrid_upload_version_checkbox.isChecked()

    def _is_shotgrid_review_upload_enabled(self) -> bool:
        return self._shotgrid_upload_review_checkbox.isChecked()

    def _shotgrid_upload_description(self) -> str:
        return self._shotgrid_description_field.text().strip()

    def _validate_state(self) -> str | None:
        if not self._review_roots.roots:
            return (
                "Select geometry for the turnaround, or make visible geometry "
                "available in the scene."
            )

        if not self._selected_destination_locations():
            return "Select at least one save destination."

        if (
            self._is_custom_destination_selected()
            and not self._custom_folder_field.text().strip()
        ):
            return "Custom Folder path is required when Custom Folder is enabled."

        if (
            self._destination_checkboxes[self.SAVE_LOCS.CURRENT.name].isChecked()
            and _scene_path() is None
        ):
            return "Save the scene before exporting to Render Folder."

        if (
            self._is_shotgrid_upload_requested()
            and not self._shotgrid_asset_display_name()
        ):
            return "Could not resolve asset metadata for ShotGrid upload."

        if (
            self._is_shotgrid_upload_requested()
            and not self._is_shotgrid_version_upload_enabled()
            and not self._is_shotgrid_review_upload_enabled()
        ):
            return (
                "Select at least one ShotGrid upload option: 'Upload as new asset "
                "version' or 'Upload to review for dailies'."
            )

        if (
            self._is_shotgrid_upload_requested()
            and self._is_shotgrid_review_upload_enabled()
            and self._shotgrid_review_combo.selected_playlist_id is None
        ):
            if self._shotgrid_review_combo.load_error:
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

    def _update_action_state(self) -> None:
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is None:
            return

        validation_error = self._validate_state()
        ok_button.setEnabled(validation_error is None)
        self._validation_label.setText(validation_error or "")
        self._validation_label.setVisible(validation_error is not None)

    def _update_ui_state(self) -> None:
        self._refresh_context_fields()
        self._sync_custom_path_row_visibility()
        if (
            self._is_shotgrid_upload_requested()
            and self._is_shotgrid_review_upload_enabled()
        ):
            self._shotgrid_review_combo.ensure_loaded_lazily()
        self._sync_shotgrid_upload_target_visibility()
        self._sync_shotgrid_review_visibility()
        self._sync_shotgrid_description_visibility()
        self._refresh_destination_path_labels()
        self._update_action_state()

    def _collect_output_paths(self, config: TurnaroundPlayblastConfig) -> list[str]:
        output_paths: list[str] = []
        for preset, bases in config.output_paths.items():
            for base in bases:
                output_paths.append(str(Path(str(base) + f".{preset.ext}")))
        return output_paths

    @staticmethod
    def _ordered_final_movie_paths(
        config: TurnaroundPlayblastConfig,
    ) -> list[Path]:
        ordered_paths: list[Path] = []
        for preset, bases in config.output_paths.items():
            for base in bases:
                ordered_paths.append(
                    Path(str(base) + f".{preset.ext}").expanduser().resolve()
                )
        return ordered_paths

    def _build_success_message(
        self,
        output_paths: list[str],
        post_export_messages: list[str],
    ) -> str:
        message_lines = ["Local turnaround export successful."]
        if output_paths:
            message_lines.append("")
            message_lines.append("Outputs:")
            message_lines.extend(output_paths)
        if post_export_messages:
            message_lines.append("")
            message_lines.append("Post-export:")
            message_lines.extend(post_export_messages)
        return "\n".join(message_lines)

    def _build_config(self) -> TurnaroundPlayblastConfig:
        output_name = self._resolve_output_name(
            f"{self._asset_filename_token()}_turnaround"
        )
        return TurnaroundPlayblastConfig(
            asset_label=self._asset_display_name(),
            output_paths=self._paths_for_filename(output_name),
            review_roots=self._review_roots.roots,
            use_default_material=self._use_default_material.isChecked(),
            use_shadows=self._use_shadows.isChecked(),
            use_anti_aliasing=self._use_anti_aliasing.isChecked(),
            include_wireframe_pass=self._include_wireframe_pass.isChecked(),
        )

    def _after_local_export(self, config: TurnaroundPlayblastConfig) -> list[str]:
        if not self._is_shotgrid_upload_requested():
            return []

        asset_display_name = self._shotgrid_asset_display_name()
        if not asset_display_name:
            return ["ShotGrid Upload: Skipped - asset metadata could not be resolved."]

        intent = PlayblastUploadIntent(
            entity=PlayblastEntity.asset(asset_display_name),
            output_paths=tuple(self._ordered_final_movie_paths(config)),
            preferred_paths=(),
            description=self._shotgrid_upload_description() or None,
            artist_display_name=resolve_artist_display_name().strip() or None,
            upload_version=self._is_shotgrid_version_upload_enabled(),
            upload_to_review=self._is_shotgrid_review_upload_enabled(),
            review_playlist_id=self._shotgrid_review_combo.selected_playlist_id,
            review_load_error=self._shotgrid_review_combo.load_error,
            fallback_version_name=f"{self._asset_filename_token()}_turnaround",
        )
        return run_playblast_upload(intent)

    def do_export(self) -> None:
        self._refresh_context()
        self._update_ui_state()

        validation_error = self._validate_state()
        if validation_error:
            MessageDialog(self, validation_error, "Asset Turnaround").exec_()
            return

        try:
            config = self._build_config()
        except Exception as exc:
            log.exception("Turnaround config generation failed")
            MessageDialog(
                self,
                f"Could not generate turnaround settings.\n\n{exc}",
                "Turnaround Error",
            ).exec_()
            return

        try:
            self.playblaster.configure(config).playblast(parent=self)
        except Exception as exc:
            log.exception("Turnaround export failed")
            MessageDialog(
                self,
                f"Turnaround export failed.\n\n{exc}",
                "Turnaround Error",
            ).exec_()
            return

        post_export_messages: list[str] = []
        try:
            post_export_messages = self._after_local_export(config)
        except Exception as exc:
            log.exception("Post-export actions failed")
            post_export_messages = [
                "Post-export actions failed. Local turnaround movie was still written.",
                f"Reason: {exc}",
            ]

        success_msg = self._build_success_message(
            self._collect_output_paths(config),
            post_export_messages,
        )
        MessageDialog(self, success_msg).exec_()
        self.close()

    def _set_custom_folder(self) -> None:
        path_list = mc.fileDialog2(
            caption="Select a custom turnaround folder",
            fileMode=2,
            hideNameEdit=True,
            okCaption="Select",
            setProjectBtnEnabled=False,
        )
        if path_list:
            self._custom_folder_field.setText(path_list[0])

    def _on_refresh_selection_clicked(self) -> None:
        self._refresh_context()
        self._update_ui_state()

    def _on_settings_changed(self, *_args) -> None:
        self._update_ui_state()


__all__ = ["AssetTurnaroundDialog"]
