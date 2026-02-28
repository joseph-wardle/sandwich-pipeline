from __future__ import annotations

import logging
import re
from collections import defaultdict
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
from pipe.m.shotfile.anim import _find_usd_shotcam
from pipe.playblast_naming import (
    playblast_date_folder,
    resolve_versioned_playblast_basename,
)
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


class AnimPlayblastDialog(PlayblastDialog):
    _context_banner: QLabel
    _custom_camera: QComboBox
    _custom_folder_row: QWidget
    _custom_in: QSpinBox
    _custom_out: QSpinBox
    _destination_checkboxes: dict[str, QCheckBox]
    _destination_path_labels: dict[str, QLabel]
    _save_locations_by_name: dict[str, SaveLocation]
    _source_tabs: QTabWidget
    _shot_camera_value: QLabel
    _shot_code_value: QLabel
    _shot_range_value: QLabel
    _shot: Shot | None
    _shot_pass: QComboBox
    _validation_label: QLabel

    SHOT_TAB_INDEX = 0
    CUSTOM_TAB_INDEX = 1
    BOTH_TAB_INDEX = 2

    SOURCE_MODE = Literal["shot", "custom", "both"]

    CONTEXT_BANNER_STYLE = (
        "padding: 8px; border: 1px solid #c3cfdb; background: #e3ebf5; color: #666;"
    )

    PASS_PATTERN = re.compile(r"^(?:Blocking|Polish) #\d+$")

    class SAVE_LOCS(PlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            lambda: get_edit_path() / "anim" / playblast_date_folder(),
            Playblaster.PRESET.EDIT_SQ,
        )

    def __init__(self, parent) -> None:
        self._shot = self._resolve_pipeline_shot_context()
        self._destination_checkboxes = {}
        self._destination_path_labels = {}
        self._save_locations_by_name = {
            location.name: location for location in self._destination_locations()
        }
        super().__init__(parent, [], "SKD Anim Playblast")

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
        title = QLabel("SKD Anim Playblast")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)

        subtitle = QLabel("Choose source mode, choose destinations, then export.")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setToolTip(
            "High-level workflow: choose source, choose destinations, then export."
        )

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_targets_section(self) -> None:
        setup_group = QGroupBox("1. Export Setup")
        setup_layout = QVBoxLayout(setup_group)

        self._context_banner = QLabel()
        self._context_banner.setWordWrap(True)
        self._context_banner.setStyleSheet(self.CONTEXT_BANNER_STYLE)
        self._context_banner.setToolTip(
            "Live context for selected export mode: shot, camera, and frame range."
        )
        setup_layout.addWidget(self._context_banner)

        setup_layout.addWidget(self._build_export_source_section())
        setup_layout.addWidget(self._build_destination_section())

        self._validation_label = QLabel()
        self._validation_label.setStyleSheet("color: #b00020;")
        self._validation_label.setToolTip(
            "Validation feedback. Export is disabled until this message is cleared."
        )
        self._validation_label.setVisible(False)
        setup_layout.addWidget(self._validation_label)

        self._main_layout.addWidget(setup_group)

    def _build_export_source_section(self) -> QGroupBox:
        source_group = QGroupBox("")
        source_layout = QVBoxLayout(source_group)

        self._source_tabs = QTabWidget()
        self._source_tabs.addTab(
            self._build_shot_source_tab(), "Shot Playblast (Pipeline Shot File)"
        )
        self._source_tabs.addTab(
            self._build_custom_source_tab(),
            "Custom Playblast",
        )
        self._source_tabs.addTab(self._build_both_source_tab(), "Both")
        self._source_tabs.currentChanged.connect(self._on_source_mode_changed)

        source_tab_bar = self._source_tabs.tabBar()
        source_tab_bar.setTabToolTip(
            self.SHOT_TAB_INDEX,
            "Export the pipeline shot loaded in this Maya scene.",
        )
        source_tab_bar.setTabToolTip(
            self.CUSTOM_TAB_INDEX,
            "Export a manual camera and frame range.",
        )
        source_tab_bar.setTabToolTip(
            self.BOTH_TAB_INDEX,
            "Export both the pipeline shot and custom range in one run.",
        )

        source_layout.addWidget(self._source_tabs)
        source_layout.addWidget(self._build_pass_row())
        return source_group

    def _build_pass_row(self) -> QWidget:
        pass_row = QWidget()
        pass_layout = QHBoxLayout(pass_row)
        pass_layout.setContentsMargins(0, 0, 0, 0)

        pass_layout.addWidget(QLabel("Pass"))

        self._shot_pass = QComboBox(self)
        self._shot_pass.addItems(["Blocking #1", "Polish #1"])
        self._shot_pass.setEditable(True)
        self._shot_pass.setToolTip(
            "Pass text shown in the HUD. Format: Blocking #<n> or Polish #<n>."
        )
        self._shot_pass.currentTextChanged.connect(self._on_source_settings_changed)
        pass_layout.addWidget(self._shot_pass)
        pass_layout.addStretch()

        return pass_row

    def _build_shot_source_tab(self) -> QWidget:
        shot_tab = QWidget()
        shot_layout = QGridLayout(shot_tab)

        shot_layout.addWidget(QLabel("Source"), 0, 0)
        source_value = QLabel("Pipeline Shot File")
        source_value.setToolTip(
            "Uses shot context from this scene's pipeline shot code."
        )
        shot_layout.addWidget(source_value, 0, 1)

        shot_layout.addWidget(QLabel("Shot"), 1, 0)
        self._shot_code_value = QLabel("-")
        self._shot_code_value.setToolTip("Resolved pipeline shot code.")
        shot_layout.addWidget(self._shot_code_value, 1, 1)

        shot_layout.addWidget(QLabel("Camera"), 2, 0)
        self._shot_camera_value = QLabel("-")
        self._shot_camera_value.setToolTip("Resolved shot camera path.")
        shot_layout.addWidget(self._shot_camera_value, 2, 1)

        shot_layout.addWidget(QLabel("Frame Range"), 3, 0)
        self._shot_range_value = QLabel("-")
        self._shot_range_value.setToolTip("Resolved shot cut range from pipeline shot.")
        shot_layout.addWidget(self._shot_range_value, 3, 1)

        return shot_tab

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

    def _build_both_source_tab(self) -> QWidget:
        both_tab = QWidget()
        both_layout = QVBoxLayout(both_tab)

        message = QLabel(
            "Both mode exports:\n"
            "1) Shot Playblast (Pipeline Shot File)\n"
            "2) Custom Playblast (manual camera + range)"
        )
        message.setWordWrap(True)
        message.setToolTip(
            "This mode exports both sources in one run using the same destination selection."
        )
        both_layout.addWidget(message)
        both_layout.addStretch()
        return both_tab

    def _build_destination_section(self) -> QGroupBox:
        destination_group = QGroupBox("Save Destinations")
        destination_layout = QVBoxLayout(destination_group)

        for location in self._destination_locations():
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            toggle = QCheckBox(location.name)
            toggle.setChecked(self._default_destination_enabled(location))
            toggle.setToolTip(f"Enable export to {location.name}.")
            toggle.toggled.connect(self._on_destination_changed)
            self._destination_checkboxes[location.name] = toggle
            row_layout.addWidget(toggle)

            path_label = QLabel("")
            path_label.setToolTip(f"Resolved output directory for {location.name}.")
            self._destination_path_labels[location.name] = path_label
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
                "Start playblast with current source and destination selections."
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
        has_shot_context = self._shot is not None
        self._source_tabs.setTabEnabled(self.SHOT_TAB_INDEX, has_shot_context)
        self._source_tabs.setTabEnabled(self.BOTH_TAB_INDEX, has_shot_context)

        default_index = (
            self.SHOT_TAB_INDEX if has_shot_context else self.CUSTOM_TAB_INDEX
        )
        self._source_tabs.setCurrentIndex(default_index)

    @staticmethod
    def _resolve_pipeline_shot_context() -> Shot | None:
        conn = DB.Get(DB_Config)
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
    def _destination_locations() -> list[SaveLocation]:
        return [
            AnimPlayblastDialog.SAVE_LOCS.EDIT,
            AnimPlayblastDialog.SAVE_LOCS.CURRENT,
            AnimPlayblastDialog.SAVE_LOCS.CUSTOM,
        ]

    def _default_destination_enabled(self, location: SaveLocation) -> bool:
        return location.name == self.SAVE_LOCS.CURRENT.name

    def _selected_source_mode(self) -> SOURCE_MODE:
        current_index = self._source_tabs.currentIndex()
        if current_index == self.SHOT_TAB_INDEX:
            return "shot"
        if current_index == self.CUSTOM_TAB_INDEX:
            return "custom"
        return "both"

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

    def _refresh_context_banner(self) -> None:
        mode = self._selected_source_mode()

        if mode in {"shot", "both"}:
            if self._shot is None:
                banner_text = (
                    "No pipeline shot context detected. Open a pipeline shot file "
                    "or switch to Custom Playblast."
                )
                self._shot_code_value.setText("-")
                self._shot_camera_value.setText("-")
                self._shot_range_value.setText("-")
            else:
                camera_path = self._get_shot_camera_path() or "<missing camera>"
                banner_text = (
                    f"Detected shot: {self._shot.code} | "
                    f"Camera: {camera_path} | "
                    f"Range: {self._shot.cut_in}-{self._shot.cut_out}"
                )
                self._shot_code_value.setText(self._shot.code)
                self._shot_camera_value.setText(camera_path)
                self._shot_range_value.setText(
                    f"{self._shot.cut_in} - {self._shot.cut_out}"
                )
        else:
            if self._shot is None:
                banner_text = "No shot context detected. Custom Playblast is active."
            else:
                banner_text = (
                    f"Detected shot: {self._shot.code}. "
                    "Custom Playblast is active and will use manual camera/range settings."
                )

        self._context_banner.setText(banner_text)

    def _sync_custom_path_row_visibility(self) -> None:
        is_visible = self._is_custom_destination_selected()
        self._custom_folder_row.setVisible(is_visible)
        self._custom_folder_field.setEnabled(is_visible)

    def _validate_pass_text(self) -> str | None:
        pass_text = str(self._shot_pass.currentText()).strip()
        if not self.PASS_PATTERN.fullmatch(pass_text):
            return "Pass must be formatted like 'Blocking #1' or 'Polish #1'."
        return None

    def _validate_target_destination_state(self) -> str | None:
        mode = self._selected_source_mode()

        if mode in {"shot", "both"}:
            if self._shot is None:
                return (
                    "No pipeline shot context was found. Use a pipeline shot file "
                    "or switch to Custom Playblast."
                )
            if not self._get_shot_camera_path():
                return "Could not resolve a shot camera path for this shot."

        if mode in {"custom", "both"}:
            if self._custom_out.value() < self._custom_in.value():
                return "Custom Out must be greater than or equal to Custom In."
            if not str(self._custom_camera.currentText()).strip():
                return "Choose a camera for Custom Playblast."

        pass_error = self._validate_pass_text()
        if pass_error:
            return pass_error

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
        if mode == "custom":
            return "Playblast Custom"
        return "Playblast Shot + Custom"

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

    def _build_custom_playblast_config(self) -> MShotPlayblastConfig:
        custom_in = self._custom_in.value()
        custom_out = self._custom_out.value()
        custom_prefix = f"customPB_{self._shot.code}" if self._shot else "customPB"
        custom_output_name = self._resolve_output_name(custom_prefix)

        return MShotPlayblastConfig(
            camera=str(self._custom_camera.currentText()),
            shot=dummy_shot(
                code="custom",
                cut_in=custom_in,
                cut_out=custom_out,
                cut_duration=max(0, custom_out - custom_in),
            ),
            paths=self._paths_for_filename(custom_output_name),
            use_sequencer=False,
        )

    def _generate_config(self) -> MPlayblastConfig:
        validation_error = self._validate_target_destination_state()
        if validation_error:
            raise ValueError(validation_error)

        mode = self._selected_source_mode()
        shots: list[MShotPlayblastConfig] = []

        if mode in {"shot", "both"}:
            shots.append(self._build_shot_playblast_config())

        if mode in {"custom", "both"}:
            shots.append(self._build_custom_playblast_config())

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
            shots=shots,
            ssao=self.use_ssao,
        )

    def _get_shot_camera_path(self) -> str | None:
        """Resolve the USD shot camera in Maya, supporting both legacy and current hierarchies."""
        camera_path = _find_usd_shotcam()
        if camera_path:
            return camera_path

        log.warning("No USD shot camera found; falling back to legacy path.")
        return "|__mayaUsd__|shotCamParent|shotCam"
