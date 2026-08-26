"""SKD Previs Playblast dialog."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import maya.cmds as mc
from Qt.QtWidgets import (
    QComboBox,
    QWidget,
)

from pipe.core.playblast import (
    CURRENT_FOLDER_ID,
    CURRENT_FOLDER_NAME,
    DiskDestination,
    FFmpegPreset,
    ShotGridDestination,
    custom_folder_destination,
)
from pipe.core.shot import maya_rlo_stream, shot_owner_for
from pipe.core.shotgrid import Shot
from pipe.core.versioning import current_version_label
from pipe.dcc.maya.playblast.shot.config import (
    MPlayblastConfig,
    MShotPlayblastConfig,
)
from pipe.dcc.maya.playblast.shot.dialog import MPlayblastDialog
from pipe.dcc.maya.playblast.viewport import resolve_active_model_panel
from pipe.dcc.maya.previs import state as previs_state

if TYPE_CHECKING:
    from pipe.dcc.maya.previs.state import PrevisState

log = logging.getLogger(__name__)


# Source-mode string used internally by `_selected_source_mode` and config
# dispatch; matches the base's string.
_MODE_SHOT = "shot"


class PrevisPlayblastDialog(MPlayblastDialog):
    _previs_state: PrevisState | None
    _shot_camera: QComboBox  # RLO Shot tab camera dropdown

    SETTINGS_KEY = "maya_previs"

    def __init__(self, parent: QWidget | None) -> None:
        # Read previs state before super(), so the tab set can branch on file
        # type while the UI is built.
        self._previs_state = previs_state.read_state()
        super().__init__(parent, windowTitle="SKD Previs Playblast")

    # ------------------------------------------------------------------
    # Base-dialog behaviour overrides for previs files
    # ------------------------------------------------------------------

    def _refresh_source_tab_availability(self) -> None:
        if self._previs_state is None:
            super()._refresh_source_tab_availability()
            return
        # A previs file's pipeline shot is the sequence proxy (`A_previs`), not a
        # real shot, so Shot mode would render the wrong thing.
        self._source_tabs.setTabEnabled(self.SHOT_TAB_INDEX, False)
        self._source_tabs.tabBar().setTabToolTip(
            self.SHOT_TAB_INDEX,
            "Not available in a previs file — playblast shots from the "
            "Previs Sequencer panel.",
        )
        if self._source_tabs.currentIndex() == self.SHOT_TAB_INDEX:
            self._source_tabs.setCurrentIndex(self._default_source_tab_index())

    def _default_source_tab_index(self) -> int:
        if self._previs_state is not None:
            return self.CUSTOM_TAB_INDEX
        return super()._default_source_tab_index()

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
        panel = resolve_active_model_panel()
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

    # ------------------------------------------------------------------
    # Routing for the viewer's Confirm panel
    # ------------------------------------------------------------------

    def _clip_folders(self) -> tuple[DiskDestination, ...]:
        scene_dir = Path(str(mc.file(query=True, sceneName=True) or ".")).parent
        return (
            DiskDestination(
                id=CURRENT_FOLDER_ID,
                name=CURRENT_FOLDER_NAME,
                directory=scene_dir,
                preset=FFmpegPreset.WEB,
                default_on=False,
            ),
            custom_folder_destination(),
        )

    def _clip_shotgrid(self) -> ShotGridDestination:
        # An RLO shot playblast is working iteration, so its Version is offered
        # but not pre-checked.
        return ShotGridDestination(
            entity=self._review_entity(),
            default_on=self._selected_source_mode() != _MODE_SHOT,
        )

    # ------------------------------------------------------------------
    # Config dispatch
    # ------------------------------------------------------------------

    def _generate_config(self) -> MPlayblastConfig:
        if self._selected_source_mode() == _MODE_SHOT:
            shot_config = self._build_rlo_shot_config()
        else:
            shot_config = self._build_custom_playblast_config()
        return MPlayblastConfig(quality=self.quality, shots=[shot_config])

    def _build_rlo_shot_config(self) -> MShotPlayblastConfig:
        if self._shot is None:
            raise ValueError("No pipeline shot context was found.")
        version_label, version_title = _resolve_rlo_version(self._shot)
        return MShotPlayblastConfig(
            camera=str(self._shot_camera.currentText()).strip(),
            shot=self._shot,
            version_label=version_label,
            version_title=version_title,
        )


def _resolve_rlo_version(shot: Shot) -> tuple[str | None, str | None]:
    scene_raw = mc.file(query=True, sceneName=True)
    if not isinstance(scene_raw, str) or not scene_raw:
        return None, None
    scene_path = Path(scene_raw).expanduser().resolve()
    stream = maya_rlo_stream(shot, owner=shot_owner_for(shot))
    return current_version_label(stream, scene_path)
