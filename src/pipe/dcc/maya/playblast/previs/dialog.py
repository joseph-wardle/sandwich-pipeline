"""SKD Previs Playblast dialog.

Extends the shared `MPlayblastDialog` with two surfaces that only apply when
the open Maya scene is a previs file (carries `previs_sequencer_state`):

* the **Shot tab** swaps its baked-`fileInfo("code")` layout for a dropdown
  over the previs file's shots, with a Compare-alternates checkbox that flips
  the playblast into a grid render via `MComparePlayblaster`;
* a new **Sequence tab** stitches every shot's primary into one dailies
  movie via `MSequencePlayblaster`, with full ShotGrid upload parity targeting
  the sequence-proxy Shot (e.g. `A_previs`).

RLO files keep the base dialog's behaviour — the Shot tab stays in its
baked-code shape, and the Sequence tab is hidden entirely.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import maya.cmds as mc
from Qt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTabWidget,
    QWidget,
)

from pipe.core.playblast import FFmpegPreset
from pipe.core.playblast.naming import build_edit_output_directory
from pipe.core.playblast.review import (
    PlayblastEntity,
    PlayblastUploadIntent,
    run_playblast_upload,
)
from pipe.core.shot import maya_rlo_stream, shot_owner_for
from pipe.core.shotgrid import Shot
from pipe.core.ui import MessageDialog
from pipe.core.util.users import resolve_artist_display_name
from pipe.core.versioning import current_version_label
from pipe.dcc.maya.playblast.previs.compare import (
    MCompareShotConfig,
    MComparePlayblaster,
)
from pipe.dcc.maya.playblast.previs.sequence import (
    MSequenceConfig,
    MSequencePlayblaster,
)
from pipe.dcc.maya.playblast.shot.config import (
    MPlayblastConfig,
    MShotPlayblastConfig,
    SaveLocation,
    dummy_shot,
)
from pipe.dcc.maya.playblast.shot.dialog import MPlayblastDialog
from pipe.dcc.maya.previs import state as previs_state
from pipe.dcc.maya.previs.cameras import focal_length, is_live
from pipe.dcc.maya.previs.playback import FRAME_START, compute_shot_ranges

if TYPE_CHECKING:
    from pipe.dcc.maya.previs.state import PrevisShot, PrevisState

log = logging.getLogger(__name__)


# Source-mode strings used internally by `_selected_source_mode` and config dispatch.
_MODE_SHOT = "shot"
_MODE_SEQUENCE = "sequence"
_MODE_CUSTOM = "custom"

# Display strings used in dropdown labels for shots without a ShotGrid code.
_UNASSIGNED_SUFFIX = "— unassigned"


class PrevisPlayblastDialog(MPlayblastDialog):
    _previs_state: PrevisState | None
    _shot_camera: QComboBox  # RLO Shot tab camera dropdown
    _previs_shot_combo: QComboBox
    _previs_primary_label: QLabel
    _previs_range_label: QLabel
    _previs_alts_label: QLabel
    _compare_alts_checkbox: QCheckBox
    _sequence_proxy_label: QLabel
    _sequence_shots_label: QLabel
    _sequence_range_label: QLabel

    SEQUENCE_TAB_INDEX: int = -1

    class SAVE_LOCS(MPlayblastDialog.SAVE_LOCS):
        EDIT = SaveLocation(
            "Send to Edit",
            lambda: build_edit_output_directory("previs"),
            FFmpegPreset.EDIT_SQ,
        )

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

        row += 1
        layout.addWidget(QLabel("Alternates"), row, 0)
        self._previs_alts_label = QLabel("-")
        self._previs_alts_label.setToolTip(
            "Alternate cameras for the selected shot. Compare-alternates renders "
            "the primary + alternates into one grid video."
        )
        layout.addWidget(self._previs_alts_label, row, 1)

        row += 1
        self._compare_alts_checkbox = QCheckBox("Compare alternates (grid playblast)")
        self._compare_alts_checkbox.setToolTip(
            "Render the primary and all alternates side-by-side into one grid "
            "video. Disabled when the selected shot has no alternates."
        )
        self._compare_alts_checkbox.toggled.connect(self._on_source_settings_changed)
        layout.addWidget(self._compare_alts_checkbox, row, 0, 1, 2)

        # No ShotGrid section in the previs Shot tab — per-shot Versions are a
        # v2 concern. Dailies for previs go through the Sequence tab.

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

        row += 1
        self._build_shotgrid_section_into(layout, start_row=row)
        return tab

    def _build_shotgrid_section_into(self, layout: QGridLayout, start_row: int) -> None:
        """Build the ShotGrid upload section inline at `start_row` of `layout`.

        Mirrors the base class's `_build_shot_source_tab` rows 4–7. Centralised
        here so both the previs Shot tab and the Sequence tab can reuse the
        same widgets (`_shotgrid_upload_checkbox`, `_shotgrid_review_combo`,
        etc.) without duplicating wiring.
        """
        row = start_row
        layout.addWidget(QLabel("ShotGrid"), row, 0)
        self._shotgrid_upload_checkbox = QCheckBox("Upload to ShotGrid")
        self._shotgrid_upload_checkbox.setChecked(False)
        self._shotgrid_upload_checkbox.setToolTip(
            "Upload this playblast as a ShotGrid Version on the target Shot."
        )
        self._shotgrid_upload_checkbox.toggled.connect(self._on_shotgrid_upload_toggled)
        layout.addWidget(self._shotgrid_upload_checkbox, row, 1)

        row += 1
        self._shotgrid_upload_target_row = self._build_shotgrid_upload_target_row()
        layout.addWidget(self._shotgrid_upload_target_row, row, 0, 1, 2)

        row += 1
        self._build_shotgrid_review_row()
        layout.addWidget(self._shotgrid_review_combo, row, 0, 1, 2)

        row += 1
        self._shotgrid_description_row = QWidget()
        description_layout = QHBoxLayout(self._shotgrid_description_row)
        description_layout.setContentsMargins(0, 0, 0, 0)
        description_layout.addWidget(QLabel("Description"))
        self._shotgrid_description_field = QLineEdit()
        self._shotgrid_description_field.setPlaceholderText(
            "Optional ShotGrid version description"
        )
        description_layout.addWidget(self._shotgrid_description_field)
        layout.addWidget(self._shotgrid_description_row, row, 0, 1, 2)
        self._sync_shotgrid_description_visibility()

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
            self._previs_alts_label.setText("-")
            self._compare_alts_checkbox.setEnabled(False)
            return

        ranges = compute_shot_ranges(self._previs_state) if self._previs_state else {}
        start, end = ranges.get(shot.id, (FRAME_START, FRAME_START))
        self._previs_primary_label.setText(shot.primary or "-")
        self._previs_range_label.setText(f"{start} - {end}")
        live_alts = [alt for alt in shot.alternates if is_live(alt)]
        self._previs_alts_label.setText(", ".join(live_alts) if live_alts else "(none)")

        # Compare only makes sense when there is something to compare.
        has_alts = bool(live_alts)
        self._compare_alts_checkbox.setEnabled(has_alts)
        if not has_alts and self._compare_alts_checkbox.isChecked():
            self._compare_alts_checkbox.setChecked(False)
        self._compare_alts_checkbox.setToolTip(
            "Render the primary and all alternates side-by-side into one grid video."
            if has_alts
            else "This shot has no alternates to compare."
        )

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

        # No shots → no sequence to playblast.
        if self.SEQUENCE_TAB_INDEX >= 0:
            self._source_tabs.setTabEnabled(self.SEQUENCE_TAB_INDEX, shot_count > 0)

    # ------------------------------------------------------------------
    # Source-mode dispatch + validation
    # ------------------------------------------------------------------

    def _selected_source_mode(self) -> str:
        current = self._source_tabs.currentIndex()
        if current == self.SHOT_TAB_INDEX:
            return _MODE_SHOT
        if self.SEQUENCE_TAB_INDEX >= 0 and current == self.SEQUENCE_TAB_INDEX:
            return _MODE_SEQUENCE
        return _MODE_CUSTOM

    def _is_previs_shot_compare(self) -> bool:
        if self._previs_state is None:
            return False
        if self._selected_source_mode() != _MODE_SHOT:
            return False
        return self._compare_alts_checkbox.isChecked()

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

    def _validate_source_state(self, mode: str) -> str | None:
        if mode == _MODE_SHOT and self._previs_state is not None:
            return self._validate_previs_shot()
        if mode == _MODE_SHOT:
            return self._validate_rlo_shot()
        if mode == _MODE_SEQUENCE:
            return self._validate_sequence()
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
        if self._compare_alts_checkbox.isChecked():
            live_alts = [alt for alt in shot.alternates if is_live(alt)]
            if not live_alts:
                return "Compare alternates needs at least one live alternate."
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
    # Visibility rules
    # ------------------------------------------------------------------

    def _refresh_custom_ui_state(self) -> None:
        self._refresh_previs_shot_fields()
        self._refresh_sequence_fields()
        self._apply_destination_visibility()

    def _refresh_shot_context_fields(self) -> None:
        # The base writes to `_shot_code_value` / `_shot_range_value`, which
        # only exist on the RLO Shot tab. In previs mode the equivalents are
        # `_previs_primary_label` / `_previs_range_label`, refreshed by
        # `_refresh_previs_shot_fields`. Skip the base path entirely there.
        if self._previs_state is not None:
            return
        super()._refresh_shot_context_fields()

    def _apply_destination_visibility(self) -> None:
        """Hide EDIT in Compare mode and in Sequence mode; show otherwise.

        Compare playblasts are review-internal (never editorial), and full-
        sequence dailies go to ShotGrid (not the editorial inbox). See
        `project_dailies_path_is_shotgrid` memory.
        """
        edit_name = self.SAVE_LOCS.EDIT.name  # type: ignore[attr-defined]
        edit_visible = not (
            self._is_previs_shot_compare()
            or self._selected_source_mode() == _MODE_SEQUENCE
        )
        toggle = self._destination_checkboxes.get(edit_name)
        if toggle is None:
            return
        row = toggle.parentWidget()
        if row is not None:
            row.setVisible(edit_visible)
        if not edit_visible and toggle.isChecked():
            toggle.setChecked(False)

    def _default_destination_enabled(self, location: SaveLocation) -> bool:
        return location.name == self.SAVE_LOCS.EDIT.name  # type: ignore[attr-defined]

    def _should_upload_shot_playblast_to_shotgrid(self) -> bool:
        # In a previs file, the ShotGrid upload section only lives on the
        # Sequence tab (not the Shot tab). Per-shot uploads to ShotGrid are a
        # v2 concern. The Sequence tab uses its own export path which uploads
        # via `_upload_sequence_playblast`, so the base's auto-upload path
        # never fires for previs files.
        if self._previs_state is not None:
            return False
        return super()._should_upload_shot_playblast_to_shotgrid()

    # ------------------------------------------------------------------
    # Config dispatch + per-mode execution
    # ------------------------------------------------------------------

    def _generate_config(self) -> MPlayblastConfig:
        # `MPlayblastConfig` is the *single-shot, single-camera* shape used by
        # `MPlayblaster`. Compare and Sequence modes don't fit this shape, so
        # `do_export` checks for them first and short-circuits before reaching
        # this method. Reaching here means we're in a single-camera mode.
        mode = self._selected_source_mode()
        if mode == _MODE_SHOT and self._previs_state is not None:
            shot_config = self._build_previs_single_shot_config()
        elif mode == _MODE_SHOT:
            shot_config = self._build_rlo_shot_config()
        else:
            shot_config = self._build_custom_playblast_config()
        return self._wrap_single_shot_config(shot_config)

    def _build_rlo_shot_config(self) -> MShotPlayblastConfig:
        if self._shot is None:
            raise ValueError("No pipeline shot context was found.")
        output_name = self._resolve_output_name(self._shot.code or "")
        version_label, version_title = _resolve_rlo_version(self._shot)
        return MShotPlayblastConfig(
            camera=str(self._shot_camera.currentText()).strip(),
            shot=self._shot,
            paths=self._paths_for_filename(output_name),
            use_sequencer=False,
            version_label=version_label,
            version_title=version_title,
        )

    def _build_previs_single_shot_config(self) -> MShotPlayblastConfig:
        shot = self._selected_previs_shot()
        if shot is None or not shot.primary:
            raise ValueError("Previs shot has no primary camera.")
        base_name = shot.shotgrid_code or self._previs_shot_combo.currentText()
        output_name = self._resolve_output_name(base_name)
        cut_in, cut_out = self._previs_shot_frame_range(shot)
        return MShotPlayblastConfig(
            camera=shot.primary,
            shot=dummy_shot(
                code=base_name,
                cut_in=cut_in,
                cut_out=cut_out,
                cut_duration=max(0, cut_out - cut_in + 1),
            ),
            paths=self._paths_for_filename(output_name),
            use_sequencer=False,
        )

    def _previs_shot_frame_range(self, shot: PrevisShot) -> tuple[int, int]:
        if self._previs_state is None:
            return (FRAME_START, FRAME_START)
        ranges = compute_shot_ranges(self._previs_state)
        return ranges.get(shot.id, (FRAME_START, FRAME_START))

    def _wrap_single_shot_config(
        self, shot_config: MShotPlayblastConfig
    ) -> MPlayblastConfig:
        return MPlayblastConfig(
            dof=self.use_dof,
            hardware_fog=self.use_hardware_fog,
            lighting=self.use_lighting,
            shadows=self.use_shadows,
            shots=[shot_config],
            ssao=self.use_ssao,
        )

    def _build_compare_config(self) -> MCompareShotConfig:
        shot = self._selected_previs_shot()
        assert self._previs_state is not None and shot is not None
        cut_in, _ = self._previs_shot_frame_range(shot)
        live_alts = [alt for alt in shot.alternates if is_live(alt)]
        cameras = [shot.primary, *live_alts]
        durations = [shot.duration_of(cam) for cam in cameras]
        focals = [focal_length(cam) for cam in cameras]
        max_length = max(durations)
        base_name = shot.shotgrid_code or self._previs_shot_combo.currentText()
        output_name = self._resolve_output_name(f"{base_name}_compare")
        return MCompareShotConfig(
            cameras=cameras,
            camera_durations=durations,
            focal_lengths=focals,
            start_frame=cut_in,
            total_frames=max_length,
            paths=self._paths_for_filename(output_name),
            shot_label=base_name,
            viewport_options=self._viewport_options_payload(),
        )

    def _build_sequence_config(self) -> MSequenceConfig:
        assert self._previs_state is not None and self._shot is not None
        ranges = compute_shot_ranges(self._previs_state)
        cuts = [(shot.primary, *ranges[shot.id]) for shot in self._previs_state.shots]
        base_name = self._shot.code or "previs"
        output_name = self._resolve_output_name(base_name)
        return MSequenceConfig(
            cuts=cuts,
            proxy_shot=self._shot,
            paths=self._paths_for_filename(output_name),
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

    # ------------------------------------------------------------------
    # Override `do_export` for Compare and Sequence modes
    # ------------------------------------------------------------------

    def do_export(self) -> None:
        # Compare and Sequence each use their own playblaster (not the base
        # `MPlayblaster`). For those, build the per-mode config, validate,
        # and run their playblaster directly. Everything else delegates to
        # the base implementation.
        if self._is_previs_shot_compare():
            self._run_compare_export()
            return
        if self._selected_source_mode() == _MODE_SEQUENCE:
            self._run_sequence_export()
            return
        super().do_export()

    def _run_compare_export(self) -> None:
        validation_error = self._validate_target_destination_state()
        if validation_error:
            MessageDialog(self, validation_error, "Playblast").exec_()
            return
        try:
            config = self._build_compare_config()
            MComparePlayblaster().configure(config).playblast()
        except Exception as exc:
            log.exception("Compare playblast failed")
            MessageDialog(
                self, f"Compare playblast failed.\n\n{exc}", "Playblast Error"
            ).exec_()
            return
        self._remember_custom_folder()
        MessageDialog(self, self._format_compare_success(config)).exec_()
        self.close()

    def _run_sequence_export(self) -> None:
        validation_error = self._validate_target_destination_state()
        if validation_error:
            MessageDialog(self, validation_error, "Playblast").exec_()
            return
        try:
            config = self._build_sequence_config()
            MSequencePlayblaster().configure(config).playblast()
        except Exception as exc:
            log.exception("Sequence playblast failed")
            MessageDialog(
                self, f"Sequence playblast failed.\n\n{exc}", "Playblast Error"
            ).exec_()
            return

        self._remember_custom_folder()
        post_messages: list[str] = []
        if self._is_shotgrid_upload_requested():
            post_messages = self._upload_sequence_playblast(config)

        MessageDialog(
            self, self._format_sequence_success(config, post_messages)
        ).exec_()
        self.close()

    def _upload_sequence_playblast(self, config: MSequenceConfig) -> list[str]:
        proxy_code = config.proxy_shot.code or ""
        output_paths = config.final_output_paths()
        intent = PlayblastUploadIntent(
            entity=PlayblastEntity.shot(proxy_code),
            output_paths=tuple(output_paths),
            preferred_paths=tuple(output_paths),
            description=self._shotgrid_upload_description() or None,
            artist_display_name=resolve_artist_display_name().strip() or None,
            upload_version=self._is_shotgrid_version_upload_enabled(),
            upload_to_review=self._is_shotgrid_review_upload_enabled(),
            review_playlist_id=self._shotgrid_review_combo.selected_playlist_id,
            review_load_error=self._shotgrid_review_combo.load_error,
            fallback_version_name=f"{proxy_code}_playblast",
        )
        return run_playblast_upload(intent)

    @staticmethod
    def _format_compare_success(config: MCompareShotConfig) -> str:
        lines = ["Compare playblast export successful.", "", "Outputs:"]
        lines.extend(str(p) for p in config.final_output_paths())
        return "\n".join(lines)

    @staticmethod
    def _format_sequence_success(
        config: MSequenceConfig, post_messages: list[str]
    ) -> str:
        lines = ["Sequence playblast export successful.", "", "Outputs:"]
        lines.extend(str(p) for p in config.final_output_paths())
        if post_messages:
            lines.append("")
            lines.append("Post-export:")
            lines.extend(post_messages)
        return "\n".join(lines)


def _resolve_rlo_version(shot: Shot) -> tuple[str | None, str | None]:
    scene_raw = mc.file(query=True, sceneName=True)
    if not isinstance(scene_raw, str) or not scene_raw:
        return None, None
    scene_path = Path(scene_raw).expanduser().resolve()
    stream = maya_rlo_stream(shot, owner=shot_owner_for(shot))
    return current_version_label(stream, scene_path)
