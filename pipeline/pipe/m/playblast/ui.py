from __future__ import annotations

import logging
import os
from abc import abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import maya.cmds as mc
from Qt import QtCore, QtWidgets
from Qt.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pipe.glui.dialogs import ButtonPair, MessageDialog
from pipe.util import Playblaster

from .playblaster import MPlayblaster
from .struct import HudDefinition, SaveLocation

if TYPE_CHECKING:
    from typing import Callable, Iterable

    from .struct import (
        MPlayblastConfig,
        MShotDialogConfig,
        MShotPlayblastConfig,
    )

log = logging.getLogger(__name__)


class ClickableQLabel(QLabel):
    clicked = QtCore.Signal()

    def mousePressEvent(self, event):  # type: ignore[override]
        self.clicked.emit()
        super().mousePressEvent(event)


class PlayblastDialog(ButtonPair, QtWidgets.QMainWindow):
    """Shared Maya playblast dialog.

    The dialog is intentionally organized into linear sections so artists can
    understand and configure exports quickly:
    1) choose export targets + destinations
    2) configure shot-specific options (subclass provided)
    3) configure viewport and folder options
    4) review a live export summary
    """

    _central_widget: QWidget
    _context_group: QGroupBox
    _context_layout: QVBoxLayout
    _custom_folder_field: QLineEdit
    _enabled_loc_cbs: dict[str, dict[str, QCheckBox]]
    _enabled_shot_cbs: dict[str, QCheckBox]
    _main_layout: QVBoxLayout
    _save_locs_by_shot: dict[str, list[SaveLocation]]
    _summary_field: QPlainTextEdit
    _use_dof: QCheckBox
    _use_hardware_fog: QCheckBox
    _use_lighting: QCheckBox
    _use_shadows: QCheckBox
    _use_ssao: QCheckBox

    playblaster = MPlayblaster()
    shot_configs: list[MShotDialogConfig]

    class SAVE_LOCS:
        CUSTOM = SaveLocation("Custom Folder", "", Playblaster.PRESET.WEB)
        CURRENT = SaveLocation(
            "Current Folder",
            lambda: Path(str(mc.file(query=True, sceneName=True) or ".")).parent,
            Playblaster.PRESET.WEB,
        )

    class MAYA_HUDS:
        CAM_NAME = "HUDCameraNames"
        CUR_FRAME = "HUDCurrentFrame"
        FOCAL_LENGTH = "HUDFocalLength"

    class CUSTOM_HUDS:
        FILENAME = HudDefinition(
            "LnDfilename",
            command=lambda: os.path.splitext(
                os.path.basename(str(mc.file(query=True, sceneName=True) or ""))
            )[0],
            event="SceneSaved",
            label="File:",
            section=5,
        )
        ARTIST = HudDefinition(
            "LnDartist",
            command=lambda: os.getlogin(),
            event="SceneOpened",
            label="Artist:",
            section=5,
        )

    def __init__(
        self,
        parent: QWidget | None,
        shot_configs: list[MShotDialogConfig],
        windowTitle: str = "LnD Playblast",
    ) -> None:
        super().__init__(parent, windowTitle=windowTitle)
        self.shot_configs = shot_configs
        self._enabled_shot_cbs = {}
        self._enabled_loc_cbs = defaultdict(dict)
        self._save_locs_by_shot = {
            cfg.id: [loc for loc, _ in cfg.save_locs] for cfg in self.shot_configs
        }

        self._setup_ui()
        self.SAVE_LOCS.CUSTOM._path = lambda: self._custom_folder_field.text()
        self._refresh_summary()

    def _setup_ui(self) -> None:
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)
        self._main_layout = QVBoxLayout()
        self._central_widget.setLayout(self._main_layout)

        self._build_header_section()
        self._build_targets_section()
        self._build_context_section()
        self._build_render_options_section()
        self._build_summary_section()
        self._build_buttons()

    def _build_header_section(self) -> None:
        title = QLabel("Playblast Export")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet("font-size: 24px; font-weight: 700;")

        subtitle = QLabel(
            "Select targets, choose destinations, and verify outputs before exporting."
        )
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setStyleSheet("color: #666;")

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_targets_section(self) -> None:
        target_group = QGroupBox("1. Targets and Destinations")
        target_layout = QVBoxLayout(target_group)

        description = QLabel(
            "Enable each target you want to export, then choose one or more destinations."
        )
        description.setStyleSheet("color: #666;")
        target_layout.addWidget(description)

        location_order = self._collect_location_order()
        targets_grid = self._build_targets_grid(location_order)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setWidget(targets_grid)
        target_layout.addWidget(scroll)

        self._main_layout.addWidget(target_group)

    def _build_targets_grid(self, location_order: list[SaveLocation]) -> QWidget:
        grid_container = QWidget()
        grid = QGridLayout(grid_container)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self._add_targets_grid_header(grid, location_order)
        self._add_targets_grid_bulk_controls(grid, location_order)
        self._add_targets_grid_rows(grid, location_order)
        return grid_container

    def _add_targets_grid_header(
        self, grid: QGridLayout, location_order: list[SaveLocation]
    ) -> None:
        grid.addWidget(QLabel("Export"), 0, 0)
        grid.addWidget(QLabel("Target"), 0, 1)

        for column, location in enumerate(location_order, start=2):
            header = QLabel(f"{location.name}\n(*.{location.preset.ext})")
            header.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(header, 0, column)

    def _add_targets_grid_bulk_controls(
        self, grid: QGridLayout, location_order: list[SaveLocation]
    ) -> None:
        select_all_targets = QPushButton("All Targets")
        select_all_targets.clicked.connect(self._make_set_all_targets_callback(True))
        grid.addWidget(select_all_targets, 1, 0)

        select_no_targets = QPushButton("No Targets")
        select_no_targets.clicked.connect(self._make_set_all_targets_callback(False))
        grid.addWidget(select_no_targets, 1, 1)

        for column, location in enumerate(location_order, start=2):
            location_controls = self._build_location_bulk_controls(location.name)
            grid.addWidget(location_controls, 1, column)

    def _build_location_bulk_controls(self, location_name: str) -> QWidget:
        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)

        enable_all = QPushButton("All")
        enable_all.clicked.connect(
            self._make_set_all_location_destinations_callback(location_name, True)
        )
        controls_layout.addWidget(enable_all)

        enable_none = QPushButton("None")
        enable_none.clicked.connect(
            self._make_set_all_location_destinations_callback(location_name, False)
        )
        controls_layout.addWidget(enable_none)
        return controls_widget

    def _add_targets_grid_rows(
        self, grid: QGridLayout, location_order: list[SaveLocation]
    ) -> None:
        for row, config in enumerate(self.shot_configs, start=2):
            self._add_target_row(grid, row, config, location_order)

    def _add_target_row(
        self,
        grid: QGridLayout,
        row: int,
        config: MShotDialogConfig,
        location_order: list[SaveLocation],
    ) -> None:
        target_toggle = self._build_target_toggle(config.id)
        self._enabled_shot_cbs[config.id] = target_toggle
        grid.addWidget(target_toggle, row, 0, alignment=QtCore.Qt.AlignCenter)

        target_label = ClickableQLabel(f"<b>{config.name}</b>", target_toggle)
        target_label.clicked.connect(lambda cb=target_toggle: cb.click())
        grid.addWidget(target_label, row, 1)

        config_locations = {
            location.name: (location, enabled) for location, enabled in config.save_locs
        }
        for column, location in enumerate(location_order, start=2):
            location_data = config_locations.get(location.name)
            if location_data is None:
                placeholder = self._build_destination_placeholder()
                grid.addWidget(placeholder, row, column)
                continue

            _, enabled_by_default = location_data
            destination_toggle = self._build_destination_toggle(
                config.id, location.name
            )
            destination_toggle.setChecked(enabled_by_default)
            grid.addWidget(
                destination_toggle, row, column, alignment=QtCore.Qt.AlignCenter
            )

        self._set_shot_locations_enabled(config.id, target_toggle.isChecked())

    def _build_target_toggle(self, shot_id: str) -> QCheckBox:
        target_toggle = QCheckBox()
        target_toggle.setChecked(True)
        target_toggle.toggled.connect(
            self._make_set_shot_locations_enabled_callback(shot_id)
        )
        target_toggle.toggled.connect(self._refresh_summary)
        return target_toggle

    def _build_destination_toggle(self, shot_id: str, location_name: str) -> QCheckBox:
        destination_toggle = QCheckBox()
        destination_toggle.toggled.connect(self._refresh_summary)
        self._enabled_loc_cbs[shot_id][location_name] = destination_toggle
        return destination_toggle

    @staticmethod
    def _build_destination_placeholder() -> QLabel:
        placeholder = QLabel("-")
        placeholder.setAlignment(QtCore.Qt.AlignCenter)
        placeholder.setEnabled(False)
        return placeholder

    def _make_set_all_targets_callback(self, enabled: bool) -> Callable[[], None]:
        def callback() -> None:
            self._set_all_targets(enabled)

        return callback

    def _make_set_all_location_destinations_callback(
        self, location_name: str, enabled: bool
    ) -> Callable[[], None]:
        def callback() -> None:
            self._set_location_for_all(location_name, enabled)

        return callback

    def _make_set_shot_locations_enabled_callback(
        self, shot_id: str
    ) -> Callable[[bool], None]:
        def callback(enabled: bool) -> None:
            self._set_shot_locations_enabled(shot_id, enabled)

        return callback

    def _build_context_section(self) -> None:
        self._context_group = QGroupBox("2. Shot Settings")
        self._context_layout = QVBoxLayout()
        self._context_layout.setContentsMargins(8, 8, 8, 8)
        self._context_group.setLayout(self._context_layout)
        self._context_group.setVisible(False)
        self._main_layout.addWidget(self._context_group)

    def add_context_widget(self, widget: QWidget) -> None:
        self._context_group.setVisible(True)
        self._context_layout.addWidget(widget)

    def _build_render_options_section(self) -> None:
        options_group = QGroupBox("3. Render Options")
        options_layout = QVBoxLayout(options_group)

        active_panel = self._resolve_active_model_panel()
        options_layout.addWidget(self._build_viewport_options_widget(active_panel))
        options_layout.addWidget(self._build_custom_folder_widget())
        self._main_layout.addWidget(options_group)

    def _build_viewport_options_widget(self, active_panel: str) -> QWidget:
        viewport_widget = QWidget()
        viewport_layout = QHBoxLayout(viewport_widget)
        viewport_layout.setContentsMargins(0, 0, 0, 0)

        self._use_lighting = self._build_option_checkbox(
            "Use Lighting",
            self._query_lighting(active_panel),
        )
        viewport_layout.addWidget(self._use_lighting)

        self._use_shadows = self._build_option_checkbox(
            "Use Shadows",
            self._query_shadows(active_panel),
        )
        viewport_layout.addWidget(self._use_shadows)

        self._use_ssao = self._build_option_checkbox(
            "Use Anti-aliasing",
            self._query_ssao(),
        )
        viewport_layout.addWidget(self._use_ssao)

        self._use_hardware_fog = self._build_option_checkbox(
            "Use Hardware Fog",
            self._query_hardware_fog(active_panel),
        )
        viewport_layout.addWidget(self._use_hardware_fog)

        self._use_dof = self._build_option_checkbox(
            "Use DoF",
            self._query_dof(active_panel),
        )
        viewport_layout.addWidget(self._use_dof)
        return viewport_widget

    def _build_custom_folder_widget(self) -> QWidget:
        custom_folder_widget = QWidget()
        custom_folder_layout = QHBoxLayout(custom_folder_widget)
        custom_folder_layout.setContentsMargins(0, 0, 0, 0)

        self._custom_folder_field = QLineEdit()
        self._custom_folder_field.setReadOnly(True)
        self._custom_folder_field.setText(self._default_custom_folder_path())
        self._custom_folder_field.textChanged.connect(self._refresh_summary)
        custom_folder_layout.addWidget(self._custom_folder_field)

        browse_button = QPushButton("Browse Custom Folder")
        browse_button.clicked.connect(self._set_custom_folder)
        custom_folder_layout.addWidget(browse_button)
        return custom_folder_widget

    def _build_option_checkbox(self, label: str, enabled_by_default: bool) -> QCheckBox:
        option_toggle = QCheckBox(label)
        option_toggle.setChecked(enabled_by_default)
        option_toggle.toggled.connect(self._refresh_summary)
        return option_toggle

    @staticmethod
    def _default_custom_folder_path() -> str:
        return os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))

    def _build_summary_section(self) -> None:
        summary_group = QGroupBox("4. Export Summary")
        summary_layout = QVBoxLayout(summary_group)

        self._summary_field = QPlainTextEdit()
        self._summary_field.setReadOnly(True)
        self._summary_field.setMinimumHeight(150)
        summary_layout.addWidget(self._summary_field)

        self._main_layout.addWidget(summary_group)

    def _build_buttons(self) -> None:
        self._init_buttons(has_cancel_button=True, ok_name="Playblast")
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.do_export)
        self._main_layout.addWidget(self.buttons)

    def _collect_location_order(self) -> list[SaveLocation]:
        ordered_locations: list[SaveLocation] = []
        seen_names: set[str] = set()

        for config in self.shot_configs:
            for location, _enabled in config.save_locs:
                if location.name in seen_names:
                    continue
                seen_names.add(location.name)
                ordered_locations.append(location)
        return ordered_locations

    def _set_all_targets(self, enabled: bool) -> None:
        for checkbox in self._enabled_shot_cbs.values():
            if checkbox.isEnabled():
                checkbox.setChecked(enabled)

    def _set_location_for_all(self, location_name: str, enabled: bool) -> None:
        for location_map in self._enabled_loc_cbs.values():
            checkbox = location_map.get(location_name)
            if checkbox is not None:
                checkbox.setChecked(enabled)

    def _set_shot_locations_enabled(self, shot_id: str, enabled: bool) -> None:
        for checkbox in self._enabled_loc_cbs.get(shot_id, {}).values():
            checkbox.setEnabled(enabled)

    @staticmethod
    def _resolve_active_model_panel() -> str:
        panel = str(mc.sequenceManager(query=True, modelPanel=True) or "")
        if panel and mc.modelPanel(panel, exists=True):
            return panel

        model_panels = mc.getPanel(type="modelPanel") or []
        if model_panels:
            return str(model_panels[0])
        return ""

    @staticmethod
    def _query_lighting(panel: str) -> bool:
        if not panel:
            return False
        try:
            return mc.modelEditor(panel, query=True, displayLights=True) == "all"
        except Exception:
            return False

    @staticmethod
    def _query_shadows(panel: str) -> bool:
        if not panel:
            return False
        try:
            return bool(mc.modelEditor(panel, query=True, shadows=True))
        except Exception:
            return False

    @staticmethod
    def _query_ssao() -> bool:
        try:
            return bool(mc.getAttr("hardwareRenderingGlobals.ssaoEnable"))
        except Exception:
            return False

    @staticmethod
    def _query_hardware_fog(panel: str) -> bool:
        if not panel:
            return False
        try:
            return bool(mc.modelEditor(panel, query=True, fogging=True))
        except Exception:
            return False

    @staticmethod
    def _query_dof(panel: str) -> bool:
        if not panel:
            return False
        try:
            camera = str(mc.modelEditor(panel, query=True, camera=True))
            return bool(mc.camera(camera, query=True, depthOfField=True))
        except Exception:
            return False

    @property
    def use_dof(self) -> bool:
        return self._use_dof.isChecked()

    @property
    def use_hardware_fog(self) -> bool:
        return self._use_hardware_fog.isChecked()

    @property
    def use_lighting(self) -> bool:
        return self._use_lighting.isChecked()

    @property
    def use_shadows(self) -> bool:
        return self._use_shadows.isChecked()

    @property
    def use_ssao(self) -> bool:
        return self._use_ssao.isChecked()

    def _refresh_summary(self, *_args) -> None:
        lines: list[str] = []
        lines.extend(self._summary_target_lines())
        lines.append("")
        lines.extend(self._summary_render_option_lines())
        self._summary_field.setPlainText("\n".join(lines))

    def _summary_target_lines(self) -> list[str]:
        lines: list[str] = []
        enabled_configs = [
            config for config in self.shot_configs if self.is_shot_enabled(config.id)
        ]
        if not enabled_configs:
            lines.append("No targets selected.")
            return lines

        lines.append("Selected targets:")
        for config in enabled_configs:
            lines.append(f"- {config.name}")
            enabled_locations = [
                location
                for location in self._save_locs_by_shot[config.id]
                if self.is_location_enabled(config.id, location.name)
            ]
            if not enabled_locations:
                lines.append("  (no destination selected)")
                continue

            for location in enabled_locations:
                lines.append(f"  -> {location.name}: {location.path}")
        return lines

    def _summary_render_option_lines(self) -> list[str]:
        return [
            "Viewport options:",
            f"- Lighting: {'On' if self.use_lighting else 'Off'}",
            f"- Shadows: {'On' if self.use_shadows else 'Off'}",
            f"- Anti-aliasing: {'On' if self.use_ssao else 'Off'}",
            f"- Hardware Fog: {'On' if self.use_hardware_fog else 'Off'}",
            f"- Depth of Field: {'On' if self.use_dof else 'Off'}",
        ]

    def save_locations_to_paths(
        self, dialog_id: str, locs: Iterable[SaveLocation], filename: str
    ) -> dict[Playblaster.PRESET, list[str | Path]]:
        paths: dict[Playblaster.PRESET, list[str | Path]] = defaultdict(list)
        for location in locs:
            if not self.is_location_enabled(dialog_id, location.name):
                continue

            destination_dir = str(location.path).strip()
            if not destination_dir:
                continue

            output_base = Path(destination_dir) / filename
            paths[location.preset].append(str(output_base))
        return paths

    def _set_custom_folder(self) -> None:
        path_list = mc.fileDialog2(
            caption="Select a custom playblast folder",
            fileMode=2,
            hideNameEdit=True,
            okCaption="Select",
            setProjectBtnEnabled=False,
        )
        if path_list:
            self._custom_folder_field.setText(path_list[0])

    @abstractmethod
    def _generate_config(self) -> MPlayblastConfig:
        raise NotImplementedError

    def is_shot_enabled(self, dialog_id: str) -> bool:
        checkbox = self._enabled_shot_cbs.get(dialog_id)
        return bool(checkbox and checkbox.isChecked())

    def is_location_enabled(self, dialog_id: str, loc_name: str) -> bool:
        location_map = self._enabled_loc_cbs.get(dialog_id)
        if not location_map:
            return False
        checkbox = location_map.get(loc_name)
        return bool(checkbox and checkbox.isChecked())

    @staticmethod
    def _output_count_for_shot(shot_cfg: MShotPlayblastConfig) -> int:
        return sum(len(paths) for paths in shot_cfg.paths.values())

    @staticmethod
    def _collect_output_paths(config: MPlayblastConfig) -> list[str]:
        output_paths: list[str] = []
        for shot_cfg in config.shots:
            for preset, bases in shot_cfg.paths.items():
                for base in bases:
                    output_paths.append(str(Path(str(base) + f".{preset.ext}")))
        return output_paths

    def _validate_config(self, config: MPlayblastConfig) -> str | None:
        if not config.shots:
            return "No playblast targets are enabled."

        for shot_cfg in config.shots:
            if self._output_count_for_shot(shot_cfg) < 1:
                return (
                    f"Target '{shot_cfg.shot.code}' has no output location selected. "
                    "Please enable at least one destination."
                )
        return None

    def do_export(self) -> None:
        try:
            config = self._generate_config()
        except Exception as exc:
            log.exception("Playblast config generation failed")
            MessageDialog(
                self.parent(),
                f"Could not generate playblast settings.\n\n{exc}",
                "Playblast Error",
            ).exec_()
            return

        validation_error = self._validate_config(config)
        if validation_error:
            MessageDialog(self.parent(), validation_error, "Playblast").exec_()
            return

        try:
            self.playblaster.configure(config).playblast()
        except Exception as exc:
            log.exception("Playblast export failed")
            MessageDialog(
                self.parent(),
                f"Playblast failed.\n\n{exc}",
                "Playblast Error",
            ).exec_()
            return

        output_paths = self._collect_output_paths(config)
        success_msg = "Playblast(s) successful!"
        if output_paths:
            success_msg += "\n\nOutputs:\n" + "\n".join(output_paths)
        MessageDialog(self.parent(), success_msg).exec_()
        self.close()
