"""SKD Previs Playblast dialog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import maya.cmds as mc
from Qt.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QTabWidget,
    QWidget,
)

from pipe.core.playblast import (
    CURRENT_FOLDER_ID,
    CUSTOM_FOLDER_ID,
    DiskDestination,
    FFmpegPreset,
    ShotGridDestination,
)
from pipe.core.playblast.tempdir import resolve_playblast_tempdir
from pipe.core.playblast.viewer import open_viewer
from pipe.core.shot import maya_rlo_stream, shot_owner_for
from pipe.core.shotgrid import Shot
from pipe.core.ui import MessageDialog
from pipe.core.versioning import current_version_label
from pipe.dcc.maya.playblast.previs.sequence import (
    MSequenceConfig,
    MSequencePlayblaster,
)
from pipe.dcc.maya.playblast.shot.config import (
    MPlayblastConfig,
    MShotPlayblastConfig,
)
from pipe.dcc.maya.playblast.shot.dialog import MPlayblastDialog
from pipe.dcc.maya.previs import state as previs_state
from pipe.dcc.maya.previs.cameras import is_live
from pipe.dcc.maya.previs.playback import compute_shot_ranges
from pipe.dcc.maya.runtime import get_main_qt_window

if TYPE_CHECKING:
    from pipe.dcc.maya.previs.state import PrevisState

log = logging.getLogger(__name__)


# Source-mode strings used internally by `_selected_source_mode` and config
# dispatch. Shot and custom match the base's strings.
_MODE_SHOT = "shot"
_MODE_SEQUENCE = "sequence"


class PrevisPlayblastDialog(MPlayblastDialog):
    _previs_state: PrevisState | None
    _shot_camera: QComboBox  # RLO Shot tab camera dropdown
    _sequence_proxy_label: QLabel
    _sequence_shots_label: QLabel
    _sequence_range_label: QLabel

    SEQUENCE_TAB_INDEX: int = -1

    SETTINGS_KEY = "maya_previs"
    # Sequence clips remember their own destination toggles in the viewer.
    SEQUENCE_SETTINGS_KEY = "maya_previs_sequence"

    def __init__(self, parent: QWidget | None) -> None:
        # Read previs state before super(), so `_add_custom_tabs` can branch on
        # file type while the UI is built.
        self._previs_state = previs_state.read_state()
        super().__init__(parent, windowTitle="SKD Previs Playblast")

    # ------------------------------------------------------------------
    # Tab assembly
    # ------------------------------------------------------------------

    def _add_custom_tabs(self, tabs: QTabWidget) -> None:
        if self._previs_state is None:
            return
        self.SEQUENCE_TAB_INDEX = tabs.count()
        tabs.addTab(self._build_sequence_tab(), "Sequence")
        tabs.tabBar().setTabToolTip(
            self.SEQUENCE_TAB_INDEX,
            "Stitch every shot's primary into one dailies movie.",
        )

    def _build_sequence_tab(self) -> QWidget:
        tab = QWidget()
        layout = QGridLayout(tab)

        row = 0
        layout.addWidget(QLabel("Source"), row, 0)
        layout.addWidget(QLabel("Previs Sequence — primaries only"), row, 1)

        row += 1
        layout.addWidget(QLabel("Sequence"), row, 0)
        self._sequence_proxy_label = QLabel("-")
        self._sequence_proxy_label.setToolTip(
            "ShotGrid proxy Shot this sequence is anchored to (e.g. `A_previs`)."
        )
        layout.addWidget(self._sequence_proxy_label, row, 1)

        row += 1
        layout.addWidget(QLabel("Total Shots"), row, 0)
        self._sequence_shots_label = QLabel("-")
        layout.addWidget(self._sequence_shots_label, row, 1)

        row += 1
        layout.addWidget(QLabel("Frame Range"), row, 0)
        self._sequence_range_label = QLabel("-")
        layout.addWidget(self._sequence_range_label, row, 1)

        return tab

    # ------------------------------------------------------------------
    # Sequence tab: data binding
    # ------------------------------------------------------------------

    def _refresh_sequence_fields(self) -> None:
        if self._previs_state is None or self.SEQUENCE_TAB_INDEX < 0:
            return
        proxy = self._shot.code if self._shot is not None else "-"
        shot_count = len(self._previs_state.shots)
        ranges = compute_shot_ranges(self._previs_state)
        if ranges:
            start = min(r[0] for r in ranges.values())
            end = max(r[1] for r in ranges.values())
            range_text = f"{start} - {end}"
        else:
            range_text = "-"
        self._sequence_proxy_label.setText(proxy or "-")
        self._sequence_shots_label.setText(str(shot_count))
        self._sequence_range_label.setText(range_text)

    # ------------------------------------------------------------------
    # Base-dialog behaviour overrides for previs files
    # ------------------------------------------------------------------

    def _selected_source_mode(self) -> str:
        current = self._source_tabs.currentIndex()
        if self.SEQUENCE_TAB_INDEX >= 0 and current == self.SEQUENCE_TAB_INDEX:
            return _MODE_SEQUENCE
        return super()._selected_source_mode()

    def _refresh_source_tab_availability(self) -> None:
        if self._previs_state is None:
            super()._refresh_source_tab_availability()
            return
        # Per-shot previs delivery lives in the previs panel now; this dialog
        # only offers whole-sequence dailies (and Custom).
        self._source_tabs.setTabEnabled(self.SHOT_TAB_INDEX, False)
        if self.SEQUENCE_TAB_INDEX >= 0:
            # No shots → no sequence to playblast.
            self._source_tabs.setTabEnabled(
                self.SEQUENCE_TAB_INDEX, bool(self._previs_state.shots)
            )
        if self._source_tabs.currentIndex() == self.SHOT_TAB_INDEX:
            self._source_tabs.setCurrentIndex(self._default_source_tab_index())

    def _default_source_tab_index(self) -> int:
        if self._previs_state is not None:
            if self.SEQUENCE_TAB_INDEX >= 0 and self._previs_state.shots:
                return self.SEQUENCE_TAB_INDEX
            return self.CUSTOM_TAB_INDEX
        return super()._default_source_tab_index()

    def _refresh_custom_ui_state(self) -> None:
        self._refresh_sequence_fields()

    def _action_button_text(self) -> str:
        if self._selected_source_mode() == _MODE_SEQUENCE:
            return "Playblast Sequence"
        return super()._action_button_text()

    def _build_shot_camera_widget(self) -> QWidget:
        # Called by the base when the RLO Shot tab is in use. (In previs files
        # that tab is disabled, but the base still builds it.)
        #
        # Order matters: set the default selection *before* wiring the
        # `currentTextChanged` signal. The base's `_build_shot_source_tab`
        # calls this helper while the tab is still mid-build — `_shot_range_value`
        # and other downstream widgets that `_on_source_settings_changed` reads
        # don't exist yet. Connecting after the default-set keeps the signal
        # from firing during construction.
        self._shot_camera = QComboBox(self)
        self._shot_camera.addItems(self._available_custom_cameras())
        self._shot_camera.setToolTip("Camera used for shot playblast output.")
        self._set_default_shot_camera()
        self._shot_camera.currentTextChanged.connect(self._on_source_settings_changed)
        return self._shot_camera

    @staticmethod
    def _active_camera_name() -> str:
        panel = MPlayblastDialog._resolve_active_model_panel()
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
                mc.listRelatives(camera_name, shapes=True, type="camera", fullPath=True)
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

    def _set_default_shot_camera(self) -> None:
        camera_name = self._active_camera_name()
        variants = self._camera_name_variants(camera_name)
        if not variants:
            return
        for index in range(self._shot_camera.count()):
            item_text = self._shot_camera.itemText(index)
            if item_text in variants:
                self._shot_camera.setCurrentIndex(index)
                return

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_export_state(self) -> str | None:
        if self._selected_source_mode() == _MODE_SEQUENCE:
            return self._validate_sequence()
        return super()._validate_export_state()

    def _validate_source_state(self, mode: str) -> str | None:
        if mode == _MODE_SHOT:
            return self._validate_rlo_shot()
        return None

    def _validate_rlo_shot(self) -> str | None:
        if (
            self._shot
            and self._shot.cut_in is not None
            and self._shot.cut_out is not None
            and self._shot.cut_out < self._shot.cut_in
        ):
            return "Shot cut range is invalid (Cut Out must be >= Cut In)."
        if not str(self._shot_camera.currentText()).strip():
            return "Choose a camera for Shot Playblast."
        return None

    def _validate_sequence(self) -> str | None:
        if self._previs_state is None or not self._previs_state.shots:
            return "This previs file has no shots."
        for shot in self._previs_state.shots:
            if not shot.primary or not is_live(shot.primary):
                label = shot.code or "(shot)"
                return (
                    f"{label} has an orphan primary '{shot.primary or '(none)'}'. "
                    "Fix or remove the shot before playblasting the sequence."
                )
        return None

    # ------------------------------------------------------------------
    # Routing for the viewer's Confirm panel
    # ------------------------------------------------------------------

    def _clip_folders(self) -> tuple[DiskDestination, ...]:
        scene_dir = Path(str(mc.file(query=True, sceneName=True) or ".")).parent
        return (
            DiskDestination(
                id=CURRENT_FOLDER_ID,
                name="Current Folder",
                directory=scene_dir,
                preset=FFmpegPreset.WEB,
                default_on=False,
            ),
            DiskDestination(
                id=CUSTOM_FOLDER_ID,
                name="Custom Folder",
                directory=resolve_playblast_tempdir(),
                preset=FFmpegPreset.WEB,
                default_on=False,
                browsable=True,
            ),
        )

    def _review_shot_code(self) -> str:
        if self._selected_source_mode() == _MODE_SEQUENCE:
            return (self._shot.code or "").strip() if self._shot is not None else ""
        return super()._review_shot_code()

    def _clip_shotgrid(self) -> ShotGridDestination:
        # An RLO shot playblast is working iteration; previs dailies are the
        # whole-sequence movie from the Sequence tab.
        return ShotGridDestination(
            entity=self._review_entity(),
            default_on=self._selected_source_mode() != _MODE_SHOT,
        )

    def _clip_output_prefix(self) -> str:
        if self._selected_source_mode() == _MODE_SEQUENCE:
            return (self._shot.code if self._shot is not None else "") or "previs"
        return super()._clip_output_prefix()

    def _clip_settings_key(self) -> str:
        if self._selected_source_mode() == _MODE_SEQUENCE:
            return self.SEQUENCE_SETTINGS_KEY
        return super()._clip_settings_key()

    # ------------------------------------------------------------------
    # Config dispatch + export
    # ------------------------------------------------------------------

    def _generate_config(self) -> MPlayblastConfig:
        # `MPlayblastConfig` is the *single-shot, single-camera* shape used by
        # `MPlayblaster`. Sequence mode doesn't fit it, so `do_export` short-
        # circuits before reaching this method.
        if self._selected_source_mode() == _MODE_SHOT:
            shot_config = self._build_rlo_shot_config()
        else:
            shot_config = self._build_custom_playblast_config()
        return MPlayblastConfig(
            dof=self.use_dof,
            hardware_fog=self.use_hardware_fog,
            lighting=self.use_lighting,
            shadows=self.use_shadows,
            shots=[shot_config],
            ssao=self.use_ssao,
        )

    def _build_rlo_shot_config(self) -> MShotPlayblastConfig:
        if self._shot is None:
            raise ValueError("No pipeline shot context was found.")
        version_label, version_title = _resolve_rlo_version(self._shot)
        return MShotPlayblastConfig(
            camera=str(self._shot_camera.currentText()).strip(),
            shot=self._shot,
            use_sequencer=False,
            version_label=version_label,
            version_title=version_title,
        )

    def _build_sequence_config(self) -> MSequenceConfig:
        assert self._previs_state is not None
        ranges = compute_shot_ranges(self._previs_state)
        cuts = [(shot.primary, *ranges[shot.id]) for shot in self._previs_state.shots]
        return MSequenceConfig(
            cuts=cuts,
            code=(self._shot.code if self._shot is not None else "") or "previs",
            viewport_options=self._viewport_options_payload(),
        )

    def _viewport_options_payload(self) -> dict[str, bool]:
        return {
            "dof": self.use_dof,
            "hardware_fog": self.use_hardware_fog,
            "lighting": self.use_lighting,
            "shadows": self.use_shadows,
            "ssao": self.use_ssao,
        }

    def do_export(self) -> None:
        # The Sequence tab renders through its own playblaster; other modes
        # use the base flow unchanged.
        if self._selected_source_mode() != _MODE_SEQUENCE:
            super().do_export()
            return

        validation_error = self._validate_export_state()
        if validation_error:
            MessageDialog(self, validation_error, "Playblast").exec_()
            return

        try:
            config = self._build_sequence_config()
            clips = MSequencePlayblaster().configure(config).playblast()
        except Exception as exc:
            log.exception("Sequence playblast failed")
            MessageDialog(
                self, f"Sequence playblast failed.\n\n{exc}", "Playblast Error"
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


def _resolve_rlo_version(shot: Shot) -> tuple[str | None, str | None]:
    scene_raw = mc.file(query=True, sceneName=True)
    if not isinstance(scene_raw, str) or not scene_raw:
        return None, None
    scene_path = Path(scene_raw).expanduser().resolve()
    stream = maya_rlo_stream(shot, owner=shot_owner_for(shot))
    return current_version_label(stream, scene_path)
