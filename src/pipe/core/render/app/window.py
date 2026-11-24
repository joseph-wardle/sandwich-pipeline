"""The Sandwich Renderer window: choose a shot, edit its render layers, send them."""

from __future__ import annotations

import getpass
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.app.log_viewer import LogViewer
from pipe.core.render.app.departments import DepartmentPanel
from pipe.core.render.app.farm_tab import FarmTab
from pipe.core.render.app.overview import OverviewPage
from pipe.core.render.app.form import ParmForm
from pipe.core.render.app.session import HoudiniSession
from pipe.core.render.protocol import (
    LIGHTING_HIP,
    OPEN,
    PUBLISH_LIGHTING,
    REMOVE_INSTANCE,
    SAVE,
    SEND,
    SET_DEPARTMENT,
    SET_PARM,
    Layer,
    LightingTarget,
    Reply,
    shot_folder,
)
from pipe.core.render.app.scene_graph import SceneGraphPanel
from pipe.core.render.app import theme
from pipe.core.render.app.shots import ShotList
from pipe.core.render.app.versions_tab import VersionsTab
from pipe.core.render.app.viewer import FrameViewer
from pipe.core.render.lighters import LIGHTING_STEP
from pipe.core.publish import Target
from pipe.core.shotgrid import Shot, ShotGrid, Status, is_previs_shot_code
from pipe.core.render import render_status
from pipe.core.render.app import background
from pipe.core.render.render_status import RenderBoard, RenderStatus
from pipe.core.ui import prompt_publish

TITLE = "Sandwich Renderer"
# How far the shot's title and render hip sit in from the edge, in pixels.
_TITLE_INSET = 12
# Requests the artist waits for. Parm edits answer too fast to need it.
_BUSY_WORDS = {
    OPEN: "Opening {shot} in Houdini…",
    SET_DEPARTMENT: "Loading {shot}'s department layers…",
    SAVE: "Saving {shot}'s render hip…",
    SEND: "Saving {shot} and sending it to Tractor…",
    PUBLISH_LIGHTING: "Publishing {shot}'s lighting hip…",
}
CANCELLED = (
    "Cancelled. Nothing was saved or sent unless the log says so. Reload to open "
    "the shot again."
)
_FRAME_RANGES = {
    "off": "the hip's current frame only",
    "stage": "the frame range the stage says",
}
_NOTE_STYLE = "QLabel { background-color: #4a3f1c; color: #f3e4b0; border-radius: 4px; padding: 6px; }"


