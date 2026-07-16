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
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pipe.core.previs import codes, mutate_manifest, naming
from pipe.core.shotgrid import ShotGrid, is_previs_shot_code
from pipe.core.ui import MessageDialog, MessageDialogCustomButtons
from pipe.core.util.paths import get_production_path

from pipe.dcc.maya.runtime import get_main_qt_window

from . import (
    breakout,
    cameras,
    dialogs,
    export,
    file_ops,
    monitor,
    playback,
    publish,
    state,
    status,
    style,
)
from .state import PrevisShot, PrevisState
from .timeline import PrevisTimeline

if TYPE_CHECKING:
    from pipe.core.previs.model import SequenceManifest

log = logging.getLogger(__name__)

PANEL_OBJECT_NAME = "previsPanel"
WORKSPACE_CONTROL_NAME = PANEL_OBJECT_NAME + "WorkspaceControl"

_panel_instance: PrevisPanel | None = None


class PrevisPanel(MayaQWidgetDockableMixin, QWidget):  # type: ignore[misc]
    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent=parent)
        self.setObjectName(PANEL_OBJECT_NAME)
        self.setWindowTitle("Previs Sequencer")
        self.setStyleSheet(f"#{PANEL_OBJECT_NAME} {{ background: {style.PANEL_BG}; }}")

        self._state = state.read_state() or PrevisState.empty()
        self._sg_conn: ShotGrid | None = None

        self._build_ui()
        self.refresh()

    # ---------- UI scaffolding ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_top_bar())
        self._timeline = PrevisTimeline(self, parent=self)
        root.addWidget(self._timeline, 1)

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

        breakout_btn = QPushButton("break out all", bar)
        breakout_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        breakout_btn.clicked.connect(self.break_out_all)
        row.addWidget(breakout_btn)

        publish_btn = QPushButton("publish all cams", bar)
        publish_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        publish_btn.clicked.connect(self.publish_all_shot_cameras)
        row.addWidget(publish_btn)

        export_btn = QPushButton("export takes", bar)
        export_btn.setStyleSheet(style.TOOLBAR_BUTTON)
        export_btn.setToolTip("Render every shot's primary to a new immutable take")
        export_btn.clicked.connect(self.export_all_takes)
        row.addWidget(export_btn)
        return bar

    def refresh(self) -> None:
        self._timeline.set_state(self._state)
        self._update_status_text()
        self._update_monitor_label()
        self._update_branch_button()
        self._warn_orphans()

    def _update_branch_button(self) -> None:
        """Branch only makes sense on an open previs file."""
        self._branch_btn.setEnabled(self._is_previs_file())

    def install_playhead_callback(self) -> None:
        """Resync the playhead on every scene time change, for the panel's lifetime.

        Parented to the workspaceControl so Maya kills the job when the panel closes —
        deliberately separate from playback.py's file-scoped monitor job.
        """
        mc.scriptJob(
            event=("timeChanged", self._timeline.sync_playhead),
            parent=WORKSPACE_CONTROL_NAME,
        )

    def _persist(self) -> None:
        state.write_state(self._state)
        self._sync_manifest()
        self.refresh()

    def _sync_manifest(self) -> None:
        """Push this file's shot codes and membership into the sequence manifest.

        Runs on every _persist, so the manifest tracks each edit without scene
        callbacks. When the scene is not a saved previs file there is nothing to
        map membership onto, so this logs why and returns.
        """
        sequence_code = self._sequence_code()
        if sequence_code is None:
            log.debug("Manifest sync skipped: file has no previs sequence code.")
            return
        filename = self._current_filename()
        if filename is None:
            log.debug("Manifest sync skipped: scene has not been saved to disk.")
            return

        # Codes are already canonical (declare_code / suggest_next enforce it),
        # so a file's membership snapshot joins the manifest's `shots` list on
        # the same key.
        file_codes = [s.code for s in self._state.shots if s.code]

        def _apply(manifest: SequenceManifest) -> None:
            for code in file_codes:
                manifest.ensure_shot(code)
            manifest.set_membership(filename, file_codes)

        mutate_manifest(sequence_code, _apply)

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
        return (
            f"{seq}  ·  {len(shots)} shot{plural}"
            f"  ·  {total_frames}f  ·  {total_frames / 24.0:.1f}s @ 24fps"
        )

    def _sequence_code(self) -> str | None:
        """Sequence-proxy code from `fileInfo` (e.g. `A_previs`), or None if absent/invalid."""
        raw = mc.fileInfo("code", query=True)
        code = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
        if not isinstance(code, str):
            return None
        return code if is_previs_shot_code(code) else None

    def pick_monitor(self) -> None:
        monitor.pick_monitor(on_bound=self._on_monitor_bound)

    def _on_monitor_bound(self, panel: str) -> None:
        self._update_monitor_label()
        playback.sync_monitor()  # show the current shot's camera immediately

    def _update_monitor_label(self) -> None:
        panel = monitor.get_monitor()
        self._monitor_label.setText(f"monitor: {panel}" if panel else "")

    def _warn_orphans(self) -> None:
        orphans = cameras.find_orphan_cameras(self._state)
        if orphans:
            dialogs.show_orphan_warning(self, orphans)

    # ---------- controller methods (called by child widgets) ----------

    def scrub_to_frame(self, frame: int) -> None:
        """Set scene time, then resync the playhead directly.

        The monitor follows via the `timeChanged` job; the playhead we move here
        instead, since that job is coalesced and lags an interactive scrub.
        """
        mc.currentTime(frame)
        self._timeline.sync_playhead()

    def jump_to_shot(self, shot_id: str) -> None:
        ranges = playback.compute_shot_ranges(self._state)
        shot_range = ranges.get(shot_id)
        if shot_range is not None:
            self.scrub_to_frame(shot_range[0])

    def add_shot(self) -> None:
        if not self._guard_previs_file():
            return
        ns = cameras.add_new_rig_reference()
        new_shot = PrevisShot(
            id=state.next_shot_id(),
            code=self._suggest_code(),
            primary=ns,
            durations={ns: state.DEFAULT_SHOT_DURATION},
        )
        self._state.shots.append(new_shot)
        self._persist()

    def _suggest_code(self) -> str:
        """Next free sticky code for this sequence, or "" if the letter can't resolve."""
        seq = self._sequence_code()
        if seq is None:
            return ""
        existing = [s.code for s in self._state.shots if s.code]
        return codes.suggest_next(seq[0], existing)

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
        self._state.shots = [s for s in self._state.shots if s.id != shot_id]
        self._persist()

    def add_alternate_new_rig(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        ns = cameras.add_new_rig_reference()
        shot.alternates.append(ns)
        shot.durations[ns] = state.DEFAULT_SHOT_DURATION
        self._persist()

    def add_alternate_duplicate_primary(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        new_ns = cameras.duplicate_primary(shot)
        if new_ns is None:
            return
        shot.alternates.append(new_ns)
        # Inherit the primary's duration — the duplicate IS the primary, at this moment.
        shot.durations[new_ns] = shot.primary_duration
        self._persist()

    def add_alternate_existing_camera(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        candidates = cameras.find_scene_cameras_outside_state(self._state)
        chosen = dialogs.pick_scene_camera(self, candidates)
        if not chosen:
            return
        shot.alternates.append(chosen)
        shot.durations[chosen] = state.DEFAULT_SHOT_DURATION
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
            mc.inViewMessage(
                assistMessage="The monitor follows the active shot; drop on a work viewport.",
                position="midCenter",
                fade=True,
            )
            return
        camera_shape = cameras.camera_shape_for_namespace(namespace)
        if camera_shape:
            mc.lookThru(panel, camera_shape)

    def promote_to_primary(self, shot_id: str, namespace: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None or shot.primary == namespace:
            return
        if namespace not in shot.alternates:
            return
        shot.alternates.remove(namespace)
        if shot.primary:
            shot.alternates.insert(0, shot.primary)
        shot.primary = namespace
        self._persist()

    def resize_camera(
        self, shot_id: str, namespace: str, new_length_frames: int
    ) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None or new_length_frames <= 0:
            return
        if shot.duration_of(namespace) == new_length_frames:
            return
        shot.durations[namespace] = new_length_frames
        self._persist()

    def preview_resize_camera(
        self, shot_id: str, namespace: str, new_length_frames: int
    ) -> None:
        """Live column-width preview during a resize drag; no state mutation."""
        self._timeline.preview_column_width(shot_id, namespace, new_length_frames)

    def remove_camera(self, shot_id: str, namespace: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        cameras.remove_camera_from_shot(shot, namespace)
        self._persist()

    def rename_camera(self, shot_id: str, namespace: str) -> None:
        new_name = dialogs.prompt_rename(self, namespace)
        if not new_name:
            return
        if not cameras.rename_camera(namespace, new_name):
            MessageDialog(
                self,
                f"Could not rename {namespace} to {new_name} (name in use or namespace missing).",
                "Rename Failed",
            ).exec_()
            return
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        if shot.primary == namespace:
            shot.primary = new_name
        shot.alternates = [new_name if a == namespace else a for a in shot.alternates]
        if namespace in shot.durations:
            shot.durations[new_name] = shot.durations.pop(namespace)
        self._persist()

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
        letter = seq[0]
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
        if new_code[0] != letter:
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
        shots[index], shots[new_index] = shots[new_index], shots[index]
        self._persist()

    def break_out_shot(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        if not shot.shotgrid_code:
            MessageDialog(
                self,
                "Assign a ShotGrid code to this shot before breaking out.",
                "Break Out to RLO",
            ).exec_()
            return
        if not self._confirm_break_out([shot]):
            return
        try:
            conn = self._conn()
            shot_range = playback.compute_shot_ranges(self._state)[shot.id]
            sg_shot = conn.get_shot(code=shot.shotgrid_code)
            breakout.break_out_shot(shot, sg_shot, shot_range, conn)
        except Exception as exc:
            log.exception("break_out_shot failed")
            MessageDialog(self, str(exc), "Break Out Failed").exec_()
            return
        self._persist()

    def break_out_all(self) -> None:
        paired = [s for s in self._state.shots if s.shotgrid_code]
        if not paired:
            MessageDialog(
                self,
                "No shots have a ShotGrid code yet. Assign codes before breaking out.",
                "Break Out to RLO",
            ).exec_()
            return
        if not self._confirm_break_out(paired):
            return
        try:
            paths = breakout.break_out_sequence(self._state, self._conn())
        except Exception as exc:
            log.exception("break_out_all failed")
            MessageDialog(self, str(exc), "Break Out Failed").exec_()
            return
        MessageDialog(
            self, f"Broke out {len(paths)} shot(s).", "Break Out to RLO"
        ).exec_()
        self._persist()

    def publish_shot_camera(self, shot_id: str) -> None:
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        if not shot.shotgrid_code:
            MessageDialog(
                self,
                "Assign a ShotGrid code to this shot before publishing its camera.",
                "Publish Shot Camera",
            ).exec_()
            return
        try:
            sg_shot = self._conn().get_shot(code=shot.shotgrid_code)
            publish.publish_shot_camera(shot, sg_shot)
        except Exception as exc:
            log.exception("publish_shot_camera failed")
            MessageDialog(self, str(exc), "Publish Failed").exec_()
            return
        self._persist()

    def publish_all_shot_cameras(self) -> None:
        paired = [s for s in self._state.shots if s.shotgrid_code]
        if not paired:
            MessageDialog(
                self,
                "No shots have a ShotGrid code yet. Assign codes before publishing.",
                "Publish Shot Cameras",
            ).exec_()
            return
        try:
            paths = publish.publish_all_shot_cameras(self._state, self._conn())
        except Exception as exc:
            log.exception("publish_all_shot_cameras failed")
            MessageDialog(self, str(exc), "Publish Failed").exec_()
            return
        MessageDialog(
            self, f"Published {len(paths)} shot camera(s).", "Publish Shot Cameras"
        ).exec_()
        self._persist()

    def export_take(self, shot_id: str) -> None:
        """Render one shot's primary to a new take, then report the outcome."""
        if not self._guard_previs_file():
            return
        shot = self._state.find_shot(shot_id)
        if shot is None:
            return
        sequence_code = self._sequence_code()
        if sequence_code is None:
            return  # guarded above; re-checked so the type stays narrowed
        cut_in, cut_out = playback.compute_shot_ranges(self._state)[shot.id]
        try:
            result = export.export_take(shot, cut_in, cut_out, sequence_code)
        except export.PrevisExportError as exc:
            MessageDialog(self, str(exc), "Export Take").exec_()
            return
        except Exception as exc:
            log.exception("export_take failed")
            MessageDialog(self, str(exc), "Export Failed").exec_()
            return
        MessageDialog(self, _describe_take(result), "Export Take").exec_()

    def export_all_takes(self) -> None:
        if not self._guard_previs_file():
            return
        sequence_code = self._sequence_code()
        if sequence_code is None:
            return
        if not self._state.shots:
            MessageDialog(
                self, "No shots in this file to export.", "Export Takes"
            ).exec_()
            return
        result = export.export_all_takes(self._state, sequence_code)
        MessageDialog(self, _summarize_take_batch(result), "Export Takes").exec_()

    def _confirm_break_out(self, shots: list[PrevisShot]) -> bool:
        """Confirm a destructive re-bake, flagging any RLO files it would overwrite."""
        prod_root = get_production_path()
        overwrites = [
            s.shotgrid_code
            for s in shots
            if s.shotgrid_code and status.rlo_path(s.shotgrid_code, prod_root).exists()
        ]
        plural = "s" if len(shots) != 1 else ""
        body = (
            f"Break out {len(shots)} shot{plural} to RLO? "
            "This re-bakes the scene from scratch."
        )
        if overwrites:
            body += "\n\nOverwrites existing RLO files:\n" + "\n".join(
                f"  • {code}" for code in overwrites
            )
        return bool(
            MessageDialogCustomButtons(
                self,
                body,
                "Break Out to RLO",
                has_cancel_button=True,
                ok_name="Break Out",
                cancel_name="Cancel",
            ).exec_()
        )

    # ---------- helpers ----------

    def _conn(self) -> ShotGrid:
        if self._sg_conn is None:
            self._sg_conn = ShotGrid.connect(DB_Config)
        return self._sg_conn

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


def _describe_take_delta(result: export.TakeResult) -> str:
    """How this take's length compares to the shot's previous take, as a phrase."""
    delta = result.length_delta
    if delta is None:
        return "first take"
    if delta == 0:
        return "same length"
    return f"{delta:+d}f vs previous"


def _describe_take(result: export.TakeResult) -> str:
    """One-line success sentence for a single take export."""
    return (
        f"Exported take {naming.version_token(result.version)} of {result.code} "
        f"— {result.duration_frames}f ({_describe_take_delta(result)})."
    )


def _summarize_take_batch(result: export.BatchResult) -> str:
    """Multi-line summary: exported takes with deltas, then skipped shots with reasons."""
    lines: list[str] = []
    if result.exported:
        plural = "s" if len(result.exported) != 1 else ""
        lines.append(f"Exported {len(result.exported)} take{plural}.")
        lines.append("")
        for take in result.exported:
            lines.append(
                f"  • {take.code}  {naming.version_token(take.version)}  "
                f"{take.duration_frames}f  ({_describe_take_delta(take)})"
            )
    if result.failed:
        if lines:
            lines.append("")
        plural = "s" if len(result.failed) != 1 else ""
        lines.append(f"Skipped {len(result.failed)} shot{plural}:")
        for label, reason in result.failed:
            lines.append(f"  • {label} — {reason}")
    return "\n".join(lines) if lines else "Nothing to export."


# ---------- workspaceControl boilerplate ----------


def _restore() -> None:
    """Called by Maya's workspaceControl restore mechanism."""
    global _panel_instance
    _panel_instance = PrevisPanel(parent=_maya_main_window())
    workspace_ptr = MQtUtil.findControl(WORKSPACE_CONTROL_NAME)
    widget_ptr = MQtUtil.findControl(_panel_instance.objectName())
    if workspace_ptr and widget_ptr:
        MQtUtil.addWidgetToMayaLayout(int(widget_ptr), int(workspace_ptr))
    _panel_instance.install_playhead_callback()


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
    _panel_instance.install_playhead_callback()


def close() -> None:
    global _panel_instance
    if _panel_instance is not None:
        _panel_instance.close()
