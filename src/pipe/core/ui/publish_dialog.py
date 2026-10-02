"""The rows every publish dialog ends with, and the dialog that is only those rows."""

from __future__ import annotations

from dataclasses import dataclass

from Qt import QtCore, QtWidgets

from .dialogs import DialogButtons

_SETTINGS_ORG = "sandwich-pipeline"
_SETTINGS_APP = "publish"
_PLAYBLAST_KEY = "playblast_after_publishing"

_NOTE_LABEL = "What changed?"
_NOTE_PLACEHOLDER = "Shown in the version history."
_NOTE_LINES = 3

_WIDTH = 380


@dataclass(frozen=True)
class PublishChoice:
    note: str
    playblast: bool


class PublishRows(QtWidgets.QWidget):
    """The version a publish becomes, its note, and whether to playblast after."""

    def __init__(
        self, version_label: str, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)

        self._version = QtWidgets.QLabel(version_label)
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
        layout.addWidget(self._playblast)
        # A dialog taller than it needs gives the room to what is above the rows.
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    def set_version_label(self, version_label: str) -> None:
        """For a dialog where the artist also picks what is published."""
        self._version.setText(version_label)

    def choice(self) -> PublishChoice:
        return PublishChoice(
            # One line, as the version history and the announcement show it.
            note=" ".join(self._note.toPlainText().split()),
            playblast=self._playblast.isChecked(),
        )


def prompt_publish(
    parent: QtWidgets.QWidget | None, version_label: str
) -> PublishChoice | None:
    """Open the publish dialog. None means do not publish."""
    dialog = _PublishDialog(parent, version_label)
    if not dialog.exec_():
        return None
    return dialog.rows.choice()


class _PublishDialog(QtWidgets.QDialog, DialogButtons):
    def __init__(self, parent: QtWidgets.QWidget | None, version_label: str) -> None:
        super().__init__(parent)
        self._init_buttons(True, "Publish", "Cancel")
        self.setWindowTitle("Publish")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(_WIDTH)

        self.rows = PublishRows(version_label)

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
