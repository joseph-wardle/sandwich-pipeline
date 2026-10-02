"""The list of publish versions, where an artist opens one or makes one current."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from Qt import QtCore, QtWidgets

from .dialogs import MessageDialogCustomButtons

if TYPE_CHECKING:
    from collections.abc import Sequence

    # Only for the type checker: that package needs USD, and Substance Painter,
    # which imports this one, has none.
    from pipe.core.publish import Entry

TITLE = "Version History"

_COLUMNS = ("Version", "", "Note", "By", "Date")
_NOTE_COLUMN = 2
_CURRENT = "current"
_DATE_FORMAT = "%b %d %H:%M"
_NO_SCENE = "No scene was kept with this version."
_SIZE = (720, 400)


class HistoryAction(Enum):
    OPEN = "open"
    MAKE_CURRENT = "make current"


def prompt_history(
    parent: QtWidgets.QWidget | None, entries: Sequence[Entry], scene_name: str
) -> tuple[HistoryAction, Entry] | None:
    """Open the version history. None means the artist chose nothing.

    `scene_name` is the working file that Open replaces.
    """
    dialog = _HistoryDialog(parent, entries, scene_name)
    if not dialog.exec_():
        return None
    return dialog.choice


class _HistoryDialog(QtWidgets.QDialog):
    choice: tuple[HistoryAction, Entry] | None

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        entries: Sequence[Entry],
        scene_name: str,
    ) -> None:
        super().__init__(parent)
        self.choice = None
        self._entries = list(entries)
        self._scene_name = scene_name
        self.setWindowTitle(TITLE)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(*_SIZE)

        self._table = QtWidgets.QTableWidget(len(self._entries), len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_NOTE_COLUMN, QtWidgets.QHeaderView.Stretch)
        for row, entry in enumerate(self._entries):
            info = entry.info
            cells = (
                entry.label,
                _CURRENT if entry.current else "",
                info.note,
                info.author,
                info.date.strftime(_DATE_FORMAT),
            )
            for column, text in enumerate(cells):
                self._table.setItem(row, column, QtWidgets.QTableWidgetItem(text))

        self._open = QtWidgets.QPushButton("Open")
        self._open.clicked.connect(self._choose_open)
        self._make_current = QtWidgets.QPushButton("Make Current")
        self._make_current.clicked.connect(self._choose_make_current)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self._open)
        buttons.addWidget(self._make_current)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._table, 1)
        layout.addLayout(buttons)

        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.selectRow(0)

    def _selected(self) -> Entry:
        return self._entries[self._table.currentRow()]

    def _on_selection_changed(self) -> None:
        entry = self._selected()
        has_scene = entry.info.source is not None
        self._open.setEnabled(has_scene)
        self._open.setToolTip("" if has_scene else _NO_SCENE)
        self._make_current.setEnabled(not entry.current)

    def _choose_open(self) -> None:
        entry = self._selected()
        self._choose(
            HistoryAction.OPEN,
            f"Replace {self._scene_name} with the one that made {entry.label}?",
            "Replace",
        )

    def _choose_make_current(self) -> None:
        entry = self._selected()
        self._choose(
            HistoryAction.MAKE_CURRENT,
            f"Make {entry.label} current for everyone?",
            "Make Current",
        )

    def _choose(self, action: HistoryAction, question: str, ok_name: str) -> None:
        confirm = MessageDialogCustomButtons(
            self,
            question,
            TITLE,
            has_cancel_button=True,
            ok_name=ok_name,
            cancel_name="Cancel",
        )
        if not confirm.exec_():
            return
        self.choice = (action, self._selected())
        self.accept()


__all__ = ["HistoryAction", "prompt_history"]