class RenderNetworkWindow(QtWidgets.QWidget):
    _shot_list: ShotList
    _heading: QtWidgets.QLabel
    _hip: QtWidgets.QLabel
    _notes: QtWidgets.QLabel
    _scroll: QtWidgets.QScrollArea
    _scene_graph: SceneGraphPanel
    _departments: DepartmentPanel
    _tabs: QtWidgets.QTabWidget
    _farm: FarmTab
    _overview: OverviewPage
    _pages: QtWidgets.QTabWidget
    _viewer: FrameViewer
    _versions: VersionsTab
    _busy: QtWidgets.QWidget
    _busy_text: QtWidgets.QLabel
    _log: LogViewer
    _reload: QtWidgets.QPushButton
    _save: QtWidgets.QPushButton
    _send: QtWidgets.QPushButton

    _houdini: HoudiniSession | None
    _form: ParmForm | None
    _layers: list[Layer]
    _lighting_target: LightingTarget | None
    _working: bool
    _dirty: bool
    # Which folders were open, by shot, so a reload keeps the artist's place.
    _form_states: dict[str, dict[str, Any]]
    # The shot whose parms show, or are opening.
    _shown: str | None

    def __init__(
        self,
        shots: Sequence[Shot],
        open_shot: str | None = None,
        lit_shots: set[str] | None = None,
        admin: bool = False,
        render: RenderBoard | None = None,
    ) -> None:
        """`lit_shots` are the codes the artist is assigned to light, which they
        may publish lighting for; None when ShotGrid couldn't say. An `admin`
        may publish any shot's lighting. `render` is each shot's Render
        task status, shown beside it; the same artists may change it."""
        super().__init__()
        self._admin = admin
        self._shots_by_code = {shot.code or "": shot for shot in shots}
        self._lit_shots = lit_shots
        self.setWindowTitle(TITLE)
        self.resize(1280, 900)
        self._houdini, self._form, self._lighting_target = None, None, None
        self._layers, self._working, self._dirty = [], False, False
        self._form_states = {}
        self._shown = None

        self._shot_list = ShotList(
            shots,
            lit_shots or set(),
            render,
            lambda code: self._lighter_refusal(code, "change it"),
        )
        self._shot_list.chosen.connect(self._choose_shot)
        self._shot_list.render_status_chosen.connect(self._change_render_status)

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(self._shot_list)
        splitter.addWidget(self._build_main())
        # Extra width goes to the main area; the shot list never shrinks for it.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        theme.shade(self._shot_list, theme.SHOTS)
        splitter.setSizes([200, 1080])
        # The farm as a whole, beside the shot by shot work.
        self._overview = OverviewPage(getpass.getuser())
        self._overview.open_job.connect(self._open_farm_job)
        self._pages = QtWidgets.QTabWidget()
        self._pages.addTab(splitter, "Shots")
        self._pages.addTab(self._overview, "Tractor Overview")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._pages)

        # Qt 6 moved QShortcut from QtWidgets to QtGui.
        shortcut = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
        save = shortcut(QtGui.QKeySequence(QtGui.QKeySequence.Save), self)
        save.activated.connect(self._on_save)
        self._set_busy(None)
        self._show_empty()
        if open_shot:
            self._shot_list.select(open_shot)
            self._choose_shot(open_shot)

    # ---- layout --------------------------------------------------------

    def _build_main(self) -> QtWidgets.QWidget:
        self._heading = QtWidgets.QLabel()
        self._heading.setStyleSheet("font-size: 18pt; font-weight: bold;")
        self._hip = QtWidgets.QLabel()
        self._hip.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._hip.setWordWrap(True)
        # Off the window's edge, so the shot reads as a title over the panels.
        for label in (self._heading, self._hip):
            label.setContentsMargins(_TITLE_INSET, 0, 0, 0)
        self._heading.setContentsMargins(_TITLE_INSET, 6, 0, 2)
        self._notes = QtWidgets.QLabel()
        self._notes.setWordWrap(True)
        self._notes.setStyleSheet(_NOTE_STYLE)

        self._publish = QtWidgets.QPushButton("Publish Lighting")
        self._publish.clicked.connect(self._on_publish)
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setObjectName(theme.PARMS)
        self._scroll.setWidgetResizable(True)
        self._scene_graph = SceneGraphPanel()
        # Publish Lighting sits under the lighting layer it publishes.
        self._departments = DepartmentPanel(self._publish)
        self._departments.hide()
        self._departments.changed.connect(self._set_department)
        stage_side = QtWidgets.QWidget()
        theme.shade(stage_side, theme.STAGE)
        stage_layout = QtWidgets.QVBoxLayout(stage_side)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.addWidget(self._departments)
        stage_layout.addWidget(self._scene_graph, 1)
        parms_and_stage = QtWidgets.QSplitter()
        parms_and_stage.addWidget(self._scroll)
        parms_and_stage.addWidget(stage_side)
        parms_and_stage.setStretchFactor(0, 3)
        parms_and_stage.setStretchFactor(1, 2)
        parms_and_stage.setSizes([640, 420])

        self._reload = QtWidgets.QPushButton("Reload")
        self._reload.setToolTip(
            "Open the render hip from disk again, dropping unsaved edits"
        )
        self._reload.clicked.connect(self._on_reload)
        self._save = QtWidgets.QPushButton("Save")
        self._save.setToolTip("Save these settings into the shot's render hip (Ctrl+S)")
        self._save.clicked.connect(self._on_save)
        self._send = QtWidgets.QPushButton("Send To Tractor")
        self._send.setToolTip("Save, then render every layer that is on")
        self._send.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px 18px; }"
        )
        self._send.clicked.connect(self._on_send)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(6, 6, 6, 6)
        buttons.addWidget(self._reload)
        buttons.addWidget(self._save)
        buttons.addStretch(1)
        buttons.addWidget(self._send)
        # The buttons act on the parms, so they live on the parms' tab.
        parms_page = QtWidgets.QWidget()
        parms_layout = QtWidgets.QVBoxLayout(parms_page)
        parms_layout.setContentsMargins(0, 0, 0, 0)
        parms_layout.setSpacing(0)
        parms_layout.addWidget(parms_and_stage, 1)
        parms_layout.addLayout(buttons)

        self._busy_text = QtWidgets.QLabel()
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 0)
        bar.setMaximumWidth(160)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self._cancel)
        self._busy = QtWidgets.QWidget()
        busy_layout = QtWidgets.QHBoxLayout(self._busy)
        busy_layout.setContentsMargins(_TITLE_INSET, 0, 0, 0)
        busy_layout.addWidget(bar)
        busy_layout.addWidget(self._busy_text, 1)
        busy_layout.addWidget(cancel)

        self._log = LogViewer()

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self._heading)
        top_layout.addWidget(self._hip)
        top_layout.addWidget(self._notes)
        # The farm and the frames need no Houdini, so they show while it opens.
        self._farm = FarmTab()
        self._viewer = FrameViewer()
        self._versions = VersionsTab(
            sorted(c for c in self._lit_shots or () if not is_previs_shot_code(c))
        )
        self._versions.deleted.connect(self._viewer.reread)
        self._farm.view_frames.connect(self._view_frames)
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.addTab(parms_page, "Parameters")
        self._tabs.addTab(self._farm, "Farm")
        self._tabs.addTab(self._viewer, "Images")
        self._tabs.addTab(self._versions, "Versions")
        top_layout.addWidget(self._tabs, 1)
        top_layout.addWidget(self._busy)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self._log)
        split.setStretchFactor(0, 1)
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        return split

    # ---- choosing a shot -----------------------------------------------

    def _choose_shot(self, code: str) -> None:
        if code == self._shown:
            return
        previous = self._shown
        self._after_unsaved(
            lambda: self._open(code), lambda: self._shot_list.select(previous)
        )

    def _open(self, code: str) -> None:
        self._shown = code
        self._heading.setText(code)
        self._farm.set_shot(code)
        self._viewer.set_shot(code)
        self._versions.set_shot(code, self._lighter_refusal(code, "delete its renders"))
        self._open_houdini(code)

    def _open_houdini(self, code: str) -> None:
        self._close_houdini()
        self._forget_form()
        self._departments.hide()
        self._hip.setText("")
        self._notes.hide()
        self._scene_graph.show_prims([])
        self._houdini = HoudiniSession(code, self)
        self._houdini.output.connect(self._log_line)
        self._houdini.stopped.connect(self._houdini_stopped)
        self._request(OPEN, then=self._opened, failed=self._not_opened)

    def _after_unsaved(
        self, proceed: Callable[[], None], stay: Callable[[], None]
    ) -> None:
        """Run `proceed` once unsaved edits are saved or dropped, else `stay`."""
        if not self._dirty or self._form is None:
            proceed()
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(TITLE)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(f"Save your changes to {self._shown} first?")
        box.setStandardButtons(
            QtWidgets.QMessageBox.Save
            | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel
        )
        answer = box.exec_()
        if answer == QtWidgets.QMessageBox.Cancel:
            stay()
        elif answer == QtWidgets.QMessageBox.Discard:
            self._dirty = False
            proceed()
        else:

            def saved(reply: Reply) -> None:
                self._saved(reply)
                proceed()

            self._request(SAVE, then=saved, failed=stay)

    # ---- actions -------------------------------------------------------

    def _on_reload(self) -> None:
        if (code := self._shown) is not None and not self._working:
            self._after_unsaved(lambda: self._reopen(code), lambda: None)

    def _reopen(self, code: str) -> None:
        if self._houdini is None:
            self._open_houdini(code)
        else:
            self._request(OPEN, then=self._opened, failed=self._not_opened)

    def _on_save(self) -> None:
        if self._form is not None and not self._working:
            self._request(SAVE, then=self._saved)

    def _on_send(self) -> None:
        if self._form is not None and not self._working and self._confirm_send():
            self._request(SEND, then=self._sent)

    def _set_parm(self, name: str, value: object) -> None:
        self._edited()
        self._request(SET_PARM, {"name": name, "value": value})

    def _remove_instance(self, name: str, position: int) -> None:
        self._edited()
        self._request(REMOVE_INSTANCE, {"name": name, "position": position})

    def _set_department(self, row: str, enabled: bool, version: str) -> None:
        self._edited()
        self._request(
            SET_DEPARTMENT, {"row": row, "enabled": enabled, "version": version}
        )

    def _confirm_send(self) -> bool:
        chosen = [layer.name for layer in self._layers if self._value(layer.toggle)]
        if not chosen:
            _tell(
                self,
                "Turn on at least one layer to send.",
                QtWidgets.QMessageBox.Information,
            )
            return False
        lines = [
            f"Layers: {', '.join(chosen)}",
            f"Frames: {self._describe_frames()}",
            f"Priority: {self._value('priority')}",
        ]
        answer = QtWidgets.QMessageBox.question(
            self,
            TITLE,
            f"Send {self._shown} to Tractor?\n\n"
            + "\n".join(lines)
            + "\n\nThe render hip is saved with these settings first.",
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _describe_frames(self) -> str:
        mode = str(self._value("framerange"))
        if mode in _FRAME_RANGES:
            return _FRAME_RANGES[mode]
        frames = self._form.evaluated("f") if self._form else []
        if len(frames) != 3:
            return "as set on the hip"
        start, end, step = (int(v) for v in frames)
        by = f" every {step}" if step != 1 else ""
        return f"{start}–{end}{by}"

    def _value(self, name: str) -> object:
        """What the parm's first component evaluates to in Houdini, else None."""
        evaluated = self._form.evaluated(name) if self._form else []
        return evaluated[0] if evaluated else None

    def _enable_publish(self, busy: bool) -> None:
        refusal = self._publish_refusal()
        self._publish.setEnabled(not refusal and not busy)
        lighter = self._lit_shots is not None and self._shown in self._lit_shots
        admin_note = "" if lighter or not self._admin else " (as a pipeline admin)"
        self._publish.setToolTip(
            refusal
            or f"Publish {self._shown}'s lighting hip, as last saved, as its next "
            f"lighting version{admin_note}"
        )

    def _publish_refusal(self) -> str:
        """Why Publish Lighting is off for the shown shot, or "" when it's on."""
        shot = self._shown
        if shot is None:
            return "Choose a shot first."
        if self._admin:
            pass
        elif self._lit_shots is None:
            return "ShotGrid couldn't say which shots you light. Reopen the window."
        elif shot not in self._lit_shots:
            return (
                f"Only {shot}'s lighter can publish its lighting: ShotGrid assigns "
                f"its {LIGHTING_STEP} task to someone else."
            )
        if self._lighting_target is None:
            return "Waiting for Houdini to read the shot's lighting publishes."
        return ""

    def _lighter_refusal(self, code: str, action: str) -> str:
        """Why the artist can't `action` in shot `code`, or "" when they light it or are an admin."""
        if self._admin or (self._lit_shots is not None and code in self._lit_shots):
            return ""
        if self._lit_shots is None:
            return "ShotGrid couldn't say which shots you light. Reopen the window."
        return f"Only {code}'s lighter or a pipeline admin can {action}."

    def _change_render_status(self, code: str, status: object) -> None:
        current = self._shot_list.render_status(code)
        if not isinstance(status, Status) or current is None:
            return
        if refusal := self._lighter_refusal(code, "change it"):
            _tell(self, refusal, QtWidgets.QMessageBox.Information)
            return
        self._log_line(f"Setting {code}'s {current.task} to {status.name} in ShotGrid…")

        def change() -> RenderStatus:
            from env_sg import DB_Config

            return render_status.change(ShotGrid.connect(DB_Config), current, status)

        def changed(render: RenderStatus) -> None:
            self._shot_list.set_render_status(code, render)
            self._log_line(f"{code}'s {render.task} is now {render.name} in ShotGrid.")

        def failed(exc: Exception) -> None:
            _tell(
                self,
                f"ShotGrid didn't change {code}'s {current.task} to {status.name}.",
                QtWidgets.QMessageBox.Warning,
                str(exc),
            )

        background.run(change, changed, failed)

    def _on_publish(self) -> None:
        code = self._shown
        if code is None or self._working or code not in self._shots_by_code:
            return
        hip = shot_folder(code) / LIGHTING_HIP
        if not hip.is_file():
            _tell(
                self,
                f"{code} has no lighting hip at {hip} yet, so there is nothing to publish.",
                QtWidgets.QMessageBox.Information,
            )
            return
        saved = datetime.fromtimestamp(hip.stat().st_mtime).strftime("%b %d at %H:%M")
        notice = (
            f"Publishes {hip} as it was last saved, {saved}. Changes not saved in an "
            "open Houdini session are left out, so save there first."
        )
        if (known := self._lighting_target) is None:
            return
        target = Target(
            entity=self._shots_by_code[code],
            name=known.name,
            current=Path(known.current),
            version=known.version,
            file_name=known.file_name,
            file_code=known.file_code,
            department=known.department,
            final=known.final,
        )
        choice = prompt_publish(self, target, notice, can_playblast=False)
        if choice is None:
            return
        # Houdini opens the render hip again after, which drops unsaved edits.
        self._after_unsaved(
            lambda: self._request(
                PUBLISH_LIGHTING,
                {
                    "note": choice.note,
                    "announce": choice.announce,
                    "final": choice.final,
                },
                then=self._published_lighting,
            ),
            lambda: None,
        )

    def _published_lighting(self, reply: Reply) -> None:
        self._opened(reply)
        _tell(
            self,
            "\n".join(reply.published or []) or "Published.",
            QtWidgets.QMessageBox.Information,
        )

    def _open_farm_job(self, shot: str, jid: int) -> None:
        """Show a job the Overview chose, on its shot's Farm tab."""
        self._pages.setCurrentIndex(0)
        self._shot_list.select(shot)
        self._choose_shot(shot)
        # The artist may have kept unsaved edits to the shot that was open.
        if self._shown == shot:
            self._tabs.setCurrentWidget(self._farm)
            self._farm.show_job(shot, jid)

    def _view_frames(self, layer: str, folder: object, frame: object) -> None:
        self._viewer.show_frames(
            layer,
            folder if isinstance(folder, Path) else None,
            frame if isinstance(frame, int) else None,
        )
        self._tabs.setCurrentWidget(self._viewer)

    def _saved(self, reply: Reply) -> None:
        self._mark_saved()
        self._log_line(f"Saved {reply.hip}")

    def _sent(self, reply: Reply) -> None:
        self._mark_saved()
        sent = reply.sent
        if sent is None:
            return
        folders = "\n".join(sent.folders)
        if sent.job_id is None:
            _tell(
                self,
                "Tractor did not confirm the job, but it may still have been queued. "
                f'Look for "{sent.title}" in Tractor before sending again.\n\n'
                f"Its folders were kept:\n{folders}",
                QtWidgets.QMessageBox.Warning,
                sent.unconfirmed,
            )
            return
        self._farm.refresh()
        _tell(
            self,
            f"Job {sent.job_id} renders into:\n{folders}\n\nFollow it on the Farm tab.",
            QtWidgets.QMessageBox.Information,
        )

    # ---- talking to Houdini --------------------------------------------

    def _request(
        self,
        command: str,
        args: dict[str, object] | None = None,
        then: Callable[[Reply], None] | None = None,
        failed: Callable[[], None] | None = None,
    ) -> None:
        """Ask Houdini to do `command`, then show what it changed and call `then`,
        or `failed` when it refused."""
        assert self._houdini is not None
        busy = command in _BUSY_WORDS
        if busy:
            self._log_line(f"— {command} {self._shown} —")
            self._set_busy(_BUSY_WORDS[command].format(shot=self._shown))

        def answered(reply: Reply) -> None:
            if reply.failure is None:
                self._show_reply(reply)
            if busy:
                self._set_busy(None)
            if reply.failure is not None:
                _tell(
                    self,
                    reply.failure.message,
                    QtWidgets.QMessageBox.Critical,
                    reply.failure.details,
                )
                self._log.expand()
                if failed is not None:
                    failed()
            elif then is not None:
                then(reply)

        self._houdini.request(command, answered, **(args or {}))

    def _show_reply(self, reply: Reply) -> None:
        if reply.hip is not None:
            self._hip.setText(_hip_text(reply))
        if reply.notes is not None:
            self._notes.setText("\n\n".join(reply.notes))
            self._notes.setVisible(bool(reply.notes))
        if reply.schema is not None:
            self._show_form(reply)
        elif reply.parms is not None and self._form is not None:
            self._form.show_parms(reply.parms)
        if reply.prims is not None:
            self._scene_graph.show_prims(reply.prims)
            if self._form is not None:
                self._form.set_prims(reply.prims)
        if reply.departments is not None:
            self._departments.show_departments(reply.departments)
        if reply.layers is not None:
            self._layers = reply.layers

    def _show_form(self, reply: Reply) -> None:
        assert reply.schema is not None and self._shown is not None
        self._forget_form()
        # The form keeps its open folders in this dict, so a reload reopens them.
        self._form = ParmForm(
            reply.schema,
            reply.parms or {},
            reply.prims or [],
            self._form_states.setdefault(self._shown, {}),
        )
        self._form.edited.connect(self._set_parm)
        self._form.instance_removed.connect(self._remove_instance)
        self._scroll.setWidget(self._form)

    def _opened(self, reply: Reply) -> None:
        self._lighting_target = reply.lighting_target
        self._mark_saved()
        self._enable_publish(self._working)

    def _not_opened(self) -> None:
        self._close_houdini()
        self._show_empty(
            f"Could not open {self._shown}. See the Log Viewer below, then Reload "
            "to try again."
        )

    def _houdini_stopped(self, message: str, details: str) -> None:
        self._close_houdini()
        self._show_empty(f"{self._shown}: Houdini stopped. Reload to open it again.")
        _tell(self, message, QtWidgets.QMessageBox.Critical, details)
        self._log.expand()

    def _cancel(self) -> None:
        if self._working:
            self._close_houdini()
            self._show_empty(f"{self._shown}: {CANCELLED}")

    def _close_houdini(self) -> None:
        if self._houdini is not None:
            self._houdini.stop()
            self._houdini.deleteLater()
            self._houdini = None
        self._working = False

    def _edited(self) -> None:
        self._dirty = True
        self._heading.setText(f"{self._shown}  •  unsaved")

    def _mark_saved(self) -> None:
        self._dirty = False
        self._heading.setText(self._shown or "")
        if self._form is not None:
            self._form.mark_saved()
        self._departments.mark_saved()

    def _forget_form(self) -> None:
        if self._form is not None:
            self._scroll.takeWidget()
            self._form.deleteLater()
            self._form = None

    def _show_empty(self, message: str = "Choose a shot on the left.") -> None:
        self._forget_form()
        self._dirty = False
        self._lighting_target = None
        self._layers = []
        self._heading.setText(message)
        self._hip.setText("")
        self._notes.hide()
        self._scene_graph.show_prims([])
        self._departments.hide()
        self._set_busy(None)

    def _set_busy(self, text: str | None) -> None:
        busy = self._working = text is not None
        self._busy.setVisible(busy)
        self._busy_text.setText(text or "")
        self._reload.setEnabled(not busy and self._shown is not None)
        for button in (self._save, self._send):
            button.setEnabled(not busy and self._form is not None)
        self._enable_publish(busy)
        self._shot_list.setEnabled(not busy)
        if self._form is not None:
            self._form.setEnabled(not busy)
        self._departments.setEnabled(not busy)

    def _log_line(self, line: str) -> None:
        self._log.append(line)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt's name)
        if self._working:
            answer = QtWidgets.QMessageBox.question(
                self, TITLE, "Houdini is still working. Stop it and close?"
            )
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
            self._close_houdini()
            self._forget_form()
        # Saving first finishes later, and closes the window then.
        answered = {"now": True, "close": False}

        def close() -> None:
            if answered["now"]:
                answered["close"] = True
            else:
                self.close()

        self._after_unsaved(close, lambda: None)
        answered["now"] = False
        if answered["close"]:
            self._close_houdini()
            event.accept()
        else:
            event.ignore()


def _hip_text(reply: Reply) -> str:
    if reply.hip_existed:
        return f"Render hip: {reply.hip}"
    seeded = (
        f" Its settings were copied from {reply.seeded_from}."
        if reply.seeded_from
        else ""
    )
    return f"Render hip: {reply.hip} — not saved yet; Save or Send creates it.{seeded}"


def _tell(
    parent: QtWidgets.QWidget,
    message: str,
    icon: QtWidgets.QMessageBox.Icon,
    details: str = "",
) -> None:
    box = QtWidgets.QMessageBox(parent)
    box.setWindowTitle(TITLE)
    box.setIcon(icon)
    box.setText(message)
    if details:
        box.setDetailedText(details)
    box.exec_()
