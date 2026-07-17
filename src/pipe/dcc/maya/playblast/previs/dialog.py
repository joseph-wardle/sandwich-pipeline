"""SKD Previs Playblast dialog.

Extends the render-only `MPlayblastDialog` with two surfaces that only apply
when the open Maya scene is a previs file (carries `previs_sequencer_state`):

* the **Shot tab** swaps its baked-`fileInfo("code")` layout for a dropdown
  over the previs file's shots;
* a new **Sequence tab** stitches every shot's primary into one clip via
  `MSequencePlayblaster`. Its ShotGrid row in the viewer targets the
  sequence-proxy Shot (e.g. `A_previs`) — previs dailies go to ShotGrid,
  never the editorial inbox.

RLO files keep the base dialog's behaviour — the Shot tab stays in its
baked-code shape, and the Sequence tab is hidden entirely.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import attrs
import maya.cmds as mc
from Qt.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QTabWidget,
    QWidget,
)

from pipe.core.playblast import Destination, FFmpegPreset, PreviewClip, ShotGridUpload
from pipe.core.playblast.naming import build_edit_output_directory
from pipe.core.playblast.tempdir import resolve_playblast_tempdir
from pipe.core.shot import maya_rlo_stream, shot_owner_for
from pipe.core.shotgrid import Shot
from pipe.core.ui import MessageDialog
from pipe.core.util.users import resolve_artist_display_name
from pipe.core.versioning import current_version_label
from pipe.dcc.maya.playblast.previs.sequence import (
    MSequenceConfig,
    MSequencePlayblaster,
)
from pipe.dcc.maya.playblast.shot.config import (
    MPlayblastConfig,
    MShotPlayblastConfig,
    dummy_shot,
)
from pipe.dcc.maya.playblast.shot.dialog import MPlayblastDialog
from pipe.dcc.maya.previs import state as previs_state
from pipe.dcc.maya.previs.cameras import is_live
from pipe.dcc.maya.previs.playback import FRAME_START, compute_shot_ranges

if TYPE_CHECKING:
    from pipe.dcc.maya.previs.state import PrevisShot, PrevisState

log = logging.getLogger(__name__)


# Source-mode strings used internally by `_selected_source_mode` and config
# dispatch. Shot and custom match the base's strings.
_MODE_SHOT = "shot"
_MODE_SEQUENCE = "sequence"
_MODE_CUSTOM = "custom"

# Display string used in dropdown labels for shots without a ShotGrid code.
_UNASSIGNED_SUFFIX = "— unassigned"


class PrevisPlayblastDialog(MPlayblastDialog):
    _previs_state: PrevisState | None
    _shot_camera: QComboBox  # RLO Shot tab camera dropdown
    _previs_shot_combo: QComboBox
    _previs_primary_label: QLabel
    _previs_range_label: QLabel
    _sequence_proxy_label: QLabel
    _sequence_shots_label: QLabel
    _sequence_range_label: QLabel

    SEQUENCE_TAB_INDEX: int = -1

    SETTINGS_KEY = "maya_previs"
    # Sequence clips remember their own destination toggles in the viewer.
    SEQUENCE_SETTINGS_KEY = "maya_previs_sequence"

    def __init__(self, parent: QWidget | None) -> None:
        # Read previs state before super(), so `_build_shot_source_tab` and
        # `_add_custom_tabs` can branch on file type while the UI is built.
        self._previs_state = previs_state.read_state()
        super().__init__(parent, windowTitle="SKD Previs Playblast")

    # ------------------------------------------------------------------
    # Tab assembly
    # ------------------------------------------------------------------

    def _build_shot_source_tab(self) -> QWidget:
        if self._previs_state is None:
            return super()._build_shot_source_tab()
        return self._build_previs_shot_tab()

    def _add_custom_tabs(self, tabs: QTabWidget) -> None:
        if self._previs_state is None:
            return
        self.SEQUENCE_TAB_INDEX = tabs.count()
        tabs.addTab(self._build_sequence_tab(), "Sequence")
        tabs.tabBar().setTabToolTip(
            self.SEQUENCE_TAB_INDEX,
            "Stitch every shot's primary into one dailies movie.",
        )

    def _build_previs_shot_tab(self) -> QWidget:
        tab = QWidget()
        layout = QGridLayout(tab)

        row = 0
        layout.addWidget(QLabel("Source"), row, 0)
        source_label = QLabel("Previs File — shot picker")
        source_label.setToolTip(
            "One previs file holds the whole sequence; pick which shot to playblast."
        )
        layout.addWidget(source_label, row, 1)

        row += 1
        layout.addWidget(QLabel("Shot"), row, 0)
        self._previs_shot_combo = self._build_previs_shot_combo()
        layout.addWidget(self._previs_shot_combo, row, 1)

        row += 1
        layout.addWidget(QLabel("Primary"), row, 0)
        self._previs_primary_label = QLabel("-")
        self._previs_primary_label.setToolTip(
            "Primary camera (namespace) for the selected shot."
        )
        layout.addWidget(self._previs_primary_label, row, 1)

        row += 1
        layout.addWidget(QLabel("Frame Range"), row, 0)
        self._previs_range_label = QLabel("-")
        self._previs_range_label.setToolTip(
            "Frame range the selected shot occupies in the sequence."
        )
        layout.addWidget(self._previs_range_label, row, 1)

        self._previs_shot_combo.currentIndexChanged.connect(
            self._on_previs_shot_selection_changed
        )
        self._select_default_previs_shot()
        return tab

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
    # Previs Shot tab: data binding
    # ------------------------------------------------------------------

    def _build_previs_shot_combo(self) -> QComboBox:
        combo = QComboBox(self)
        combo.setToolTip(
            "Pick which previs shot to playblast. Default = the shot the "
            "current frame is inside."
        )
        assert self._previs_state is not None
        for shot in self._previs_state.shots:
            combo.addItem(self._previs_shot_label(shot), userData=shot.id)
        return combo

    @staticmethod
    def _previs_shot_label(shot: PrevisShot) -> str:
        display = shot.code or "—"
        if shot.shotgrid_code:
            return f"{display} — {shot.shotgrid_code}"
        return f"{display} {_UNASSIGNED_SUFFIX}"

    @staticmethod
    def _previs_shot_code(shot: PrevisShot) -> str:
        """Filename-friendly code for one previs shot."""
        return shot.shotgrid_code or shot.code or "previs"

    def _select_default_previs_shot(self) -> None:
        """Default to the shot containing the current frame. Falls back to the
        first shot if the playhead is outside the sequence."""
        if self._previs_state is None or not self._previs_state.shots:
            return
        ranges = compute_shot_ranges(self._previs_state)
        frame = int(mc.currentTime(query=True))
        for shot in self._previs_state.shots:
            start, end = ranges.get(shot.id, (0, -1))
            if start <= frame <= end:
                self._set_previs_shot_combo_to(shot.id)
                return
        self._previs_shot_combo.setCurrentIndex(0)

    def _set_previs_shot_combo_to(self, shot_id: str) -> None:
        for index in range(self._previs_shot_combo.count()):
            if self._previs_shot_combo.itemData(index) == shot_id:
                self._previs_shot_combo.setCurrentIndex(index)
                return

    def _selected_previs_shot(self) -> PrevisShot | None:
        if self._previs_state is None:
            return None
        shot_id = self._previs_shot_combo.currentData()
        if not isinstance(shot_id, str):
            return None
        return self._previs_state.find_shot(shot_id)

    def _on_previs_shot_selection_changed(self, _index: int) -> None:
        self._update_ui_state()

    def _refresh_previs_shot_fields(self) -> None:
        if self._previs_state is None:
            return
        shot = self._selected_previs_shot()
        if shot is None:
            self._previs_primary_label.setText("-")
            self._previs_range_label.setText("-")
            return

        start, end = self._previs_shot_frame_range(shot)
        self._previs_primary_label.setText(shot.primary or "-")
        self._previs_range_label.setText(f"{start} - {end}")

    def _previs_shot_frame_range(self, shot: PrevisShot) -> tuple[int, int]:
        if self._previs_state is None:
            return (FRAME_START, FRAME_START)
        ranges = compute_shot_ranges(self._previs_state)
        return ranges.get(shot.id, (FRAME_START, FRAME_START))

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
        # The previs Shot tab works off sequencer state, not the baked code,
        # so it stays enabled even without a resolved pipeline shot.
        self._source_tabs.setTabEnabled(self.SHOT_TAB_INDEX, True)
        if self.SEQUENCE_TAB_INDEX >= 0:
            # No shots → no sequence to playblast.
            self._source_tabs.setTabEnabled(
                self.SEQUENCE_TAB_INDEX, bool(self._previs_state.shots)
            )

    def _default_source_tab_index(self) -> int:
        if self._previs_state is not None:
            return self.SHOT_TAB_INDEX
        return super()._default_source_tab_index()

    def _refresh_shot_context_fields(self) -> None:
        # The base writes to `_shot_code_value` / `_shot_range_value`, which
        # only exist on the RLO Shot tab. In previs mode the equivalents are
        # refreshed by `_refresh_previs_shot_fields`. Skip the base path there.
        if self._previs_state is not None:
            return
        super()._refresh_shot_context_fields()

    def _refresh_custom_ui_state(self) -> None:
        self._refresh_previs_shot_fields()
        self._refresh_sequence_fields()

    def _action_button_text(self) -> str:
        if self._selected_source_mode() == _MODE_SEQUENCE:
            return "Playblast Sequence"
        return super()._action_button_text()

    def _build_shot_camera_widget(self) -> QWidget:
        # Called by the base when the RLO Shot tab is in use. The previs Shot
        # tab is built by `_build_previs_shot_tab` and doesn't touch this.
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
        mode = self._selected_source_mode()
        # Previs modes validate against sequencer state, not the base's
        # pipeline-shot-context requirement.
        if mode == _MODE_SHOT and self._previs_state is not None:
            return self._validate_previs_shot()
        if mode == _MODE_SEQUENCE:
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

    def _validate_previs_shot(self) -> str | None:
        shot = self._selected_previs_shot()
        if shot is None:
            return "Pick a shot to playblast."
        if not shot.primary or not is_live(shot.primary):
            return (
                f"{self._previs_shot_combo.currentText()} has an orphan primary "
                f"'{shot.primary or '(none)'}'. Fix or remove the shot before playblasting."
            )
        return None

    def _validate_sequence(self) -> str | None:
        if self._previs_state is None or not self._previs_state.shots:
            return "This previs file has no shots."
        for shot in self._previs_state.shots:
            if not shot.primary or not is_live(shot.primary):
                label = self._previs_shot_label(shot)
                return (
                    f"{label} has an orphan primary '{shot.primary or '(none)'}'. "
                    "Fix or remove the shot before playblasting the sequence."
                )
        return None

    # ------------------------------------------------------------------
    # Routing for the viewer's Confirm panel
    # ------------------------------------------------------------------

    def _clip_destinations(self) -> tuple[Destination, ...]:
        scene_dir = Path(str(mc.file(query=True, sceneName=True) or ".")).parent
        rows = [
            Destination(
                name="Current Folder",
                directory=scene_dir,
                preset=FFmpegPreset.WEB,
                default_on=False,
            ),
            Destination(
                name="Custom Folder",
                directory=resolve_playblast_tempdir(),
                preset=FFmpegPreset.WEB,
                default_on=False,
                browsable=True,
            ),
        ]
        # Per-shot previs playblasts feed editorial; full-sequence dailies go
        # to ShotGrid instead (see `project_dailies_path_is_shotgrid`).
        if self._selected_source_mode() != _MODE_SEQUENCE:
            rows.insert(
                0,
                Destination(
                    name="Send to Edit",
                    directory=build_edit_output_directory("previs"),
                    preset=FFmpegPreset.EDIT_SQ,
                ),
            )
        return tuple(rows)

    def _clip_shotgrid(self) -> ShotGridUpload | None:
        if self._selected_source_mode() == _MODE_SEQUENCE:
            code = (self._shot.code or "").strip() if self._shot is not None else ""
            if not code:
                return None
            return ShotGridUpload(
                entity_kind="shot",
                entity_value=code,
                artist_display_name=resolve_artist_display_name().strip() or None,
            )
        if self._previs_state is not None:
            # Per-shot previs Versions aren't offered; previs dailies go
            # through the Sequence tab.
            return None
        return super()._clip_shotgrid()

    def _clip_output_prefix(self) -> str:
        mode = self._selected_source_mode()
        if mode == _MODE_SEQUENCE:
            return (self._shot.code if self._shot is not None else "") or "previs"
        if mode == _MODE_SHOT and self._previs_state is not None:
            shot = self._selected_previs_shot()
            return self._previs_shot_code(shot) if shot is not None else "previs"
        return super()._clip_output_prefix()

    def _routed_clip(self, clip: PreviewClip) -> PreviewClip:
        routed = super()._routed_clip(clip)
        if self._selected_source_mode() == _MODE_SEQUENCE:
            routed = attrs.evolve(routed, settings_key=self.SEQUENCE_SETTINGS_KEY)
        return routed

    # ------------------------------------------------------------------
    # Config dispatch + export
    # ------------------------------------------------------------------

    def _generate_config(self) -> MPlayblastConfig:
        # `MPlayblastConfig` is the *single-shot, single-camera* shape used by
        # `MPlayblaster`. Sequence mode doesn't fit it, so `do_export` short-
        # circuits before reaching this method.
        mode = self._selected_source_mode()
        if mode == _MODE_SHOT and self._previs_state is not None:
            shot_config = self._build_previs_single_shot_config()
        elif mode == _MODE_SHOT:
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

    def _build_previs_single_shot_config(self) -> MShotPlayblastConfig:
        shot = self._selected_previs_shot()
        if shot is None or not shot.primary:
            raise ValueError("Previs shot has no primary camera.")
        cut_in, cut_out = self._previs_shot_frame_range(shot)
        return MShotPlayblastConfig(
            camera=shot.primary,
            shot=dummy_shot(
                code=self._previs_shot_code(shot),
                cut_in=cut_in,
                cut_out=cut_out,
                cut_duration=max(0, cut_out - cut_in + 1),
            ),
            use_sequencer=False,
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

        viewer_error = self._hand_off_to_viewer(
            [self._routed_clip(clip) for clip in clips]
        )
        if viewer_error:
            MessageDialog(self, viewer_error, "Playblast Error").exec_()
            return
        self.close()


def _resolve_rlo_version(shot: Shot) -> tuple[str | None, str | None]:
    scene_raw = mc.file(query=True, sceneName=True)
    if not isinstance(scene_raw, str) or not scene_raw:
        return None, None
    scene_path = Path(scene_raw).expanduser().resolve()
    stream = maya_rlo_stream(shot, owner=shot_owner_for(shot))
    return current_version_label(stream, scene_path)
