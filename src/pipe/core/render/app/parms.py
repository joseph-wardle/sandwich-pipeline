"""The Parameters tab: the shot's render hip, open in Houdini, to edit, save and send."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from Qt import QtCore, QtWidgets

from pipe.core.render.app import theme, widgets
from pipe.core.render.app.departments import DepartmentPanel
from pipe.core.render.app.form import ParmForm
from pipe.core.render.app.scene_graph import SceneGraphPanel
from pipe.core.render.app.session import HoudiniSession
from pipe.core.render.app.widgets import TITLE, LogViewer, tell
from pipe.core.render.protocol import (
    OPEN,
    PUBLISH_LIGHTING,
    REMOVE_INSTANCE,
    REVERT,
    SAVE,
    SEND,
    SET_DEPARTMENT,
    SET_PARM,
    Layer,
    LightingTarget,
    Reply,
)
from pipe.core.ui.publish_dialog import PublishChoice

_TITLE_INSET = 16
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
# Notes that start with these words say what the window did for the artist,
# rather than warn them.
_INFO_NOTES = ("Added ",)


class ParmsTab(QtWidgets.QWidget):
    """The window places `header` above its tabs and `busy_bar` below them, and
    gives `status_pill` to sit beside the shot's name.

    `changed` fires when busy, the shown shot or its lighting target change;
    `sent` once Tractor has a job.
    """

    changed = QtCore.Signal()
    sent = QtCore.Signal()

    header: QtWidgets.QWidget
    busy_bar: QtWidgets.QWidget
    _heading: QtWidgets.QLabel
    _status_pill: QtWidgets.QWidget
    _unsaved: widgets.Chip
    _hip_icon: QtWidgets.QLabel
    _hip: QtWidgets.QLabel
    _notes: QtWidgets.QWidget
    _notes_layout: QtWidgets.QVBoxLayout
    _banners: list[widgets.Banner]
    _busy_text: QtWidgets.QLabel
    _scroll: QtWidgets.QScrollArea
    _scene_graph: SceneGraphPanel
    _departments: DepartmentPanel
    _reload: QtWidgets.QPushButton
    _save: QtWidgets.QPushButton
    _send: QtWidgets.QPushButton
    _log: LogViewer

    _houdini: HoudiniSession | None
    _form: ParmForm | None
    _layers: list[Layer]
    lighting_target: LightingTarget | None
    working: bool
    _dirty: bool
    _form_states: dict[str, dict[str, Any]]
    shown: str | None

    def __init__(
        self,
        log: LogViewer,
        lighting_tools: QtWidgets.QWidget,
        status_pill: QtWidgets.QWidget,
    ) -> None:
        super().__init__()
        self._log = log
        self._status_pill = status_pill
        self._banners = []
        self._houdini, self._form, self.lighting_target = None, None, None
        self._layers, self.working, self._dirty = [], False, False
        self._form_states, self.shown = {}, None

        self._heading = QtWidgets.QLabel()
        self._heading.setStyleSheet("QLabel { font-size: 20pt; font-weight: 700; }")
        self._unsaved = widgets.Chip("Unsaved changes", theme.WARNING)
        self._unsaved.setToolTip("Save (Ctrl+S) or Send keeps them in the render hip")
        self._unsaved.hide()
        self._hip_icon = widgets.icon_label("file", theme.DIM_TEXT, 14)
        self._hip = QtWidgets.QLabel()
        self._hip.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._hip.setWordWrap(True)
        self._hip.setStyleSheet(f"QLabel {{ color: {theme.DIM_TEXT}; }}")
        self._notes = QtWidgets.QWidget()
        self._notes_layout = QtWidgets.QVBoxLayout(self._notes)
        self._notes_layout.setContentsMargins(_TITLE_INSET, 2, _TITLE_INSET, 2)
        self._notes_layout.setSpacing(6)
        # Off the window's edge, so the shot reads as a title over the panels.
        title_row = QtWidgets.QHBoxLayout()
        title_row.setContentsMargins(_TITLE_INSET, 10, _TITLE_INSET, 0)
        title_row.setSpacing(12)
        title_row.addWidget(self._heading)
        title_row.addWidget(status_pill, 0, QtCore.Qt.AlignVCenter)
        title_row.addWidget(self._unsaved, 0, QtCore.Qt.AlignVCenter)
        title_row.addStretch(1)
        hip_row = QtWidgets.QHBoxLayout()
        hip_row.setContentsMargins(_TITLE_INSET, 0, _TITLE_INSET, 6)
        hip_row.setSpacing(6)
        hip_row.addWidget(self._hip_icon, 0, QtCore.Qt.AlignTop)
        hip_row.addWidget(self._hip, 1)
        self.header = QtWidgets.QWidget()
        header_layout = QtWidgets.QVBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.addLayout(title_row)
        header_layout.addLayout(hip_row)
        header_layout.addWidget(self._notes)

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setObjectName(theme.PARMS)
        self._scroll.setWidgetResizable(True)
        self._scene_graph = SceneGraphPanel()
        self._departments = DepartmentPanel(lighting_tools)
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

        self._reload = widgets.button(
            "Reload",
            "refresh",
            "Open the render hip from disk again, dropping unsaved edits",
            theme.QUIET,
            self.reload,
        )
        self._save = widgets.button(
            "Save",
            "save",
            "Save these settings into the shot's render hip (Ctrl+S)",
            clicked=self.save,
        )
        self._send = widgets.button(
            "Send to Tractor",
            "send",
            "Save, then render every layer that is on",
            theme.PRIMARY,
            self._on_send,
        )
        actions = QtWidgets.QWidget()
        theme.shade(actions, theme.HEADER)
        buttons = QtWidgets.QHBoxLayout(actions)
        buttons.setContentsMargins(10, 8, 10, 8)
        buttons.addWidget(self._reload)
        buttons.addWidget(self._save)
        buttons.addStretch(1)
        buttons.addWidget(self._send)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(parms_and_stage, 1)
        layout.addWidget(actions)

        self._busy_text = QtWidgets.QLabel()
        self._busy_text.setStyleSheet("QLabel { font-weight: 600; }")
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedWidth(140)
        cancel = widgets.button("Cancel", "x", "Stop Houdini", clicked=self._cancel)
        card = QtWidgets.QFrame()
        card.setObjectName(theme.CARD)
        busy_layout = QtWidgets.QHBoxLayout(card)
        busy_layout.setContentsMargins(12, 6, 8, 6)
        busy_layout.setSpacing(12)
        busy_layout.addWidget(bar)
        busy_layout.addWidget(self._busy_text, 1)
        busy_layout.addWidget(cancel)
        self.busy_bar = QtWidgets.QWidget()
        busy_row = QtWidgets.QHBoxLayout(self.busy_bar)
        busy_row.setContentsMargins(8, 0, 8, 6)
        busy_row.addWidget(card)

        self._show_empty()

    def open(self, code: str) -> None:
        """Open `code`'s render hip in a new Houdini."""
        self.shown = code
        self._heading.setText(code)
        self._close_houdini()
        self._forget_form()
        self._departments.hide()
        self._unsaved.hide()
        self._set_hip("")
        self._set_notes([])
        self._scene_graph.show_prims([])
        self._houdini = HoudiniSession(code, self)
        self._houdini.output.connect(self._log.append)
        self._houdini.stopped.connect(self._houdini_stopped)
        self._request(OPEN, then=self._opened, failed=self._not_opened)

    def after_unsaved(
        self, proceed: Callable[[], None], stay: Callable[[], None]
    ) -> None:
        """Run `proceed` once unsaved edits are saved or dropped, else `stay`."""
        if not self._dirty or self._form is None:
            proceed()
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(TITLE)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setText(f"Save your changes to {self.shown} first?")
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

    def reload(self) -> None:
        if (code := self.shown) is not None and not self.working:
            self.after_unsaved(lambda: self._reopen(code), lambda: None)

    def save(self) -> None:
        if self._form is not None and not self.working:
            self._request(SAVE, then=self._saved)

    def publish_lighting(self, choice: PublishChoice) -> None:
        # Houdini opens the render hip again after, which drops unsaved edits.
        self.after_unsaved(
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

    def stop(self) -> None:
        """Stop Houdini at once, dropping unsaved edits."""
        self._close_houdini()
        self._forget_form()

    def _reopen(self, code: str) -> None:
        if self._houdini is None:
            self.open(code)
        else:
            self._request(OPEN, then=self._opened, failed=self._not_opened)

    def _on_send(self) -> None:
        if self._form is not None and not self.working and self._confirm_send():
            self._request(SEND, then=self._sent)

    def _set_parm(self, name: str, value: object) -> None:
        self._edited()
        self._request(SET_PARM, {"name": name, "value": value})

    def _remove_instance(self, name: str, position: int) -> None:
        self._edited()
        self._request(REMOVE_INSTANCE, {"name": name, "position": position})

    def _revert(self, names: list[str]) -> None:
        self._edited()
        self._request(REVERT, {"names": names})

    def _set_department(self, row: str, enabled: bool, version: str) -> None:
        self._edited()
        self._request(
            SET_DEPARTMENT, {"row": row, "enabled": enabled, "version": version}
        )

    def _confirm_send(self) -> bool:
        chosen = [layer.name for layer in self._layers if self._value(layer.toggle)]
        if not chosen:
            tell(
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
            f"Send {self.shown} to Tractor?\n\n"
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

    def _saved(self, reply: Reply) -> None:
        self._mark_saved()
        self._log.append(f"Saved {reply.hip}")

    def _sent(self, reply: Reply) -> None:
        self._mark_saved()
        sent = reply.sent
        if sent is None:
            return
        folders = "\n".join(sent.folders)
        if sent.job_id is None:
            tell(
                self,
                "Tractor did not confirm the job, but it may still have been queued. "
                f'Look for "{sent.title}" in Tractor before sending again.\n\n'
                f"Its folders were kept:\n{folders}",
                QtWidgets.QMessageBox.Warning,
                sent.unconfirmed,
            )
            return
        self.sent.emit()
        tell(
            self,
            f"Job {sent.job_id} renders into:\n{folders}\n\nFollow it on the Farm tab.",
            QtWidgets.QMessageBox.Information,
        )

    def _published_lighting(self, reply: Reply) -> None:
        self._opened(reply)
        tell(
            self,
            "\n".join(reply.published or []) or "Published.",
            QtWidgets.QMessageBox.Information,
        )

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
            self._log.append(f"— {command} {self.shown} —")
            self._set_busy(_BUSY_WORDS[command].format(shot=self.shown))

        def answered(reply: Reply) -> None:
            if reply.failure is None:
                self._show_reply(reply)
            if busy:
                self._set_busy(None)
            if reply.failure is not None:
                tell(
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
            self._set_hip(_hip_text(reply))
        if reply.notes is not None:
            self._set_notes(reply.notes)
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
        assert reply.schema is not None and self.shown is not None
        self._forget_form()
        # The form keeps its open folders in this dict, so a reload reopens them.
        self._form = ParmForm(
            reply.schema,
            reply.parms or {},
            reply.prims or [],
            self._form_states.setdefault(self.shown, {}),
        )
        self._form.edited.connect(self._set_parm)
        self._form.instance_removed.connect(self._remove_instance)
        self._form.reverted.connect(self._revert)
        self._scroll.setWidget(self._form)

    def _opened(self, reply: Reply) -> None:
        self.lighting_target = reply.lighting_target
        self._mark_saved()
        self.changed.emit()

    def _not_opened(self) -> None:
        self._close_houdini()
        self._show_empty(
            f"Could not open {self.shown}. See the Log Viewer below, then Reload "
            "to try again."
        )

    def _houdini_stopped(self, message: str, details: str) -> None:
        self._close_houdini()
        self._show_empty(f"{self.shown}: Houdini stopped. Reload to open it again.")
        tell(self, message, QtWidgets.QMessageBox.Critical, details)
        self._log.expand()

    def _cancel(self) -> None:
        if self.working:
            self._close_houdini()
            self._show_empty(f"{self.shown}: {CANCELLED}")

    def _close_houdini(self) -> None:
        if self._houdini is not None:
            self._houdini.stop()
            self._houdini.deleteLater()
            self._houdini = None
        self.working = False

    def _edited(self) -> None:
        self._dirty = True
        self._unsaved.show()

    def _mark_saved(self) -> None:
        self._dirty = False
        self._unsaved.hide()
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
        self.lighting_target = None
        self._layers = []
        self._heading.setText(message)
        self._status_pill.hide()
        self._unsaved.hide()
        self._set_hip("")
        self._set_notes([])
        self._scene_graph.show_prims([])
        self._departments.hide()
        self._set_busy(None)

    def _set_hip(self, text: str) -> None:
        self._hip.setText(text)
        self._hip.setVisible(bool(text))
        self._hip_icon.setVisible(bool(text))

    def _set_notes(self, notes: Sequence[str]) -> None:
        """Show each of Houdini's notes as its own banner."""
        for banner in self._banners:
            # Out of the layout now; deleted once the event loop is free.
            banner.hide()
            banner.deleteLater()
        self._banners = []
        for note in notes:
            kind = widgets.INFO if note.startswith(_INFO_NOTES) else widgets.WARNING
            self._banners.append(widgets.Banner(note, kind))
            self._notes_layout.addWidget(self._banners[-1])
        self._notes.setVisible(bool(notes))

    def _set_busy(self, text: str | None) -> None:
        busy = self.working = text is not None
        self.busy_bar.setVisible(busy)
        self._busy_text.setText(text or "")
        self._reload.setEnabled(not busy and self.shown is not None)
        for button in (self._save, self._send):
            button.setEnabled(not busy and self._form is not None)
        if self._form is not None:
            self._form.setEnabled(not busy)
        self._departments.setEnabled(not busy)
        self.changed.emit()


def _hip_text(reply: Reply) -> str:
    if reply.hip_existed:
        return f"Render hip: {reply.hip}"
    seeded = (
        f" Its settings were copied from {reply.seeded_from}."
        if reply.seeded_from
        else ""
    )
    return f"Render hip: {reply.hip} — not saved yet; Save or Send creates it.{seeded}"
