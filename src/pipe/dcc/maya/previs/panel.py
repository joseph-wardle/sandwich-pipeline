"""Dockable previs panel. Acts as the controller for all interactive operations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

import maya.cmds as mc
from env_sg import DB_Config
from maya.app.general.mayaMixin import MayaQWidgetDockableMixin  # type: ignore
from maya.OpenMayaUI import MQtUtil
from Qt.QtCompat import wrapInstance
from Qt.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pipe.core.playblast import PreviewClip
from pipe.core.playblast.viewer import open_viewer
from pipe.core.previs import ManifestWriteRefused, codes, mutate_manifest, naming
from pipe.core.shotgrid import ShotGrid, ShotGridError
from pipe.core.ui import MessageDialog, progress_scope
from pipe.core.util.paths import get_previs_path

from pipe.dcc.maya.command import undo_chunk
from pipe.dcc.maya.runtime import get_main_qt_window

from . import (
    active,
    camera_sequencer,
    cameras,
    dialogs,
    file_ops,
    monitor,
    playblast,
    rlo,
    state,
    style,
)
from .cut_view import CutView
from .state import PrevisShot, PrevisState, ShotTake
from .timeline_view import TimelineView

if TYPE_CHECKING:
    from pipe.core.previs.model import SequenceManifest

log = logging.getLogger(__name__)

PANEL_OBJECT_NAME = "previsPanel"
WORKSPACE_CONTROL_NAME = PANEL_OBJECT_NAME + "WorkspaceControl"
_PLAYBLAST_TITLE = "Previs Playblast"
_BREAK_OUT_TITLE = "Break Out Shot"

_panel_instance: PrevisPanel | None = None


class PrevisPanel(MayaQWidgetDockableMixin, QWidget):  # type: ignore[misc]
    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent=parent)
        self.setObjectName(PANEL_OBJECT_NAME)
        self.setWindowTitle("Previs Sequencer")
        self.setStyleSheet(f"#{PANEL_OBJECT_NAME} {{ background: {style.PANEL_BG}; }}")

        self._state = state.read_state() or PrevisState()
        self._synced: tuple[str | None, tuple[str, ...]] | None = None

        self._build_ui()
        self.refresh()

    # ---------- UI scaffolding ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        self._cut_view = CutView(self, parent=self)
        self._timeline_view = TimelineView(self, parent=self)
        self._views = QStackedWidget(self)
        self._views.addWidget(self._cut_view)
        self._views.addWidget(self._timeline_view)
        root.addWidget(self._views, 1)
        # Not persisted: the cut is what the panel is for, so every session opens
        # on it regardless of where the last one ended.
        self._view: CutView | TimelineView = self._cut_view

    def _build_top_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("topBar")
        bar.setFixedHeight(36)
        bar.setStyleSheet(style.TOP_BAR)

        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(10)

        dot = QFrame(bar)
        dot.setObjectName("topBarDot")
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(style.TOP_BAR_DOT)
        row.addWidget(dot)

        title = QLabel("Pre-vis Sequencer", bar)
        title.setObjectName("title")
        row.addWidget(title)

        self._info = QLabel("", bar)
        self._info.setObjectName("info")
        row.addWidget(self._info)

        self._cut_btn = _view_button(
            bar, "cut", "Shots in edit order — horizontal position is cut position"
        )
        self._cut_btn.clicked.connect(lambda: self._show_view(self._cut_view))
        row.addWidget(self._cut_btn)

        self._timeline_btn = _view_button(
            bar,
            "timeline",
            "Shots where their animation lives. Horizontal position is scene time",
        )
        self._timeline_btn.clicked.connect(lambda: self._show_view(self._timeline_view))
        row.addWidget(self._timeline_btn)

        # Exclusive group, so "which button is lit" cannot drift from each other
        # and clicking the lit one cannot leave the pair unlit.
        self._view_group = QButtonGroup(bar)
        self._view_group.addButton(self._cut_btn)
        self._view_group.addButton(self._timeline_btn)
        self._cut_btn.setChecked(True)

        row.addStretch(1)

        self._monitor_label = QLabel("", bar)
        self._monitor_label.setObjectName("info")
        row.addWidget(self._monitor_label)

        monitor_btn = QPushButton("set monitor", bar)
        monitor_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        monitor_btn.clicked.connect(self.pick_monitor)
        row.addWidget(monitor_btn)

        clean_check = QCheckBox("clean", bar)
        clean_check.setStyleSheet(style.TOOLBAR_CHECKBOX)
        clean_check.setChecked(monitor.clean_view())
        clean_check.setToolTip("Hide grid, cameras, and rig controls in the monitor")
        clean_check.toggled.connect(monitor.set_clean_view)
        row.addWidget(clean_check)

        add_btn = QPushButton("+ shot", bar)
        add_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        add_btn.clicked.connect(self.add_shot)
        row.addWidget(add_btn)

        self._branch_btn = QPushButton("branch", bar)
        self._branch_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        self._branch_btn.setToolTip(
            "Save a new version of this file (optionally starting a new stream)"
        )
        self._branch_btn.clicked.connect(self.branch_file)
        row.addWidget(self._branch_btn)

        playblast_btn = QPushButton("playblast all", bar)
        playblast_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        playblast_btn.setToolTip(
            "Render every shot's primary take and review the clips"
        )
        playblast_btn.clicked.connect(self.playblast_all_shots)
        row.addWidget(playblast_btn)
        return bar

    def _show_view(self, view: CutView | TimelineView) -> None:
        """Swap which axis is on screen."""
        if view is self._view:
            return
        self._view = view
        self._views.setCurrentWidget(view)
        # Redundant after a click (the button checked itself), but keeps the
        # lit button honest if this is ever called from anywhere else.
        (self._cut_btn if view is self._cut_view else self._timeline_btn).setChecked(
            True
        )
        view.set_state(self._state)

    def refresh(self) -> None:
        self._view.set_state(self._state)
        self._update_status_text()
        self._update_monitor_label()
        self._update_branch_button()
        active.sync_monitor(self._state)

    def _update_branch_button(self) -> None:
        """Branch only makes sense on an open previs file."""
        self._branch_btn.setEnabled(self._is_previs_file())

    def install_scene_callbacks(self) -> None:
        """Follow the scene for the panel's lifetime.

        The scene node is the state; `self._state` is only a copy of it, so every
        event that can move the node behind the panel's back has to be here.
        """
        for event, handler in (
            ("timeChanged", self._sync_playhead),
            ("Undo", self._reload_state),
            ("Redo", self._reload_state),
            ("SceneOpened", self._adopt_scene),
            ("NewSceneOpened", self._adopt_scene),
        ):
            mc.scriptJob(event=(event, handler), parent=WORKSPACE_CONTROL_NAME)

    def _sync_playhead(self) -> None:
        self._view.sync_playhead()

    def _reload_state(self) -> None:
        """Adopt whatever the scene now says, discarding the panel's copy."""
        scene_state = state.read_state() or PrevisState()
        if scene_state.to_dict() == self._state.to_dict():
            return
        self._state = scene_state
        self.refresh()

    def _adopt_scene(self) -> None:
        """A different file is on screen, so this panel's shots and selection are gone."""
        active.set_selected_shot(None)
        self._synced = None  # a different file's membership is a different fact
        self._state = state.read_state() or PrevisState()
        self.refresh()
        self._offer_sequencer_import()

    def _offer_sequencer_import(self) -> None:
        """Offer to read a legacy file's Camera Sequencer as this file's shot list."""
        if self._state.shots:
            return
        sequence = self._sequence_code()
        result = camera_sequencer.import_from_scene(
            naming.sequence_letter(sequence) if sequence else ""
        )
        if result is None:
            return
        imported, report = result
        if not dialogs.confirm_sequencer_import(self, report):
            return
        # One chunk, so a look at the result undoes back to the legacy sequencer
        # rather than to a file with neither.
        with undo_chunk("previsImportSequencer"):
            camera_sequencer.strip_sequencer()
            self._state = imported
            self._persist()

    def _persist(self) -> None:
        state.write_state(self._state)
        self._sync_manifest()
        self.refresh()

    def _sync_manifest(self) -> None:
        """Push this file's shot codes and membership into the sequence manifest."""
        sequence_code = self._sequence_code()
        filename = self._current_filename()
        if sequence_code is None or filename is None:
            log.debug("Manifest sync skipped: no previs sequence, or scene unsaved.")
            return

        # Codes are already canonical (declare_code / suggest_next enforce it),
        # so a file's membership snapshot joins the manifest's `shots` list on
        # the same key.
        file_codes = tuple(s.code for s in self._state.shots if s.code)
        if self._synced == (filename, file_codes):
            return
        # Recorded before the write: a manifest this build cannot save is not
        # going to become savable on the next drag, and retrying would put the
        # same dialog in front of every edit.
        self._synced = (filename, file_codes)

        def _apply(manifest: SequenceManifest) -> None:
            for code in file_codes:
                manifest.ensure_shot(code)
            manifest.set_membership(filename, list(file_codes))

        try:
            mutate_manifest(sequence_code, _apply)
        except ManifestWriteRefused as exc:
            MessageDialog(self, str(exc), "Previs Manifest").exec_()

    def _current_filename(self) -> str | None:
        """Basename of the open scene on disk, or None if it was never saved."""
        scene = mc.file(query=True, sceneName=True)
        if not isinstance(scene, str) or not scene:
            return None
        return Path(scene).name

    def _update_status_text(self) -> None:
        if not self._is_previs_file():
            self._info.setText("no previs file open")
            return
        self._info.setText(self._compose_info_line())

    def _compose_info_line(self) -> str:
        """The panel's top-bar summary: `<seq_code> · N shots · Mf · X.Xs @ 24fps`."""
        seq = self._sequence_code() or "—"
        shots = self._state.shots
        if not shots:
            return f"{seq}  ·  no shots"
        total_frames = sum(s.primary_duration for s in shots)
        plural = "s" if len(shots) != 1 else ""
        line = (
            f"{seq}  ·  {len(shots)} shot{plural}"
            f"  ·  {total_frames}f  ·  {total_frames / 24.0:.1f}s @ 24fps"
        )
        missing = len(cameras.find_orphan_cameras(self._state))
        if missing:
            return f"{line}  ·  {missing} camera{'s' if missing != 1 else ''} missing"
        return line

    def _sequence_code(self) -> str | None:
        return file_ops.sequence_code()

    def pick_monitor(self) -> None:
        monitor.pick_monitor(on_bound=self._on_monitor_bound)

    def _on_monitor_bound(self, panel: str) -> None:
        self._update_monitor_label()
        active.sync_monitor(self._state)  # show the active shot's camera now

    def _update_monitor_label(self) -> None:
        panel = monitor.get_monitor()
        self._monitor_label.setText(f"monitor: {panel}" if panel else "")

    # ---------- controller methods (called by child widgets) ----------

    def scrub_to_frame(self, frame: int) -> None:
        """Set scene time, then resync the playhead directly.

        The monitor follows via the `timeChanged` job; the playhead we move here
        instead, since that job is coalesced and lags an interactive scrub.
        """
        mc.currentTime(frame)
        self._sync_playhead()

    def select_shot(self, shot_id: str) -> None:
        """Make `shot_id` the shot that wins an overlap."""
        if active.selected_shot_id() == shot_id:
            return
        active.set_selected_shot(shot_id)
        self._view.apply_selection(shot_id)
        self._view.sync_playhead()
        active.sync_monitor(self._state)

    def jump_to_shot(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        self.select_shot(shot_id)
        self.scrub_to_frame(shot.source_in)

    def add_shot(self) -> None:
        if not self._guard_previs_file():
            return
        # The rig reference joins the undo chunk
        with undo_chunk("previsAddShot"):
            ns = cameras.add_new_rig_reference()
            self._state.shots.append(
                PrevisShot(
                    id=state.next_shot_id(),
                    code=self._suggest_code(),
                    source_in=self._state.next_source_in(),
                    takes=[ShotTake(ns)],
                    primary=ns,
                )
            )
            self._persist()

    def _suggest_code(self) -> str:
        """Next free sticky code for this sequence, or "" if the letter can't resolve."""
        seq = self._sequence_code()
        if seq is None:
            return ""
        existing = [s.code for s in self._state.shots if s.code]
        return codes.suggest_next(naming.sequence_letter(seq), existing)

    def branch_file(self) -> None:
        """Checkpoint the open file as its next version, optionally on a new stream."""
        if not self._guard_previs_file():
            return
        request = dialogs.prompt_branch(self)
        if request is None:
            return
        try:
            new_filename = file_ops.branch_current(
                request.note, new_label=request.new_label
            )
        except file_ops.PrevisFileError as exc:
            MessageDialog(self, str(exc), "Cannot Branch File").exec_()
            return
        except Exception as exc:
            log.exception("branch_file failed")
            MessageDialog(self, str(exc), "Branch Failed").exec_()
            return
        self.refresh()
        MessageDialog(
            self,
            f"Branched to {new_filename}. You are now working in the new file.",
            "Branch Previs File",
        ).exec_()

    def remove_shot(self, shot_id: str) -> None:
        """Delete a shot and the cameras it owns, once the artist confirms both."""
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        live = [ns for ns in shot.namespaces if cameras.is_live(ns)]
        # A camera another shot is still cut from outlives this one.
        doomed = [ns for ns in live if len(self._state.shots_using(ns)) == 1]
        kept = [ns for ns in live if ns not in doomed]
        if not dialogs.confirm_delete_shot(
            self,
            label=shot.code or "this shot",
            namespaces=doomed,
            kept=kept,
            undoable=not any(cameras.is_referenced(ns) for ns in doomed),
        ):
            return
        with undo_chunk("previsDeleteShot"):
            for namespace in doomed:
                cameras.delete_camera_rig(namespace)
            self._state.shots = [s for s in self._state.shots if s.id != shot_id]
            self._persist()

    def add_take_new_rig(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        with undo_chunk("previsAddTake"):
            shot.add_take(cameras.add_new_rig_reference())
            self._persist()

    def add_take_duplicate_primary(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        # The rig reference and its copied keys belong to the same edit as the take.
        with undo_chunk("previsDuplicateTake"):
            new_ns = cameras.duplicate_primary(shot)
            if new_ns is not None:
                # A copy of the primary's keys starts out the primary's length.
                shot.add_take(new_ns, shot.primary_duration)
                self._persist()

    def add_take_existing_camera(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        candidates = cameras.find_scene_cameras_outside_state(self._state)
        chosen = dialogs.pick_scene_camera(self, candidates)
        if not chosen:
            return
        with undo_chunk("previsAddTake"):
            shot.add_take(chosen)
            self._persist()

    def look_through_under_cursor(self, namespace: str) -> None:
        """Aim the work viewport under the cursor at `namespace`'s camera.

        Maya viewports aren't Qt drop targets, so a drag released over one never
        fires a dropEvent — we ask Maya which panel the cursor ended over instead.
        """
        panel = cast(str, mc.getPanel(underPointer=True))
        if panel not in (mc.getPanel(type="modelPanel") or []):
            return  # released over the panel's own UI or empty space
        if panel == monitor.get_monitor():
            # The monitor re-aims at the active shot on every time change, so an aim
            # here would just revert
            _assist("The monitor follows the active shot; drop on a work viewport.")
            return
        self._aim_viewport(panel, namespace)

    def look_through(self, namespace: str) -> None:
        """Aim a work viewport at `namespace`'s camera."""
        visible = mc.getPanel(visiblePanels=True) or []
        panels = [
            p
            for p in (mc.getPanel(type="modelPanel") or [])
            if p in visible and p != monitor.get_monitor()
        ]
        if not panels:
            _assist(
                "The monitor follows the active shot; open another viewport to "
                "look through this camera."
                if monitor.get_monitor()
                else "No model viewport open to look through."
            )
            return
        self._aim_viewport(panels[0], namespace)

    def _aim_viewport(self, panel: str, namespace: str) -> None:
        camera_shape = cameras.camera_shape_for_namespace(namespace)
        if not camera_shape:
            _assist(f"{namespace} has no camera in the scene — right-click to re-link.")
            return
        mc.lookThru(panel, camera_shape)

    def promote_to_primary(self, shot_id: str, namespace: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None or shot.primary == namespace:
            return
        if shot.find_take(namespace) is None:
            return
        with undo_chunk("previsPromoteTake"):
            shot.primary = namespace
            self._persist()

    def resize_camera(
        self, shot_id: str, namespace: str, new_length_frames: int
    ) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None or new_length_frames <= 0:
            return
        take = shot.find_take(namespace)
        if take is None or take.duration == new_length_frames:
            return
        with undo_chunk("previsResizeShot"):
            take.duration = new_length_frames
            self._persist()

    def set_source_in(self, shot_id: str, frame: int) -> None:
        """Move where this shot's material sits in scene time."""
        shot = self._state.find_shot(shot_id)
        if shot is None or shot.source_in == int(frame):
            return
        with undo_chunk("previsSetSourceIn"):
            shot.source_in = int(frame)
            self._persist()

    def set_source_in_to_current(self, shot_id: str) -> None:
        """Menu path to the exact frame a four-pixels-per-frame drag can't land on."""
        self.set_source_in(shot_id, int(mc.currentTime(query=True)))

    def trim_head(self, shot_id: str, delta_frames: int) -> None:
        """Move the shot's head by `delta_frames`, holding `source_out` still."""
        shot = self._state.find_shot(shot_id)
        if shot is None or delta_frames == 0:
            return
        take = shot.primary_take
        # Both unreachable from the UI — a takeless shot draws as an inert
        # placeholder, and the handle clamps the drag to leave one frame.
        if take is None or take.duration - delta_frames <= 0:
            return
        with undo_chunk("previsTrimHead"):
            shot.source_in += delta_frames
            take.duration -= delta_frames
            self._persist()

    def preview_resize_camera(
        self, shot_id: str, namespace: str, new_length_frames: int
    ) -> None:
        """Live width preview during a resize drag; no state mutation."""
        self._view.preview_resize(shot_id, namespace, new_length_frames)

    def preview_span(
        self, shot_id: str, *, start_delta: int, length_delta: int
    ) -> None:
        """Live block geometry during a source-axis drag; no state mutation."""
        self._timeline_view.preview_span(
            shot_id, start_delta=start_delta, length_delta=length_delta
        )

    def remove_camera(self, shot_id: str, namespace: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        with undo_chunk("previsRemoveTake"):
            shot.drop_take(namespace)
            self._persist()

    def relink_camera(self, shot_id: str, namespace: str) -> None:
        """Repoint a take at a camera that is actually in the scene, keeping its length."""
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        take = shot.find_take(namespace)
        if take is None:
            return
        candidates = cameras.find_scene_cameras_outside_state(self._state)
        if not candidates:
            MessageDialog(
                self,
                f"There is no untracked camera in the scene to re-link {namespace} to. "
                "Add the camera back, or delete the take.",
                "Re-link Camera",
            ).exec_()
            return
        chosen = dialogs.pick_scene_camera(self, candidates)
        if not chosen:
            return
        # Every shot cut from the dead camera follows it to the new one; leaving
        # the others pointed at a namespace that is gone only hides the problem.
        with undo_chunk("previsRelinkTake"):
            self._repoint_takes(namespace, chosen)
            self._persist()

    def rename_camera(self, shot_id: str, namespace: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        new_name = dialogs.prompt_rename(self, namespace)
        if not new_name:
            return
        # Renaming the namespace and repointing the takes at it is one edit
        with undo_chunk("previsRenameTake"):
            renamed = cameras.rename_camera(namespace, new_name)
            if renamed:
                self._repoint_takes(namespace, new_name)
                self._persist()
        if not renamed:
            MessageDialog(
                self,
                f"Could not rename {namespace} to {new_name} (name in use or namespace missing).",
                "Rename Failed",
            ).exec_()

    def _repoint_takes(self, namespace: str, new_namespace: str) -> None:
        """Move every take on `namespace` to `new_namespace`, across all shots."""
        for shot in self._state.shots_using(namespace):
            shot.retarget_take(namespace, new_namespace)

    def declare_code(self, shot_id: str) -> None:
        """Declare a shot's sticky code from free text

        The artist owns the code; we only canonicalize it and reject the three ways
        it can be wrong: malformed, wrong sequence letter, or already used in this file.
        """
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        seq = self._sequence_code()
        if seq is None:
            MessageDialog(
                self,
                "Could not determine the sequence letter for this file.",
                "Set Shot Code",
            ).exec_()
            return
        letter = naming.sequence_letter(seq)
        raw = dialogs.prompt_shot_code(
            self, current=shot.code, suggestion=self._suggest_code()
        )
        if raw is None:
            return
        try:
            new_code = codes.normalize_code(raw)
        except ValueError:
            MessageDialog(
                self,
                f"'{raw}' is not a valid shot code. Use <LETTER>_<number>, e.g. A_010.",
                "Set Shot Code",
            ).exec_()
            return
        if codes.shot_letter(new_code) != letter:
            MessageDialog(
                self,
                f"Shot code {new_code} does not belong to sequence {letter}. "
                f"Use a {letter}_ code.",
                "Set Shot Code",
            ).exec_()
            return
        if any(s.code == new_code for s in self._state.shots if s.id != shot_id):
            MessageDialog(
                self,
                f"Shot code {new_code} is already used by another shot in this file.",
                "Set Shot Code",
            ).exec_()
            return
        with undo_chunk("previsSetShotCode"):
            shot.code = new_code
            self._persist()

    def move_shot(self, shot_id: str, delta: int) -> None:
        shots = self._state.shots
        index = next((i for i, s in enumerate(shots) if s.id == shot_id), -1)
        if index < 0:
            return
        new_index = max(0, min(len(shots) - 1, index + delta))
        if new_index == index:
            return
        with undo_chunk("previsMoveShot"):
            shots[index], shots[new_index] = shots[new_index], shots[index]
            self._persist()

    def playblast_shot(self, shot_id: str) -> None:
        """Render one shot's primary to a preview and open it in the viewer."""
        shot = self._state.find_shot(shot_id)
        if shot is not None:
            self._launch_playblasts([shot], ask=False)

    def break_out_shot(self, shot_id: str) -> None:
        """Deliver one previs shot to its RLO scene, then come back to previs."""
        shot = self._state.find_shot(shot_id)
        if shot is None or not self._guard_previs_file():
            return
        label = shot.code or shot.id
        try:
            conn = ShotGrid.connect(DB_Config)
            plan = self._plan_break_out(shot, conn)
            if not dialogs.confirm_break_out(self, plan):
                return
            destination = rlo.deliver(plan, shot, self._state, conn)
        except rlo.BreakOutError as exc:
            MessageDialog(self, str(exc), _BREAK_OUT_TITLE).exec_()
            return
        except ShotGridError as exc:
            log.exception("Could not read ShotGrid to break out %s.", label)
            MessageDialog(
                self,
                f"Could not reach ShotGrid, so nothing was broken out:\n{exc}",
                _BREAK_OUT_TITLE,
            ).exec_()
            return
        except Exception as exc:
            log.exception("Break-out of %s failed.", label)
            MessageDialog(
                self, f"Breaking out {label} failed:\n{exc}", _BREAK_OUT_TITLE
            ).exec_()
            return
        # Break-out leaves the previs file reopened, so the panel adopts it now
        # rather than waiting on the idle-time scene job.
        self._adopt_scene()
        published = self._publish_break_out_version(shot, plan.code)
        MessageDialog(
            self,
            f"Broke out {plan.code} to\n{destination}\n\n{published}",
            _BREAK_OUT_TITLE,
        ).exec_()

    def _publish_break_out_version(self, shot: PrevisShot, code: str) -> str:
        """Give the new Shot its first ShotGrid Version, and say how that went."""
        sequence_code = self._sequence_code()
        if sequence_code is None:
            return "No sequence code, so no playblast was published."
        # `_adopt_scene` re-read the file, so prefer the reopened scene's copy of
        # the shot over the one captured before delivery.
        delivered = self._state.find_shot(shot.id) or shot
        try:
            return playblast.deliver_break_out_version(
                delivered, sequence_code, previs_root=get_previs_path()
            )
        except Exception as exc:
            log.exception("Publishing a ShotGrid Version for %s failed.", code)
            return f"No playblast was published for {code}:\n{exc}"

    def _plan_break_out(self, shot: PrevisShot, conn: ShotGrid) -> rlo.DeliveryPlan:
        """What breaking `shot` out would do."""
        sequence_code = self._sequence_code()
        if sequence_code is None:
            raise rlo.BreakOutError(
                "This file is not stamped with a previs sequence code, so break-out "
                "cannot tell which sequence the shot belongs to. Reopen it through "
                "Open Previs in the shelf."
            )
        return rlo.plan_delivery(shot, conn.get_shot(code=sequence_code), conn)

    def playblast_all_shots(self) -> None:
        self._launch_playblasts(self._state.shots, ask=True)

    def _launch_playblasts(self, shots: list[PrevisShot], *, ask: bool) -> None:
        """Render `shots` and hand the clips to the viewer."""
        if not self._guard_previs_file():
            return
        sequence_code = self._sequence_code()
        if sequence_code is None:
            return  # guarded above; re-checked so the type stays narrowed
        if not shots:
            MessageDialog(
                self, "No shots in this file to playblast.", _PLAYBLAST_TITLE
            ).exec_()
            return
        if ask:
            shots = self._pick_shots(shots)
            if not shots:
                return

        batch = self._render_shots(shots, sequence_code)
        if batch is None:
            return
        if batch.failed:
            MessageDialog(
                self, _summarize_skipped(batch.failed), _PLAYBLAST_TITLE
            ).exec_()
        if not batch.clips:
            if not (batch.failed or batch.cancelled):
                MessageDialog(self, "Nothing was rendered.", _PLAYBLAST_TITLE).exec_()
            return

        cut, cut_unavailable = self._stage_cut(batch.clips, sequence_code)
        open_viewer(
            batch.clips,
            parent=get_main_qt_window(),
            cut=cut,
            cut_unavailable=cut_unavailable,
        )

    def _pick_shots(self, shots: list[PrevisShot]) -> list[PrevisShot]:
        """The artist's checklist pick, in cut order; empty when they cancel."""
        rows = [
            dialogs.PlayblastRow(
                shot_id=shot.id,
                label=shot.code or "no code",
                detail=(
                    f"{shot.source_in}–{shot.source_out}  ·  {shot.primary_duration}f"
                ),
                blocker=playblast.render_blocker(shot),
            )
            for shot in shots
        ]
        chosen = dialogs.pick_shots_to_playblast(self, rows)
        if chosen is None:
            return []
        picked = set(chosen)
        return [shot for shot in shots if shot.id in picked]

    def _render_shots(
        self, shots: list[PrevisShot], sequence_code: str
    ) -> playblast.ShotPlayblastBatch | None:
        """Render the batch behind a progress dialog, or None if it broke outright."""
        step = "Rendering shots"
        try:
            with progress_scope(
                parent=self,
                title=_PLAYBLAST_TITLE,
                steps=[step],
                cancellable=True,
            ) as progress:
                progress.begin_step(step)

                def on_shot(index: int, label: str) -> bool:
                    progress.update_substep(index, len(shots), f"Rendering {label}")
                    return not progress.cancelled

                return playblast.build_shot_playblasts(
                    shots,
                    sequence_code,
                    previs_root=get_previs_path(),
                    on_shot=on_shot,
                )
        except Exception as exc:
            log.exception("Previs playblast render failed")
            MessageDialog(self, str(exc), _PLAYBLAST_TITLE).exec_()
            return None

    def _stage_cut(
        self, clips: list[PreviewClip], sequence_code: str
    ) -> tuple[PreviewClip | None, str]:
        """The whole-file cut offered beside the clips, or why there is none."""
        if len(clips) < 2:
            return None, ""
        try:
            cut = playblast.build_cut(
                clips,
                filename=self._current_filename(),
                sequence_code=sequence_code,
                previs_root=get_previs_path(),
            )
        except playblast.PrevisPlayblastError as exc:
            return None, str(exc)
        except Exception as exc:
            log.exception("Previs cut staging failed")
            return None, f"The cut could not be staged. {exc}"
        return cut, ""

    # ---------- helpers ----------

    def _is_previs_file(self) -> bool:
        return self._sequence_code() is not None

    def _guard_previs_file(self) -> bool:
        if self._is_previs_file():
            return True
        MessageDialog(
            self,
            "Open a previs file first (Open Previs in the shelf).",
            "No Previs File",
        ).exec_()
        return False


def _assist(message: str) -> None:
    """Say why a viewport gesture did nothing, where the artist is already looking."""
    mc.inViewMessage(assistMessage=message, position="midCenter", fade=True)


def _view_button(parent: QWidget, text: str, tooltip: str) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setCheckable(True)
    btn.setStyleSheet(style.TOOLBAR_BUTTON)
    btn.setToolTip(tooltip)
    return btn


def _summarize_skipped(failed: list[tuple[str, str]]) -> str:
    """Multi-line list of shots that rendered nothing, with reasons."""
    plural = "s" if len(failed) != 1 else ""
    lines = [f"Skipped {len(failed)} shot{plural}:"]
    lines += [f"  • {label} — {reason}" for label, reason in failed]
    return "\n".join(lines)


# ---------- workspaceControl boilerplate ----------


def _restore() -> None:
    """Called by Maya's workspaceControl restore mechanism."""
    global _panel_instance
    _panel_instance = PrevisPanel(parent=_maya_main_window())
    workspace_ptr = MQtUtil.findControl(WORKSPACE_CONTROL_NAME)
    widget_ptr = MQtUtil.findControl(_panel_instance.objectName())
    if workspace_ptr and widget_ptr:
        MQtUtil.addWidgetToMayaLayout(int(widget_ptr), int(workspace_ptr))
    _panel_instance.install_scene_callbacks()


# Generated from __name__ so an IDE module-rename stays consistent without manual edits.
UI_SCRIPT = f"""
import {__name__}
{__name__}.{_restore.__name__}()
"""


def _maya_main_window() -> QMainWindow:
    ptr = MQtUtil.mainWindow()
    return cast(QMainWindow, wrapInstance(int(ptr), QMainWindow))


def _delete_workspace_control() -> None:
    if mc.workspaceControl(WORKSPACE_CONTROL_NAME, query=True, exists=True):
        mc.workspaceControl(WORKSPACE_CONTROL_NAME, edit=True, close=True)
        mc.deleteUI(WORKSPACE_CONTROL_NAME, control=True)


def launch() -> None:
    global _panel_instance
    _delete_workspace_control()
    _panel_instance = PrevisPanel(parent=get_main_qt_window())
    _panel_instance.show(  # type: ignore[attr-defined]
        dockable=True,
        uiScript=UI_SCRIPT,
        workspaceControlName=WORKSPACE_CONTROL_NAME,
    )
    _panel_instance.install_scene_callbacks()
    # After the panel is docked, never during construction: `_restore` builds one
    # too, and a modal there would block Maya mid-layout-restore.
    _panel_instance._offer_sequencer_import()


def close() -> None:
    global _panel_instance
    if _panel_instance is not None:
        _panel_instance.close()
