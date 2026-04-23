from __future__ import annotations

import logging
import os
from abc import abstractmethod
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import maya.cmds as mc
from env_sg import DB_Config
from Qt import QtCore, QtWidgets
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

from pipe.db import DB
from pipe.glui.dialogs import ButtonPair, MessageDialog
from pipe.playblast_artist import resolve_artist_display_name
from pipe.playblast_naming import (
    resolve_versioned_playblast_basename,
)
from pipe.playblast_shotgrid import (
    UPLOAD_TARGET_REVIEW,
    UPLOAD_TARGET_VERSION_ONLY,
    PlayblastVersionUploadRequest,
    default_version_name_from_movie_path,
    list_recent_review_playlists,
    resolve_preferred_upload_movie_path,
    upload_playblast_version,
)
from pipe.util import Playblaster

from .playblaster import MPlayblaster
from .struct import HudDefinition, MShotPlayblastConfig, SaveLocation, dummy_shot

if TYPE_CHECKING:
    from pipe.struct.db import Shot

    from .struct import MPlayblastConfig

log = logging.getLogger(__name__)


class ClickableQLabel(QLabel):
    clicked = QtCore.Signal()

    def mousePressEvent(self, event):  # type: ignore
        self.clicked.emit()
        super().mousePressEvent(event)


