"""Render-only Maya playblast dialog."""

from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
import maya.cmds as mc
from Qt import QtCore, QtWidgets
from Qt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast import (
    Destination,
    DiskDestination,
    PreviewClip,
    ReviewEntity,
    ShotGridDestination,
    shot_or_scratch,
)
from pipe.core.playblast.viewer import open_viewer
from pipe.core.shotgrid import ShotGrid, ShotGridError
from pipe.core.ui import FAIL_STYLE, ButtonPair, MessageDialog, set_tab_available
from pipe.dcc.maya.playblast.shot.config import (
    MPlayblastConfig,
    MShotPlayblastConfig,
    dummy_shot,
)
from pipe.dcc.maya.playblast.shot.playblaster import MPlayblaster
from pipe.dcc.maya.runtime import get_main_qt_window

if TYPE_CHECKING:
    from pipe.core.shotgrid import Shot

log = logging.getLogger(__name__)

_MODE_SHOT = "shot"
_MODE_CUSTOM = "custom"

_SHOT_TAB_TIP = "Uses this scene's shot code, camera, and frame range."
_NO_SHOT_CONTEXT_TIP = (
    "This scene has no shot code. Open a shot file, or use Custom Playblast."
)


class MPlayblastDialog(ButtonPair, QtWidgets.QMainWindow):
    """Shared render-only playblast dialog: pick a source, pick viewport
    options, render. Encodes, folder copies, and ShotGrid uploads all happen
    later in the viewer, on Confirm."""

    _central_widget: QWidget
    _main_layout: QVBoxLayout
    _custom_camera: QComboBox
    _custom_in: QSpinBox
    _custom_out: QSpinBox
    _shot: Shot | None
    _shot_camera_widget: QWidget
    _shot_code_value: QLabel
    _shot_range_value: QLabel
    _source_tabs: QTabWidget
    _validation_label: QLabel
    _use_dof: QCheckBox
    _use_hardware_fog: QCheckBox
    _use_lighting: QCheckBox
    _use_shadows: QCheckBox
    _use_ssao: QCheckBox

    SHOT_TAB_INDEX = 0
    CUSTOM_TAB_INDEX: int

    # Key for the viewer's per-tool destination-toggle memory.
    SETTINGS_KEY = ""

    playblaster = MPlayblaster()

    def __init__(self, parent: QWidget | None, windowTitle: str = "Playblast") -> None:
        super().__init__(parent, windowTitle=windowTitle)
        self._shot = self._resolve_pipeline_shot_context()
        self._setup_ui()
        self._update_ui_state()

    # ------------------------------------------------------------------
    # UI assembly
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)
        self._main_layout = QVBoxLayout()
        self._central_widget.setLayout(self._main_layout)

        self._build_header_section()
        self._build_source_section()
        self._build_viewport_options_section()
        self._build_buttons()
        self._set_default_source_tab()

    def _build_header_section(self) -> None:
        title = QLabel(self.windowTitle())
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(QtCore.Qt.AlignCenter)

        subtitle = QLabel("Playblast, then pick destinations in the viewer")
        subtitle.setAlignment(QtCore.Qt.AlignCenter)
        subtitle.setToolTip(
            "The playblast opens in the viewer; nothing is saved or uploaded "
            "until you confirm destinations there."
        )

        self._main_layout.addWidget(title)
        self._main_layout.addWidget(subtitle)

    def _build_source_section(self) -> None:
        source_group = QGroupBox("Source")
        source_layout = QVBoxLayout(source_group)

        self._source_tabs = QTabWidget()
        self._source_tabs.addTab(self._build_shot_source_tab(), "Shot Playblast")
        source_tab_bar = self._source_tabs.tabBar()

        self.CUSTOM_TAB_INDEX = self._source_tabs.count()
        self._source_tabs.addTab(self._build_custom_source_tab(), "Custom Playblast")
        source_tab_bar.setTabToolTip(
            self.CUSTOM_TAB_INDEX,
            "Uses manual camera and manual frame range.",
        )

        self._source_tabs.currentChanged.connect(self._on_source_mode_changed)
        source_layout.addWidget(self._source_tabs)

        extra_options = self._build_extra_source_options()
        if extra_options:
            source_layout.addWidget(extra_options)

        self._validation_label = QLabel()
        self._validation_label.setStyleSheet(FAIL_STYLE)
        self._validation_label.setVisible(False)
        source_layout.addWidget(self._validation_label)

        self._main_layout.addWidget(source_group)

    def _build_extra_source_options(self) -> QWidget | None:
        """Override to add options below the tabs."""
        return None

    @abstractmethod
    def _build_shot_camera_widget(self) -> QWidget:
        """Return the camera widget for the Shot tab (QLabel or QComboBox)."""
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

    def _build_viewport_options_section(self) -> None:
        options_group = QGroupBox("Viewport Options")
        options_layout = QHBoxLayout(options_group)

        active_panel = self._resolve_active_model_panel()
        self._use_lighting = self._build_option_checkbox(
            "Use Lighting",
            self._query_lighting(active_panel),
            "Use viewport lighting for playblast capture.",
        )
        self._use_shadows = self._build_option_checkbox(
            "Use Shadows",
            self._query_shadows(active_panel),
            "Render viewport shadows in playblast.",
        )
        self._use_ssao = self._build_option_checkbox(
            "Use Ambient Occlusion",
            self._query_ssao(),
            "Shade viewport contact areas with screen-space ambient occlusion.",
        )
        self._use_hardware_fog = self._build_option_checkbox(
            "Use Hardware Fog",
            self._query_hardware_fog(active_panel),
            "Include hardware fog from viewport settings.",
        )
        self._use_dof = self._build_option_checkbox(
            "Use DoF",
            self._query_dof(active_panel),
            "Include camera depth of field in playblast.",
        )
        for checkbox in (
            self._use_lighting,
            self._use_shadows,
            self._use_ssao,
            self._use_hardware_fog,
            self._use_dof,
        ):
            options_layout.addWidget(checkbox)

        self._main_layout.addWidget(options_group)

    def _build_option_checkbox(
        self, label: str, enabled_by_default: bool, tooltip: str
    ) -> QCheckBox:
        option_toggle = QCheckBox(label)
        option_toggle.setChecked(enabled_by_default)
        option_toggle.setToolTip(tooltip)
        return option_toggle

    def _build_buttons(self) -> None:
        self._init_buttons(has_cancel_button=True, ok_name="Playblast Shot")
        self.buttons.rejected.connect(self.close)
        self.buttons.accepted.connect(self.do_export)

        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setToolTip("Render the playblast and open it in the viewer.")

        cancel_button = self.buttons.button(QDialogButtonBox.Cancel)
        if cancel_button is not None:
            cancel_button.setToolTip("Close without rendering.")

        self._main_layout.addWidget(self.buttons)

    def _set_default_source_tab(self) -> None:
        self._refresh_source_tab_availability()
        self._source_tabs.setCurrentIndex(self._default_source_tab_index())

    def _refresh_source_tab_availability(self) -> None:
        has_shot_context = self._shot is not None
        set_tab_available(
            self._source_tabs,
            self.SHOT_TAB_INDEX,
            available=has_shot_context,
            tooltip=_SHOT_TAB_TIP,
            reason=_NO_SHOT_CONTEXT_TIP,
        )
        if self._selected_source_mode() == _MODE_SHOT and not has_shot_context:
            self._source_tabs.setCurrentIndex(self._default_source_tab_index())

    def _default_source_tab_index(self) -> int:
        if self._shot is not None:
            return self.SHOT_TAB_INDEX
        return self.CUSTOM_TAB_INDEX

    # ------------------------------------------------------------------
    # Scene / ShotGrid context
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_pipeline_shot_context() -> Shot | None:
        # env_sg holds gitignored production credentials; import lazily so
        # importing this module never requires them.
        from env_sg import DB_Config

        try:
            conn = ShotGrid.connect(DB_Config)
        except ShotGridError:
            log.warning(
                "Could not connect to ShotGrid; playblast dialog will start in Custom mode.",
                exc_info=True,
            )
            return None

        # Non-pipeline scenes have no `code` fileInfo entry; treat as Custom.
        code_values = mc.fileInfo("code", query=True) or []
        if not code_values:
            return None
        code = str(code_values[0]).strip()
        if not code:
            return None

        try:
            return conn.get_shot(code=code)
        except ShotGridError:
            log.warning(
                "Could not resolve ShotGrid shot for fileInfo code '%s'; "
                "playblast dialog will start in Custom mode.",
                code,
                exc_info=True,
            )
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
    def _scene_stem() -> str:
        scene_name = Path(str(mc.file(query=True, sceneName=True) or "")).stem
        return scene_name or "playblast"

    def _selected_source_mode(self) -> str:
        if self._source_tabs.currentIndex() == self.SHOT_TAB_INDEX:
            return _MODE_SHOT
        return _MODE_CUSTOM

    # ------------------------------------------------------------------
    # Validation + UI state
    # ------------------------------------------------------------------

    @abstractmethod
    def _validate_source_state(self, mode: str) -> str | None:
        """Return a validation error for the selected source mode, or None."""
        pass

    def _validate_export_state(self) -> str | None:
        mode = self._selected_source_mode()

        if mode == _MODE_SHOT and self._shot is None:
            return (
                "No pipeline shot context was found. Use a pipeline shot file "
                "or switch to Custom Playblast."
            )

        if mode == _MODE_CUSTOM:
            if self._custom_out.value() < self._custom_in.value():
                return "Custom Out must be greater than or equal to Custom In."
            if not str(self._custom_camera.currentText()).strip():
                return "Choose a camera for Custom Playblast."

        return self._validate_source_state(mode)

    def _action_button_text(self) -> str:
        if self._selected_source_mode() == _MODE_SHOT:
            return "Playblast Shot"
        return "Playblast Custom"

    def _update_action_state(self) -> None:
        ok_button = self.buttons.button(QDialogButtonBox.Ok)
        if ok_button is None:
            return

        ok_button.setText(self._action_button_text())
        validation_error = self._validate_export_state()
        ok_button.setEnabled(validation_error is None)
        self._validation_label.setText(validation_error or "")
        self._validation_label.setVisible(validation_error is not None)

    def _update_ui_state(self) -> None:
        self._refresh_source_tab_availability()
        self._refresh_shot_context_fields()
        self._refresh_custom_ui_state()
        self._update_action_state()

    def _refresh_shot_context_fields(self) -> None:
        if self._shot is None:
            self._shot_code_value.setText("-")
            self._shot_range_value.setText("-")
            return
        self._shot_code_value.setText(self._shot.code or "-")
        self._shot_range_value.setText(f"{self._shot.cut_in} - {self._shot.cut_out}")

    def _refresh_custom_ui_state(self) -> None:
        """Override to update subclass-specific UI fields."""
        pass

    def _on_source_mode_changed(self, _index: int) -> None:
        self._update_ui_state()

    def _on_custom_in_changed(self, in_frame: int) -> None:
        self._custom_out.setMinimum(in_frame)
        self._update_ui_state()

    def _on_source_settings_changed(self, *_args: object) -> None:
        self._update_ui_state()

    # ------------------------------------------------------------------
    # Viewport option queries
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Config generation
    # ------------------------------------------------------------------

    @abstractmethod
    def _generate_config(self) -> MPlayblastConfig:
        raise NotImplementedError

    def _build_custom_playblast_config(self) -> MShotPlayblastConfig:
        custom_in = self._custom_in.value()
        custom_out = self._custom_out.value()
        return MShotPlayblastConfig(
            camera=str(self._custom_camera.currentText()),
            shot=dummy_shot(
                code=self._scene_stem(),
                cut_in=custom_in,
                cut_out=custom_out,
                cut_duration=max(0, custom_out - custom_in),
            ),
        )

    # ------------------------------------------------------------------
    # Routing for the viewer's Confirm panel
    # ------------------------------------------------------------------

    @abstractmethod
    def _clip_folders(self) -> tuple[DiskDestination, ...]:
        """Folder rows the viewer's Confirm panel offers for this tool."""
        raise NotImplementedError

    def _clip_shotgrid(self) -> ShotGridDestination:
        return ShotGridDestination(entity=self._review_entity())

    def _review_entity(self) -> ReviewEntity:
        """What this playblast's ShotGrid Version attaches to."""
        return shot_or_scratch(self._review_shot_code(), self._scene_stem())

    def _review_shot_code(self) -> str:
        """The pipeline shot this playblast reviews, or "" for a scratch scene."""
        if self._selected_source_mode() != _MODE_SHOT or self._shot is None:
            return ""
        return (self._shot.code or "").strip()

    def _clip_destinations(self) -> tuple[Destination, ...]:
        return (*self._clip_folders(), self._clip_shotgrid())

    def _clip_output_prefix(self) -> str:
        """Basename prefix Confirm versions filenames from (`<prefix>_<date>.v###`)."""
        if self._selected_source_mode() == _MODE_SHOT and self._shot is not None:
            return self._shot.code or "playblast"
        return f"{self._scene_stem()}_custom"

    def _clip_settings_key(self) -> str:
        """Key for the viewer's destination-toggle memory. Override to give a
        source mode its own remembered toggles."""
        return self.SETTINGS_KEY

    def _routed_clip(self, clip: PreviewClip) -> PreviewClip:
        return attrs.evolve(
            clip,
            output_prefix=self._clip_output_prefix(),
            settings_key=self._clip_settings_key(),
            destinations=self._clip_destinations(),
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def do_export(self) -> None:
        """Render the preview frames and open the viewer on them. Nothing
        persists here — that happens in the viewer, on Confirm."""
        validation_error = self._validate_export_state()
        if validation_error:
            MessageDialog(self, validation_error, "Playblast").exec_()
            return

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

        try:
            clips = self.playblaster.configure(config).playblast()
        except Exception as exc:
            log.exception("Playblast export failed")
            MessageDialog(
                self, f"Playblast failed.\n\n{exc}", "Playblast Error"
            ).exec_()
            return

        if not clips:
            MessageDialog(self, "Nothing was rendered.", "Playblast").exec_()
            return

        open_viewer(
            [self._routed_clip(clip) for clip in clips],
            parent=get_main_qt_window(),
        )
        self.close()
