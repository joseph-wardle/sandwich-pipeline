"""The Render Network window: choose a shot, edit its render layers, send them.

Every action runs the headless Houdini worker, which opens the shot's render
hip. Nothing in the window changes the hip until Save or Send.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.network.client import WorkerRun
from pipe.core.render.network.departments import DepartmentPanel
from pipe.core.render.network.farm_tab import FarmTab
from pipe.core.render.network.overview import OverviewPage
from pipe.core.render.network.form import ParmForm
from pipe.core.render.network.protocol import (
    DESCRIBE,
    LIGHTING_HIP,
    PUBLISH_LIGHTING,
    SAVE,
    SEND,
    LightingChoice,
    Result,
    Edits,
    shot_folder,
)
from pipe.core.render.network.scene_graph import SceneGraphPanel
from pipe.core.render.network import theme
from pipe.core.render.network.shots import ShotList
from pipe.core.render.network.viewer import FrameViewer
from pipe.core.render.lighters import LIGHTING_STEP
from pipe.core.publish import Target
from pipe.core.shotgrid import Shot
from pipe.core.ui import prompt_publish

TITLE = "Render Network"
# How far the shot's title and render hip sit in from the edge, in pixels.
_TITLE_INSET = 12
_LAYER, _ON, _NEXT, _LAST = range(4)
_BUSY_WORDS = {
    DESCRIBE: "Opening {shot} in Houdini…",
    SAVE: "Saving {shot}'s render hip…",
    SEND: "Saving {shot} and sending it to Tractor…",
    PUBLISH_LIGHTING: "Publishing {shot}'s lighting hip…",
}
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
    _layers: QtWidgets.QTreeWidget
    _scroll: QtWidgets.QScrollArea
    _scene_graph: SceneGraphPanel
    _departments: DepartmentPanel
    _tabs: QtWidgets.QTabWidget
    _farm: FarmTab
    _overview: OverviewPage
    _pages: QtWidgets.QTabWidget
    _viewer: FrameViewer
    _busy: QtWidgets.QWidget
    _busy_text: QtWidgets.QLabel
    _log: QtWidgets.QPlainTextEdit
    _reload: QtWidgets.QPushButton
    _save: QtWidgets.QPushButton
    _send: QtWidgets.QPushButton

    _form: ParmForm | None
    _result: Result | None
    _run: WorkerRun | None
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
    ) -> None:
        """`lit_shots` are the codes the artist is assigned to light, which they
        may publish lighting for; None when ShotGrid couldn't say. An `admin`
        may publish any shot's lighting."""
        super().__init__()
        self._admin = admin
        self._shots_by_code = {shot.code or "": shot for shot in shots}
        self._lit_shots = lit_shots
        self.setWindowTitle(TITLE)
        self.resize(1280, 900)
        self._form, self._result, self._run = None, None, None
        self._dirty = False
        self._form_states = {}
        self._shown = None

        self._shot_list = ShotList(shots, lit_shots or set())
        self._shot_list.chosen.connect(self._choose_shot)

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
        self._overview = OverviewPage(
            [shot.code or "" for shot in shots], getpass.getuser()
        )
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

        self._layers = QtWidgets.QTreeWidget()
        self._layers.setHeaderLabels(["Layer", "On", "Next render", "Last send"])
        self._layers.setRootIsDecorated(False)
        self._layers.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setObjectName(theme.PARMS)
        self._scroll.setWidgetResizable(True)
        self._scene_graph = SceneGraphPanel()
        self._departments = DepartmentPanel()
        self._departments.hide()
        self._departments.changed.connect(self._edited)
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

        self._busy_text = QtWidgets.QLabel()
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 0)
        bar.setMaximumWidth(160)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self._cancel)
        self._busy = QtWidgets.QWidget()
        busy_layout = QtWidgets.QHBoxLayout(self._busy)
        busy_layout.setContentsMargins(0, 0, 0, 0)
        busy_layout.addWidget(bar)
        busy_layout.addWidget(self._busy_text, 1)
        busy_layout.addWidget(cancel)

        self._publish = QtWidgets.QPushButton("Publish Lighting")
        self._publish.clicked.connect(self._on_publish)
        self._reload = QtWidgets.QPushButton("Reload")
        self._reload.setToolTip("Read the render hip again, dropping unsaved edits")
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
        buttons.addWidget(self._reload)
        buttons.addWidget(self._save)
        buttons.addWidget(self._publish)
        buttons.addStretch(1)
        buttons.addWidget(self._send)

        self._log = QtWidgets.QPlainTextEdit()
        self._log.setObjectName(theme.LOG)
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(5000)
        self._log.setPlaceholderText("What Houdini says while it works shows here.")
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self._log.setFont(font)

        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(self._heading)
        top_layout.addWidget(self._hip)
        top_layout.addWidget(self._notes)
        top_layout.addWidget(self._layers)
        # The farm and the frames need no Houdini, so they show while it opens.
        self._farm = FarmTab()
        self._viewer = FrameViewer()
        self._farm.view_frames.connect(self._view_frames)
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.addTab(parms_and_stage, "Parameters")
        self._tabs.addTab(self._farm, "Farm")
        self._tabs.addTab(self._viewer, "Images")
        top_layout.addWidget(self._tabs, 1)
        top_layout.addWidget(self._busy)
        top_layout.addLayout(buttons)

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self._log)
        split.setStretchFactor(0, 1)
        split.setSizes([760, 140])
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
        self._forget_form()
        self._heading.setText(code)
        self._hip.setText("")
        self._notes.hide()
        self._layers.hide()
        self._scene_graph.show_prims([])
        self._farm.set_shot(code)
        self._viewer.set_shot(code)
        self._start(DESCRIBE, code, None, self._show)

    def _after_unsaved(
        self, proceed: Callable[[], None], stay: Callable[[], None]
    ) -> None:
        """Run `proceed` once unsaved edits are saved or dropped, else `stay`."""
        if not self._dirty or self._result is None:
            proceed()
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(TITLE)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(f"Save your changes to {self._result.shot} first?")
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
            shot = self._result.shot

            def saved(result: Result) -> None:
                self._show(result)
                if result.failure is None:
                    proceed()
                else:
                    stay()

            self._start(SAVE, shot, self._values(), saved)

    # ---- actions -------------------------------------------------------

    def _on_reload(self) -> None:
        if self._result is not None:
            shot = self._result.shot
            self._after_unsaved(
                lambda: self._start(DESCRIBE, shot, None, self._show), lambda: None
            )

    def _on_save(self) -> None:
        if self._result is not None and self._run is None:
            self._start(SAVE, self._result.shot, self._values(), self._saved)

    def _on_send(self) -> None:
        if self._result is None or self._form is None or self._run is not None:
            return
        if self._confirm_send():
            self._start(SEND, self._result.shot, self._values(), self._sent)

    def _confirm_send(self) -> bool:
        assert self._result is not None and self._form is not None
        form = self._form
        chosen = [
            layer.name for layer in self._result.layers if form.value(layer.toggle)
        ]
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
            f"Priority: {form.value('priority')}",
        ]
        answer = QtWidgets.QMessageBox.question(
            self,
            TITLE,
            f"Send {self._result.shot} to Tractor?\n\n"
            + "\n".join(lines)
            + "\n\nThe render hip is saved with these settings first.",
        )
        return answer == QtWidgets.QMessageBox.Yes

    def _describe_frames(self) -> str:
        assert self._form is not None and self._result is not None
        mode = self._form.value("framerange")
        if mode in _FRAME_RANGES:
            return _FRAME_RANGES[mode]
        frames = self._form.values().get("f", [])
        evaluated = self._result.evaluated.get("f", [])
        shown = [
            evaluated[i] if isinstance(v, dict) and i < len(evaluated) else v
            for i, v in enumerate(frames)
        ]
        if len(shown) < 3 or any(isinstance(v, dict) for v in shown):
            return "as set on the hip"
        start, end, step = (int(v) for v in shown)
        by = f" every {step}" if step != 1 else ""
        return f"{start}–{end}{by}"

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
        if self._result is None or self._result.lighting_target is None:
            return "Waiting for Houdini to read the shot's lighting publishes."
        return ""

    def _on_publish(self) -> None:
        code = self._shown
        if code is None or self._run is not None or code not in self._shots_by_code:
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
        known = self._result.lighting_target if self._result else None
        if known is None:
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
        self._start(
            PUBLISH_LIGHTING,
            code,
            None,
            self._published_lighting,
            LightingChoice(
                note=choice.note, announce=choice.announce, final=choice.final
            ),
        )

    def _published_lighting(self, result: Result) -> None:
        if result.failure is not None:
            _tell(
                self,
                result.failure.message,
                QtWidgets.QMessageBox.Critical,
                result.failure.details,
            )
            return
        _tell(
            self,
            "\n".join(result.published) or "Published.",
            QtWidgets.QMessageBox.Information,
        )
        # The render hip reads lighting's publish, so read it again.
        if self._result is not None and self._result.shot == result.shot:
            self._on_reload()
        elif self._shown == result.shot:
            self._open(result.shot)

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

    def _saved(self, result: Result) -> None:
        self._show(result)
        if result.failure is None:
            self._log_line(f"Saved {result.hip}")

    def _sent(self, result: Result) -> None:
        self._show(result)
        sent = result.sent
        if result.failure is not None or sent is None:
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

    # ---- running the worker --------------------------------------------

    def _start(
        self,
        command: str,
        shot: str,
        edits: Edits | None,
        then: Callable[[Result], None],
        choice: LightingChoice | None = None,
    ) -> None:
        if self._run is not None:
            return
        self._log_line(f"— {command} {shot} —")
        run = WorkerRun(command, shot, edits, self, choice)
        run.output.connect(self._log_line)
        run.finished.connect(lambda result: self._finished(result, then))
        self._run = run
        self._set_busy(_BUSY_WORDS[command].format(shot=shot))
        run.start()

    def _finished(self, result: Result, then: Callable[[Result], None]) -> None:
        self._run = None
        self._set_busy(None)
        then(result)

    def _cancel(self) -> None:
        if self._run is not None:
            self._run.cancel()

    def _show(self, result: Result) -> None:
        if result.failure is not None:
            _tell(
                self,
                result.failure.message,
                QtWidgets.QMessageBox.Critical,
                result.failure.details,
            )
            if self._result is None or self._result.shot != result.shot:
                self._show_empty(f"Could not open {result.shot}. See the log below.")
            return

        self._forget_form()
        self._result = result
        self._dirty = False
        self._heading.setText(result.shot)
        self._hip.setText(_hip_text(result))
        self._notes.setText("\n\n".join(result.notes))
        self._notes.setVisible(bool(result.notes))

        # The form keeps its open folders in this dict, so a reload reopens them.
        self._form = ParmForm(
            result.schema,
            result.values,
            result.prims,
            result.evaluated,
            self._form_states.setdefault(result.shot, {}),
        )
        self._form.changed.connect(self._edited)
        self._scroll.setWidget(self._form)
        self._show_layers()
        self._scene_graph.show_prims(result.prims)
        self._departments.show_departments(result.departments)
        self._set_busy(None)

    def _show_layers(self) -> None:
        self._layers.clear()
        self._layers.setVisible(self._result is not None and self._form is not None)
        if self._result is None or self._form is None:
            return
        for layer in self._result.layers:
            on = bool(self._form.value(layer.toggle))
            item = QtWidgets.QTreeWidgetItem(
                [
                    layer.name,
                    "●" if on else "",
                    layer.next_folder,
                    layer.last_job or "—",
                ]
            )
            item.setToolTip(_NEXT, layer.next_folder)
            item.setToolTip(_LAST, layer.last_job)
            if not on:
                for column in range(4):
                    item.setForeground(
                        column,
                        self.palette().brush(
                            QtGui.QPalette.Disabled, QtGui.QPalette.Text
                        ),
                    )
            self._layers.addTopLevelItem(item)
        for column in (_LAYER, _ON):
            self._layers.resizeColumnToContents(column)
        self._layers.setColumnWidth(_NEXT, 420)
        # As tall as its rows, so the parms get the rest.
        rows = self._layers.topLevelItemCount()
        row_height = self._layers.sizeHintForRow(0) if rows else 0
        self._layers.setFixedHeight(
            self._layers.header().sizeHint().height()
            + rows * row_height
            + 2 * self._layers.frameWidth()
        )

    def _edited(self) -> None:
        self._dirty = True
        self._show_layers()
        if self._result is not None:
            self._heading.setText(f"{self._result.shot}  •  unsaved")

    def _values(self) -> Edits:
        assert self._form is not None
        return Edits(self._form.values(), self._departments.choices())

    def _forget_form(self) -> None:
        if self._form is not None:
            self._scroll.takeWidget()
            self._form.deleteLater()
            self._form = None

    def _show_empty(self, message: str = "Choose a shot on the left.") -> None:
        self._result = None
        self._forget_form()
        self._heading.setText(message)
        self._hip.setText("")
        self._notes.hide()
        self._layers.hide()
        self._scene_graph.show_prims([])
        self._departments.hide()
        self._set_busy(None)

    def _set_busy(self, text: str | None) -> None:
        busy = text is not None
        self._busy.setVisible(busy)
        self._busy_text.setText(text or "")
        ready = not busy and self._result is not None
        for button in (self._reload, self._save, self._send):
            button.setEnabled(ready)
        self._enable_publish(busy)
        self._shot_list.setEnabled(not busy)
        if self._form is not None:
            self._form.setEnabled(not busy)
        self._departments.setEnabled(not busy)

    def _log_line(self, line: str) -> None:
        self._log.appendPlainText(line)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 (Qt's name)
        if self._run is not None:
            answer = QtWidgets.QMessageBox.question(
                self, TITLE, "Houdini is still working. Stop it and close?"
            )
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
            self._run.cancel()
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
            event.accept()
        else:
            event.ignore()


def _hip_text(result: Result) -> str:
    if result.hip_existed:
        return f"Render hip: {result.hip}"
    seeded = (
        f" Its settings were copied from {result.seeded_from}."
        if result.seeded_from
        else ""
    )
    return f"Render hip: {result.hip} — not saved yet; Save or Send creates it.{seeded}"


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