class PlayblastDialog(ButtonPair, QtWidgets.QMainWindow):
    """Shared Maya playblast dialog using a Tabbed interface.

    The dialog is intentionally organized into linear sections so artists can
    understand and configure exports quickly:
    1) choose export targets + destinations
    2) configure shot-specific options (subclass provided)
    3) configure viewport and folder options
    4) review a live export summary
    """

    _central_widget: QWidget
    _main_layout: QVBoxLayout
    _custom_camera: QComboBox
    _custom_folder_row: QWidget
    _custom_in: QSpinBox
    _custom_out: QSpinBox
    _destination_checkboxes: dict[str, QCheckBox]
    _destination_path_labels: dict[str, QLabel]
    _save_locations_by_name: dict[str, SaveLocation]
    _shot_camera_widget: QWidget
    _shot_code_value: QLabel
    _shotgrid_description_field: QLineEdit
    _shotgrid_description_row: QWidget
    _shotgrid_review_combo: QComboBox
    _shotgrid_review_refresh_button: QPushButton
    _shotgrid_review_row: QWidget
    _shotgrid_upload_checkbox: QCheckBox
    _shotgrid_upload_review_checkbox: QCheckBox
    _shotgrid_upload_version_checkbox: QCheckBox
    _shotgrid_upload_target_row: QWidget
    _shot_range_value: QLabel
    _source_tabs: QTabWidget
    _validation_label: QLabel
    _use_dof: QCheckBox
    _use_hardware_fog: QCheckBox
    _use_lighting: QCheckBox
    _use_shadows: QCheckBox
    _use_ssao: QCheckBox
    _shot: Shot | None
    _custom_folder_field: QLineEdit
    _shotgrid_review_lazy_load_attempted: bool
    _shotgrid_review_load_error: str | None

    SHOT_TAB_INDEX = 0
    CUSTOM_TAB_INDEX: int

    playblaster = MPlayblaster()

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
            command=resolve_artist_display_name,
            event="SceneOpened",
            label="Artist:",
            section=5,
        )

    def __init__(
        self,
        parent: QWidget | None,
        shot_configs: list[Any] | None = None,  # Used by legacy UI, kept for sig compat
        windowTitle: str = "Playblast",
    ) -> None:
        super().__init__(parent, windowTitle=windowTitle)
        self._shot = self._resolve_pipeline_shot_context()
        self._destination_checkboxes = {}
        self._destination_path_labels = {}
        self._save_locations_by_name = {
            location.name: location for location in self._destination_locations()
        }
        self._shotgrid_review_lazy_load_attempted = False
        self._shotgrid_review_load_error = None

        self._setup_ui()
        self.SAVE_LOCS.CUSTOM._path = lambda: self._custom_folder_field.text()
        self._update_ui_state()

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

    def _build_header_section(self) -> None:
        title = QLabel(self.windowTitle())
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)

        subtitle = QLabel("Choose source mode, choose destinations, then export")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setToolTip(
            "High-level workflow: choose source, choose destinations, then export."
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
            "Validation feedback. Export is disabled until this message is cleared."
        )
        self._validation_label.setVisible(False)
        setup_layout.addWidget(self._validation_label)

        self._main_layout.addWidget(setup_group)

    def _build_export_source_section(self) -> QGroupBox:
        source_group = QGroupBox("")
        source_layout = QVBoxLayout(source_group)

        self._source_tabs = QTabWidget()

        # 1. Standard Shot Tab
        self._source_tabs.addTab(self._build_shot_source_tab(), "Shot Playblast")
        source_tab_bar = self._source_tabs.tabBar()
        source_tab_bar.setTabToolTip(
            self.SHOT_TAB_INDEX,
            "Uses shot code metadata from this Maya scene and resolved shot camera/range.",
        )

        # 2. Hook: Subclasses can inject extra tabs here (e.g. Sequencer)
        self._add_custom_tabs(self._source_tabs)

        # 3. Standard Custom Tab
        self.CUSTOM_TAB_INDEX = self._source_tabs.count()
        self._source_tabs.addTab(self._build_custom_source_tab(), "Custom Playblast")
        source_tab_bar.setTabToolTip(
            self.CUSTOM_TAB_INDEX,
            "Uses manual camera and manual frame range.",
        )

        self._source_tabs.currentChanged.connect(self._on_source_mode_changed)
        self._source_tabs.setToolTip(
            "Select how exports are generated: shot context or manual custom range."
        )

        source_layout.addWidget(self._source_tabs)

        # 4. Hook: Subclasses can inject widgets below the tabs (e.g. Anim Pass)
        extra_options = self._build_extra_source_options()
        if extra_options:
            source_layout.addWidget(extra_options)

        return source_group

    def _add_custom_tabs(self, tabs: QTabWidget) -> None:
        """Override to inject tabs between Shot and Custom."""
        pass

    def _build_extra_source_options(self) -> QWidget | None:
        """Override to add options below the tabs."""
        return None

    @abstractmethod
    def _build_shot_camera_widget(self) -> QWidget:
        """Return the camera widget for the Shot tab (QLabel or QComboBox)."""
        pass

    @abstractmethod
    def _validate_source_state(self, mode: str) -> str | None:
        """Return a validation error string for the currently selected source mode, or None."""
        pass

    def _build_shot_source_tab(self) -> QWidget:
        shot_tab = QWidget()
        shot_layout = QGridLayout(shot_tab)

        shot_layout.addWidget(QLabel("Source"), 0, 0)
        source_value = QLabel("Pipeline Shot File")
        source_value.setToolTip(
            "Source is resolved from this scene's pipeline shot metadata."
        )
        shot_layout.addWidget(source_value, 0, 1)

        shot_layout.addWidget(QLabel("Shot"), 1, 0)
        self._shot_code_value = QLabel("-")
        self._shot_code_value.setToolTip("Resolved pipeline shot code.")
        shot_layout.addWidget(self._shot_code_value, 1, 1)

        shot_layout.addWidget(QLabel("Camera"), 2, 0)
        self._shot_camera_widget = self._build_shot_camera_widget()
        shot_layout.addWidget(self._shot_camera_widget, 2, 1)

        shot_layout.addWidget(QLabel("Frame Range"), 3, 0)
        self._shot_range_value = QLabel("-")
        self._shot_range_value.setToolTip(
            "Resolved cut range from the detected pipeline shot."
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

        self._shotgrid_upload_target_row = self._build_shotgrid_upload_target_row()
        shot_layout.addWidget(self._shotgrid_upload_target_row, 5, 0, 1, 2)

        self._shotgrid_review_row = self._build_shotgrid_review_row()
        shot_layout.addWidget(self._shotgrid_review_row, 6, 0, 1, 2)

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
        shot_layout.addWidget(self._shotgrid_description_row, 7, 0, 1, 2)

        self._sync_shotgrid_description_visibility()

        return shot_tab

    def _build_shotgrid_upload_target_row(self) -> QWidget:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("Upload Options"))

        self._shotgrid_upload_version_checkbox = QCheckBox("Upload as new shot version")
        self._shotgrid_upload_version_checkbox.setChecked(True)
        self._shotgrid_upload_version_checkbox.setToolTip(
            "Create a new ShotGrid Version for this shot upload."
        )
        self._shotgrid_upload_version_checkbox.toggled.connect(
            self._on_shotgrid_upload_mode_changed
        )
        row_layout.addWidget(self._shotgrid_upload_version_checkbox)

        self._shotgrid_upload_review_checkbox = QCheckBox(
            "Upload to review for dailies"
        )
        self._shotgrid_upload_review_checkbox.setChecked(False)
        self._shotgrid_upload_review_checkbox.setToolTip(
            "Also link the uploaded Version to a review playlist."
        )
        self._shotgrid_upload_review_checkbox.toggled.connect(
            self._on_shotgrid_upload_mode_changed
        )
        row_layout.addWidget(self._shotgrid_upload_review_checkbox)

        row_layout.addStretch()
        return row_widget

    def _build_shotgrid_review_row(self) -> QWidget:
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("Review"))

        self._shotgrid_review_combo = QComboBox(self)
        self._shotgrid_review_combo.setToolTip(
            "Select the ShotGrid review playlist to link this Version to."
        )
        row_layout.addWidget(self._shotgrid_review_combo)

        self._shotgrid_review_refresh_button = QPushButton("Refresh")
        self._shotgrid_review_refresh_button.setToolTip(
            "Reload the recent ShotGrid review playlist options."
        )
        self._shotgrid_review_refresh_button.clicked.connect(
            self._on_refresh_shotgrid_reviews_clicked
        )
        row_layout.addWidget(self._shotgrid_review_refresh_button)

        self._set_review_combo_placeholder("No reviews loaded yet.")
        self._shotgrid_review_combo.currentIndexChanged.connect(
            self._on_source_settings_changed
        )
        return row_widget

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

    def _build_option_checkbox(self, label: str, enabled_by_default: bool) -> QCheckBox:
        option_toggle = QCheckBox(label)
        option_toggle.setChecked(enabled_by_default)
        option_toggle.toggled.connect(self._on_source_settings_changed)
        return option_toggle

    @staticmethod
    def _default_custom_folder_path() -> str:
        return os.getenv("TMPDIR", os.getenv("TEMP", "tmp"))

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
        self._refresh_source_tab_availability()
        self._source_tabs.setCurrentIndex(self._default_source_tab_index())

    def _refresh_source_tab_availability(self) -> None:
        has_shot_context = self._shot is not None
        self._source_tabs.setTabEnabled(self.SHOT_TAB_INDEX, has_shot_context)

        selected_mode = self._selected_source_mode()
        if selected_mode == "shot" and not has_shot_context:
            self._source_tabs.setCurrentIndex(self._default_source_tab_index())

    def _default_source_tab_index(self) -> int:
        if self._shot is not None:
            return self.SHOT_TAB_INDEX
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

    def _destination_locations(self) -> list[SaveLocation]:
        return [
            self.SAVE_LOCS.EDIT,  # type: ignore
            self.SAVE_LOCS.CURRENT,
            self.SAVE_LOCS.CUSTOM,
        ]

    def _default_destination_enabled(self, location: SaveLocation) -> bool:
        return location.name == self.SAVE_LOCS.CURRENT.name

    def _selected_source_mode(self) -> str:
        current_index = self._source_tabs.currentIndex()
        if current_index == self.SHOT_TAB_INDEX:
            return "shot"
        if getattr(self, "SEQUENCER_TAB_INDEX", -1) == current_index:
            return "sequencer"
        return "custom"

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
        edit_location = self._save_locations_by_name.get(self.SAVE_LOCS.EDIT.name)  # type: ignore
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

    def _should_upload_shot_playblast_to_shotgrid(self) -> bool:
        return (
            self._selected_source_mode() == "shot"
            and self._is_shotgrid_upload_requested()
        )

    def _upload_shot_playblast_to_shotgrid(
        self,
        config: MPlayblastConfig,
    ) -> list[str]:
        if not config.shots:
            return ["ShotGrid Upload: Skipped - no shot output was generated."]

        shot_code = str(config.shots[0].shot.code or "").strip()
        if not shot_code:
            return ["ShotGrid Upload: Skipped - shot code is missing."]

        movie_path = self._resolve_shotgrid_upload_movie_path(config)
        if movie_path is None:
            return [
                "ShotGrid Upload: Skipped - no valid playblast movie file was found."
            ]

        version_name = default_version_name_from_movie_path(movie_path)
        if not version_name:
            version_name = f"{shot_code}_playblast"

        artist_name = resolve_artist_display_name().strip() or None
        upload_target = self._shotgrid_upload_target()
        review_playlist_id = (
            self._selected_shotgrid_review_playlist_id()
            if upload_target == UPLOAD_TARGET_REVIEW
            else None
        )
        selected_review_playlist_id = self._selected_shotgrid_review_playlist_id()
        fallback_reason = self._shotgrid_review_fallback_reason_for_upload()
        pre_upload_warning = self._shotgrid_review_fallback_warning_for_upload()

        upload_request = PlayblastVersionUploadRequest(
            shot_code=shot_code,
            movie_path=movie_path,
            version_name=version_name,
            description=self._shotgrid_upload_description() or None,
            path_to_frames=str(movie_path),
            artist_display_name=artist_name,
            upload_target=upload_target,
            review_playlist_id=review_playlist_id,
        )

        try:
            upload_result = upload_playblast_version(upload_request)
        except Exception as exc:
            log.exception("ShotGrid upload failed for shot '%s'", shot_code)
            return [f"ShotGrid Upload: Failed - {exc}"]

        message_lines: list[str] = []
        if upload_result.ok:
            success_message = (
                f"ShotGrid Upload: Success - {upload_result.version_name}"
                f" (shot {upload_result.shot_code})."
            )
            if upload_result.version_id is not None:
                success_message = (
                    f"{success_message} Version ID: {upload_result.version_id}."
                )
            message_lines.append(success_message)
        else:
            message_lines.append(f"ShotGrid Upload: Failed - {upload_result.message}")

        if pre_upload_warning and upload_result.ok:
            message_lines.append(f"ShotGrid Warning: {pre_upload_warning}")
        if pre_upload_warning:
            log.warning(
                "ShotGrid review upload fallback to version upload "
                "(shot_code=%s, version_id=%s, playlist_id=%s, reason=%s)",
                shot_code,
                upload_result.version_id,
                selected_review_playlist_id,
                fallback_reason or "review list unavailable",
            )
        for warning in upload_result.warnings:
            message_lines.append(f"ShotGrid Warning: {warning}")

        return message_lines

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

    def _sync_custom_path_row_visibility(self) -> None:
        is_visible = self._is_custom_destination_selected()
        self._custom_folder_row.setVisible(is_visible)
        self._custom_folder_field.setEnabled(is_visible)

    def _sync_shotgrid_description_visibility(self) -> None:
        show_description = self._is_shotgrid_upload_requested()
        self._shotgrid_description_row.setVisible(show_description)
        self._shotgrid_description_field.setEnabled(show_description)

    def _sync_shotgrid_upload_target_visibility(self) -> None:
        show_target = self._is_shotgrid_upload_requested()
        self._shotgrid_upload_target_row.setVisible(show_target)
        self._shotgrid_upload_version_checkbox.setEnabled(show_target)
        self._shotgrid_upload_review_checkbox.setEnabled(show_target)

    def _sync_shotgrid_review_visibility(self) -> None:
        show_review = (
            self._is_shotgrid_upload_requested()
            and self._is_shotgrid_review_upload_enabled()
        )
        self._shotgrid_review_row.setVisible(show_review)
        self._shotgrid_review_combo.setEnabled(show_review)
        self._shotgrid_review_refresh_button.setEnabled(show_review)

    def _is_shotgrid_upload_requested(self) -> bool:
        return self._shotgrid_upload_checkbox.isChecked()

    def _shotgrid_upload_target(self) -> str:
        if self._can_upload_to_selected_shotgrid_review():
            return UPLOAD_TARGET_REVIEW
        return UPLOAD_TARGET_VERSION_ONLY

    def _is_shotgrid_version_upload_enabled(self) -> bool:
        return self._shotgrid_upload_version_checkbox.isChecked()

    def _is_shotgrid_review_upload_enabled(self) -> bool:
        return self._shotgrid_upload_review_checkbox.isChecked()

    def _selected_shotgrid_review_playlist_id(self) -> int | None:
        selected = self._shotgrid_review_combo.currentData()
        if isinstance(selected, int) and selected > 0:
            return selected
        return None

    def _can_upload_to_selected_shotgrid_review(self) -> bool:
        return (
            self._is_shotgrid_review_upload_enabled()
            and self._selected_shotgrid_review_playlist_id() is not None
        )

    def _shotgrid_review_fallback_warning_for_upload(self) -> str | None:
        fallback_reason = self._shotgrid_review_fallback_reason_for_upload()
        if fallback_reason is None:
            return None
        return (
            "Review upload skipped because recent reviews could not be loaded. "
            "Version upload continued."
        )

    def _shotgrid_review_fallback_reason_for_upload(self) -> str | None:
        if not self._is_shotgrid_upload_requested():
            return None
        if not self._is_shotgrid_version_upload_enabled():
            return None
        if not self._is_shotgrid_review_upload_enabled():
            return None
        if self._selected_shotgrid_review_playlist_id() is not None:
            return None
        return self._shotgrid_review_load_error

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
        self._shotgrid_review_lazy_load_attempted = True
        previous_playlist_id = self._selected_shotgrid_review_playlist_id()

        try:
            review_options = list_recent_review_playlists(limit=10)
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

    def _shotgrid_upload_description(self) -> str:
        return self._shotgrid_description_field.text().strip()

    def _validate_target_destination_state(self) -> str | None:
        mode = self._selected_source_mode()

        if mode == "shot":
            if self._shot is None:
                return (
                    "No pipeline shot context was found. Use a pipeline shot file "
                    "or switch to Custom Playblast."
                )

        if mode == "custom":
            if self._custom_out.value() < self._custom_in.value():
                return "Custom Out must be greater than or equal to Custom In."
            if not str(self._custom_camera.currentText()).strip():
                return "Choose a camera for Custom Playblast."

        # Let subclasses run their own validation
        subclass_error = self._validate_source_state(mode)
        if subclass_error:
            return subclass_error

        if not self._selected_destination_locations():
            return "Select at least one save destination."

        if (
            self._is_custom_destination_selected()
            and not self._custom_folder_field.text().strip()
        ):
            return "Custom Folder path is required when Custom Folder destination is enabled."

        if (
            mode == "shot"
            and self._is_shotgrid_upload_requested()
            and not self._is_shotgrid_version_upload_enabled()
            and not self._is_shotgrid_review_upload_enabled()
        ):
            return (
                "Select at least one ShotGrid upload option: 'Upload as new shot "
                "version' or 'Upload to review for dailies'."
            )

        if (
            mode == "shot"
            and self._is_shotgrid_upload_requested()
            and self._is_shotgrid_review_upload_enabled()
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
        # Allows subclasses to hook into the update state to refresh their custom fields
        self._refresh_custom_ui_state()
        self._sync_custom_path_row_visibility()
        if (
            self._is_shotgrid_upload_requested()
            and self._is_shotgrid_review_upload_enabled()
        ):
            self._ensure_shotgrid_reviews_loaded_lazily()
        self._sync_shotgrid_upload_target_visibility()
        self._sync_shotgrid_review_visibility()
        self._sync_shotgrid_description_visibility()
        self._refresh_destination_path_labels()
        self._update_action_state()

    def _refresh_custom_ui_state(self) -> None:
        """Override to update subclass specific UI fields"""
        pass

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

    def _on_shotgrid_upload_mode_changed(self, _enabled: bool) -> None:
        self._update_ui_state()

    def _on_refresh_shotgrid_reviews_clicked(self) -> None:
        self._load_shotgrid_reviews(force_refresh=True)
        self._update_ui_state()

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

    @abstractmethod
    def _generate_config(self) -> MPlayblastConfig:
        raise NotImplementedError

    def _validate_config(self, config: MPlayblastConfig) -> str | None:
        validation_error = self._validate_target_destination_state()
        if validation_error:
            return validation_error

        if not config.shots:
            return "No playblast targets are configured."

        for shot_cfg in config.shots:
            output_count = sum(len(paths) for paths in shot_cfg.paths.values())
            if output_count < 1:
                return (
                    f"Target '{shot_cfg.shot.code}' has no output location selected. "
                    "Please enable at least one destination."
                )
        return None

    def _after_local_playblast(
        self,
        config: MPlayblastConfig,
    ) -> list[str]:
        """Run optional tool-specific actions after local playblast succeeds.

        Returned lines are appended to the success dialog.
        """
        if not self._should_upload_shot_playblast_to_shotgrid():
            return []
        return self._upload_shot_playblast_to_shotgrid(config)

    @staticmethod
    def _collect_output_paths(config: MPlayblastConfig) -> list[str]:
        output_paths: list[str] = []
        for shot_cfg in config.shots:
            for preset, bases in shot_cfg.paths.items():
                for base in bases:
                    output_paths.append(str(Path(str(base) + f".{preset.ext}")))
        return output_paths

    @staticmethod
    def _build_success_message(
        output_paths: list[str],
        post_playblast_messages: list[str],
    ) -> str:
        message_lines = ["Local playblast export successful."]
        if output_paths:
            message_lines.append("")
            message_lines.append("Outputs:")
            message_lines.extend(output_paths)
        if post_playblast_messages:
            message_lines.append("")
            message_lines.append("Post-export:")
            message_lines.extend(post_playblast_messages)
        return "\n".join(message_lines)

    def do_export(self) -> None:
        try:
            config = self._generate_config()
        except Exception as exc:
            log.exception("Playblast config generation failed")
            MessageDialog(
                self,
                f"Could not generate playblast settings.\n\n{exc}",
                "Playblast Error",
            ).exec_()
            return

        validation_error = self._validate_config(config)
        if validation_error:
            MessageDialog(self, validation_error, "Playblast").exec_()
            return

        try:
            self.playblaster.configure(config).playblast()
        except Exception as exc:
            log.exception("Playblast export failed")
            MessageDialog(
                self,
                f"Playblast failed.\n\n{exc}",
                "Playblast Error",
            ).exec_()
            return

        post_playblast_messages: list[str] = []
        try:
            post_playblast_messages = self._after_local_playblast(config)
        except Exception as exc:
            log.exception("Post-playblast actions failed")
            post_playblast_messages = [
                "Post-export actions failed. Local playblast files were still written.",
                f"Reason: {exc}",
            ]

        output_paths = self._collect_output_paths(config)
        success_msg = self._build_success_message(output_paths, post_playblast_messages)
        MessageDialog(self, success_msg).exec_()
        self.close()

    def _build_custom_playblast_config(self) -> MShotPlayblastConfig:
        custom_in = self._custom_in.value()
        custom_out = self._custom_out.value()
        custom_code = self._scene_stem()
        output_name = self._resolve_output_name(
            f"{custom_code}_custom"
            if self._selected_source_mode() == "custom" and custom_code != "custom"
            else f"customPB_{self._shot.code}"
            if self._shot
            else "customPB"
        )

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

    @staticmethod
    def _scene_stem() -> str:
        scene_name = Path(str(mc.file(query=True, sceneName=True) or "")).stem
        return scene_name or "playblast"
