"""The rows every publish dialog ends with, and the dialog that is only those rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from Qt import QtCore, QtWidgets

from pipe.core.util.text import and_list

from .dialogs import DialogButtons

if TYPE_CHECKING:
    from pipe.core.publish import Target

_SETTINGS_ORG = "sandwich-pipeline"
_SETTINGS_APP = "publish"
_PLAYBLAST_KEY = "playblast_after_publishing"

_NOTE_LABEL = "What changed?"
_NOTE_PLACEHOLDER = "Shown in the version history."
_NOTE_LINES = 3

_TELL = "Tell {}"
_TELLS = "Tells {}"
_TELLS_SINCE = "Tells {} (v{:03d} was final)"
_FINAL = "Final"

_WIDTH = 380


@dataclass(frozen=True)
class PublishChoice:
    note: str
    # Tell downstream. Core also tells them for a FINAL, and after one.
    announce: bool
    final: bool
    playblast: bool


class PublishRows(QtWidgets.QWidget):
    """The version a publish becomes, its note, who is told, and whether to playblast."""

    def __init__(
        self, target: Target | None, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._target = target

        self._version = QtWidgets.QLabel()
        heading = self._version.font()
        heading.setBold(True)
        self._version.setFont(heading)

        # A box with a question over it and the cursor in it reads as something
        # to fill in. A publish with no note still goes through.
        self._note = QtWidgets.QPlainTextEdit()
        self._note.setPlaceholderText(_NOTE_PLACEHOLDER)
        self._note.setTabChangesFocus(True)
        self._note.setFixedHeight(_height_of_lines(self._note, _NOTE_LINES))
        note_label = QtWidgets.QLabel(_NOTE_LABEL)
        note_label.setBuddy(self._note)
        # The dialog that holds these rows opens with the cursor in the note.
        self.setFocusProxy(self._note)

        self._tell = QtWidgets.QCheckBox()
        self._final = QtWidgets.QCheckBox(_FINAL)
        self._final.toggled.connect(self._sync_tell)

        self._playblast = QtWidgets.QCheckBox("Playblast after publishing")
        self._playblast.setChecked(_load_playblast())
        # Remembered as it is toggled, so a dialog holding these rows has nothing
        # to call when it closes.
        self._playblast.toggled.connect(_save_playblast)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._version)
        layout.addWidget(note_label)
        layout.addWidget(self._note)
        layout.addWidget(self._tell)
        layout.addWidget(self._final)
        layout.addWidget(self._playblast)
        # A dialog taller than it needs gives the room to what is above the rows.
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self.set_target(target)

    def set_target(self, target: Target | None) -> None:
        """For a dialog where the artist also picks what is published."""
        self._target = target
        self._version.setText(target.label if target else "")
        # Nothing to tell for a set or a department with no downstream.
        self._tell.setVisible(bool(target and target.downstream))
        self._sync_tell()

    def choice(self) -> PublishChoice:
        return PublishChoice(
            # One line, as the version history and the announcement show it.
            note=" ".join(self._note.toPlainText().split()),
            announce=self._tell.isChecked(),
            final=self._final.isChecked(),
            playblast=self._playblast.isChecked(),
        )

    def _sync_tell(self) -> None:
        """Lock the announce row when core will announce whatever it says."""
        if self._target is None:
            return
        steps = and_list(self._target.downstream)
        since = self._target.final
        if since is not None:
            text = _TELLS_SINCE.format(steps, since)
        elif self._final.isChecked():
            text = _TELLS.format(steps)
        else:
            text = _TELL.format(steps)
        self._tell.setText(text)
        forced = since is not None or self._final.isChecked()
        if forced:
            self._tell.setChecked(True)
        self._tell.setEnabled(not forced)


def prompt_publish(
    parent: QtWidgets.QWidget | None, target: Target
) -> PublishChoice | None:
    """Open the publish dialog. None means do not publish."""
    dialog = _PublishDialog(parent, target)
    if not dialog.exec_():
        return None
    return dialog.rows.choice()


class _PublishDialog(QtWidgets.QDialog, DialogButtons):
    def __init__(self, parent: QtWidgets.QWidget | None, target: Target) -> None:
        super().__init__(parent)
        self._init_buttons(True, "Publish", "Cancel")
        self.setWindowTitle("Publish")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(_WIDTH)

        self.rows = PublishRows(target)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.rows)
        layout.addWidget(self.buttons)

        self.rows.setFocus()


def _height_of_lines(box: QtWidgets.QPlainTextEdit, lines: int) -> int:
    margins = 2 * (box.frameWidth() + int(box.document().documentMargin()))
    return box.fontMetrics().lineSpacing() * lines + margins


def _load_playblast() -> bool:
    settings = QtCore.QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    return bool(settings.value(_PLAYBLAST_KEY, False, type=bool))


def _save_playblast(checked: bool) -> None:
    settings = QtCore.QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    settings.setValue(_PLAYBLAST_KEY, checked)
    settings.sync()


__all__ = ["PublishChoice", "PublishRows", "prompt_publish"]
