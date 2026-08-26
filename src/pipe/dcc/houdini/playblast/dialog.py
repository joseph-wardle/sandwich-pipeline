"""Render-only Houdini playblast dialog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import attrs
import hou
from Qt import QtCore, QtWidgets

from pipe.core.playblast import (
    CURRENT_FOLDER_ID,
    CURRENT_FOLDER_NAME,
    EDIT_FOLDER_ID,
    EDIT_FOLDER_NAME,
    Destination,
    DiskDestination,
    FFmpegPreset,
    PreviewClip,
    ReviewEntity,
    ShotGridDestination,
    custom_folder_destination,
    shot_or_scratch,
)
from pipe.core.playblast.naming import build_edit_output_directory
from pipe.core.shotgrid import ShotGridError
from pipe.core.ui import FAIL_STYLE, DialogButtons, set_tab_available

if TYPE_CHECKING:
    from pipe.core.shotgrid import Shot, ShotGrid

log = logging.getLogger(__name__)


SOURCE_MODE = Literal["shot", "custom"]

# fx is the only department whose playblasts feed editorial.
EDIT_DEPARTMENT = "fx"

_SHOT_TAB_TIP = "Uses this file's shot camera and the ShotGrid cut range."
_NO_SHOT_CONTEXT_TIP = "This .hip file is not in a shot. Use Custom Playblast instead."


class HPlayblastDialog(QtWidgets.QDialog, DialogButtons):
    SHOT_TAB_INDEX = 0
    CUSTOM_TAB_INDEX = 1

    CURRENT_VIEWPORT_CAMERA_TOKEN = "__current_viewport_camera__"

    # Key for the viewer's per-tool destination-toggle memory.
    SETTINGS_KEY = "houdini_shot"

    _conn: ShotGrid
    _custom_camera: QtWidgets.QComboBox
    _custom_in: QtWidgets.QSpinBox
    _custom_out: QtWidgets.QSpinBox
    _default_shot_code: str
    _main_layout: QtWidgets.QVBoxLayout
    _shot: Shot | None
    _shot_camera_value: QtWidgets.QLabel
    _shot_code_value: QtWidgets.QLabel
    _shot_range_value: QtWidgets.QLabel
    _source_tabs: QtWidgets.QTabWidget
    _validation_label: QtWidgets.QLabel

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        conn: "ShotGrid",
        default_shot_code: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._default_shot_code = (default_shot_code or "").strip()
        self._shot = self._resolve_shot_context(self._default_shot_code)

        self._init_buttons(True, "Playblast Shot", "Cancel")
        self.setWindowTitle("Houdini Playblast")

        self._setup_ui()
        self._wire_ui_signals()
        self._set_default_source_tab()
        self._update_ui_state()

    @property
    def shot(self) -> Shot | None:
        """The shot resolved at construction, or None for a non-shot file."""
        return self._shot

    @property
    def shot_code(self) -> str:
        if self._shot is None:
            return ""
        return (self._shot.code or "").strip()

    @property
    def selected_source_mode(self) -> SOURCE_MODE:
        if self._source_tabs.currentIndex() == self.SHOT_TAB_INDEX:
            return "shot"
        return "custom"

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

    # ------------------------------------------------------------------
    # UI assembly
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self._main_layout = QtWidgets.QVBoxLayout(self)
        self._build_header_section()
        self._build_source_section()
        self._build_buttons()

    def _wire_ui_signals(self) -> None:
        self._source_tabs.currentChanged.connect(self._on_ui_input_changed)
        self._custom_camera.currentTextChanged.connect(self._on_ui_input_changed)
        self._custom_out.valueChanged.connect(self._on_ui_input_changed)
        self._custom_in.valueChanged.connect(self._on_custom_in_changed)

    def _build_header_section(self) -> None:
        title = QtWidgets.QLabel("Houdini Playblast")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)

        subtitle = QtWidgets.QLabel("Playblast, then pick destinations in the viewer")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setToolTip(
            "The playblast opens in the viewer; nothing is saved or uploaded "
            "until you confirm destinations there."
        )

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_source_section(self) -> None:
        source_group = QtWidgets.QGroupBox("Source")
        source_layout = QtWidgets.QVBoxLayout(source_group)

        self._source_tabs = self._build_source_tabs()
        source_layout.addWidget(self._source_tabs)

        self._validation_label = QtWidgets.QLabel()
        self._validation_label.setStyleSheet(FAIL_STYLE)
        self._validation_label.setVisible(False)
        source_layout.addWidget(self._validation_label)

        self._main_layout.addWidget(source_group)

    def _build_source_tabs(self) -> QtWidgets.QTabWidget:
        source_tabs = QtWidgets.QTabWidget()
        source_tabs.addTab(self._build_shot_source_tab(), "Shot Playblast")
        source_tabs.addTab(self._build_custom_source_tab(), "Custom Playblast")

        tab_bar = source_tabs.tabBar()
        tab_bar.setTabToolTip(
            self.CUSTOM_TAB_INDEX,
            "Uses manual camera and frame range for non-shot testing or exploratory output.",
        )
        return source_tabs

    def _build_shot_source_tab(self) -> QtWidgets.QWidget:
        shot_tab = QtWidgets.QWidget()
        shot_layout = QtWidgets.QGridLayout(shot_tab)

        shot_layout.addWidget(QtWidgets.QLabel("Source"), 0, 0)
        source_value = QtWidgets.QLabel("Pipeline Shot Context")
        source_value.setToolTip("Shot mode uses shot context detected from the scene.")
        shot_layout.addWidget(source_value, 0, 1)

        shot_layout.addWidget(QtWidgets.QLabel("Shot"), 1, 0)
        self._shot_code_value = self._build_value_label("Detected shot code.")
        shot_layout.addWidget(self._shot_code_value, 1, 1)

        shot_layout.addWidget(QtWidgets.QLabel("Camera"), 2, 0)
        self._shot_camera_value = self._build_value_label(
            "Viewport camera currently used by capture."
        )
        shot_layout.addWidget(self._shot_camera_value, 2, 1)

        shot_layout.addWidget(QtWidgets.QLabel("Frame Range"), 3, 0)
        self._shot_range_value = self._build_value_label(
            "ShotGrid cut range for the detected shot."
        )
        shot_layout.addWidget(self._shot_range_value, 3, 1)

        return shot_tab

    @staticmethod
    def _build_value_label(tooltip: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel("-")
        label.setToolTip(tooltip)
        return label

    def _build_custom_source_tab(self) -> QtWidgets.QWidget:
        custom_tab = QtWidgets.QWidget()
        custom_layout = QtWidgets.QGridLayout(custom_tab)

        custom_layout.addWidget(QtWidgets.QLabel("Source"), 0, 0)
        source_value = QtWidgets.QLabel("Manual Custom Settings")
        source_value.setToolTip(
            "Custom mode is intended for testing and non-shot scene playblasts."
        )
        custom_layout.addWidget(source_value, 0, 1, 1, 3)

        timeline_in, timeline_out = self._timeline_range()
        self._custom_in = QtWidgets.QSpinBox(self, minimum=-100000, maximum=100000)
        self._custom_out = QtWidgets.QSpinBox(self, minimum=-100000, maximum=100000)
        self._custom_in.setValue(timeline_in)
        self._custom_out.setValue(timeline_out)
        self._custom_out.setMinimum(self._custom_in.value())
        self._custom_in.setToolTip("Custom start frame for this playblast.")
        self._custom_out.setToolTip("Custom end frame for this playblast.")

        custom_layout.addWidget(QtWidgets.QLabel("Custom In"), 1, 0)
        custom_layout.addWidget(self._custom_in, 1, 1)
        custom_layout.addWidget(QtWidgets.QLabel("Custom Out"), 1, 2)
        custom_layout.addWidget(self._custom_out, 1, 3)

        custom_layout.addWidget(QtWidgets.QLabel("Camera"), 2, 0)
        self._custom_camera = QtWidgets.QComboBox()
        self._populate_custom_camera_options()
        self._custom_camera.setToolTip("Camera used for custom mode playblast capture.")
        custom_layout.addWidget(self._custom_camera, 2, 1, 1, 3)

        return custom_tab

    def _build_buttons(self) -> None:
        ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setToolTip("Render the playblast and open it in the viewer.")

        cancel_button = self.buttons.button(QtWidgets.QDialogButtonBox.Cancel)
        if cancel_button is not None:
            cancel_button.setToolTip("Close without rendering.")

        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._main_layout.addWidget(self.buttons)

    # ------------------------------------------------------------------
    # Scene / ShotGrid context
    # ------------------------------------------------------------------

    def _resolve_shot_context(self, shot_code: str) -> Shot | None:
        if not shot_code:
            return None
        try:
            return self._conn.get_shot(code=shot_code)
        except ShotGridError:
            log.warning(
                "Could not resolve ShotGrid shot for code '%s'; "
                "playblast dialog will start in Custom mode.",
                shot_code,
                exc_info=True,
            )
            return None

    def _set_default_source_tab(self) -> None:
        has_shot_context = self._shot is not None
        set_tab_available(
            self._source_tabs,
            self.SHOT_TAB_INDEX,
            available=has_shot_context,
            tooltip=_SHOT_TAB_TIP,
            reason=_NO_SHOT_CONTEXT_TIP,
        )
        default_index = (
            self.SHOT_TAB_INDEX if has_shot_context else self.CUSTOM_TAB_INDEX
        )
        self._source_tabs.setCurrentIndex(default_index)

    # ------------------------------------------------------------------
    # Validation + UI state
    # ------------------------------------------------------------------

    def _update_ui_state(self) -> None:
        self._refresh_shot_context_fields()
        self._update_action_state()

    def _refresh_shot_context_fields(self) -> None:
        if self._shot is None:
            self._shot_code_value.setText(self._default_shot_code or "-")
            self._shot_range_value.setText("-")
        else:
            self._shot_code_value.setText(self._shot.code or "-")
            self._shot_range_value.setText(
                f"{self._shot.cut_in} - {self._shot.cut_out}"
            )

        self._shot_camera_value.setText(self._current_viewport_camera_label())

    def _update_action_state(self) -> None:
        ok_button = self.buttons.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button is None:
            return

        ok_button.setText(self._action_button_text())
        validation_error = self._validate_source_state()
        ok_button.setEnabled(validation_error is None)
        self._validation_label.setText(validation_error or "")
        self._validation_label.setVisible(validation_error is not None)

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

    def _action_button_text(self) -> str:
        if self.selected_source_mode == "shot":
            return "Playblast Shot"
        return "Playblast Custom"

    # ------------------------------------------------------------------
    # Routing for the viewer's Confirm panel
    # ------------------------------------------------------------------

    def routed_clip(self, clip: PreviewClip) -> PreviewClip:
        """Attach this dialog's routing data for the viewer's Confirm panel."""
        return attrs.evolve(
            clip,
            output_prefix=self._clip_output_prefix(),
            settings_key=self.SETTINGS_KEY,
            destinations=self._clip_destinations(),
        )

    def _clip_destinations(self) -> tuple[Destination, ...]:
        return (
            *self._clip_folders(),
            ShotGridDestination(entity=self._review_entity()),
        )

    def _clip_folders(self) -> tuple[DiskDestination, ...]:
        return (
            DiskDestination(
                id=EDIT_FOLDER_ID,
                name=EDIT_FOLDER_NAME,
                directory=build_edit_output_directory(EDIT_DEPARTMENT),
                preset=FFmpegPreset.EDIT_SQ,
            ),
            DiskDestination(
                id=CURRENT_FOLDER_ID,
                name=CURRENT_FOLDER_NAME,
                directory=self._current_scene_directory(),
                preset=FFmpegPreset.WEB,
                default_on=False,
            ),
            custom_folder_destination(),
        )

    def _review_entity(self) -> ReviewEntity:
        """What this playblast's ShotGrid Version attaches to."""
        code = self.shot_code if self.selected_source_mode == "shot" else ""
        return shot_or_scratch(code, self.custom_shot_code)

    def _clip_output_prefix(self) -> str:
        if self.selected_source_mode == "shot":
            return self.shot_code or "playblast"
        scene_stem = self._scene_stem()
        return f"{scene_stem or 'playblast'}_custom"

    # ------------------------------------------------------------------
    # hou queries
    # ------------------------------------------------------------------

    @staticmethod
    def _current_scene_directory() -> Path:
        try:
            return Path(hou.hipFile.path()).expanduser().resolve().parent
        except Exception:
            return Path.cwd()

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
