"""Failed tries as rows, and the Tractor log of the one chosen, for the Farm
Doctor's side panes.

Choosing a row reads its task's whole log, which holds every try, and scrolls
to that try, shaded, with its error lines in red.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from Qt import QtCore, QtGui, QtWidgets

from pipe.core import tractor
from pipe.core.render.app import background, theme, widgets

_DATA_ROLE = QtCore.Qt.UserRole
# Logs read in the last minute are shown again without asking Tractor.
_CACHE_SECONDS = 60.0
_TRY_SHADE = QtGui.QColor(61, 121, 214, 45)
_ERROR_SHADE = QtGui.QColor(110, 40, 40)
_ERROR_LINE = re.compile(r"error|fatal|segmentation|killed|traceback", re.IGNORECASE)
# Every try starts with a header like "====[... /J18230/T71/C68.3/... on yoshi ]====".
_TRY_HEADER = re.compile(r"^====\[\S+ \S+ /J\d+/T\d+/C\d+\.\d+/", re.MULTILINE)


@dataclass(frozen=True)
class TryRow:
    """One failed try, as a row: what it shows, and how to find its log."""

    cells: tuple[str, ...]
    owner: str
    jid: int
    tid: int
    title: str
    # The try's key, such as "J18230/T71/C68.3", or "" when it isn't known.
    key: str
    machine: str
    # When it ended, in seconds since the epoch.
    when: float = 0.0
    color: str | None = None


class TriesPanel(QtWidgets.QSplitter):
    """Rows of failed tries over the log of the one chosen."""

    _rows: QtWidgets.QTreeWidget
    _heading: QtWidgets.QLabel
    _log: QtWidgets.QPlainTextEdit
    # Each task's log and when it was read, by (job, task).
    _logs: dict[tuple[int, int], tuple[float, str]]

    def __init__(
        self, headers: list[str], parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(QtCore.Qt.Vertical, parent)
        self._logs = {}
        self._rows = QtWidgets.QTreeWidget()
        self._rows.setHeaderLabels(headers)
        self._rows.setRootIsDecorated(False)
        self._rows.setUniformRowHeights(True)
        self._rows.setAlternatingRowColors(True)
        self._rows.setToolTip("Click a try to read its log below")
        self._rows.currentItemChanged.connect(lambda *_: self._chosen())
        widgets.sortable(self._rows)

        self._heading = QtWidgets.QLabel("Click a try above to read its log.")
        self._heading.setEnabled(False)
        self._log = QtWidgets.QPlainTextEdit()
        self._log.setObjectName(theme.LOG)
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        reader = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(reader)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self._heading)
        layout.addWidget(self._log, 1)

        self.addWidget(self._rows)
        self.addWidget(reader)
        self.setSizes([200, 360])

    def set_rows(self, rows: list[TryRow]) -> None:
        """Show `rows`, keeping the chosen try chosen if it is still there."""
        current = self._rows.currentItem()
        chosen: TryRow | None = current.data(0, _DATA_ROLE) if current else None
        self._rows.blockSignals(True)
        self._rows.clear()
        again = None
        for row in rows:
            item = widgets.SortableItem(list(row.cells))
            item.setData(0, _DATA_ROLE, row)
            # Its first column shows only the time; sort by the whole date.
            item.setData(0, widgets.SORT_ROLE, row.when)
            if row.color:
                for column in range(item.columnCount()):
                    item.setForeground(column, QtGui.QBrush(QtGui.QColor(row.color)))
            self._rows.addTopLevelItem(item)
            if chosen is not None and _same(row, chosen):
                again = item
        if again is not None:
            self._rows.setCurrentItem(again)
        self._rows.blockSignals(False)
        for column in range(self._rows.columnCount()):
            self._rows.resizeColumnToContents(column)
        if again is None:
            self._heading.setText(
                "Click a try above to read its log." if rows else "No failed tries."
            )
            self._log.clear()

    def _chosen(self) -> None:
        item = self._rows.currentItem()
        if item is None:
            return
        row: TryRow = item.data(0, _DATA_ROLE)
        cached = self._logs.get((row.jid, row.tid))
        if cached and time.time() - cached[0] < _CACHE_SECONDS:
            self._show(row, cached[1])
            return
        self._heading.setText(f"Reading the log of {row.title}…")
        background.run(
            lambda: tractor.task_log(row.owner, row.jid, row.tid, row.title),
            lambda text: self._read(row, text),
            lambda exc: self._heading.setText(str(exc)),
        )

    def _read(self, row: TryRow, text: str) -> None:
        self._logs[(row.jid, row.tid)] = (time.time(), text)
        item = self._rows.currentItem()
        if item is not None and _same(item.data(0, _DATA_ROLE), row):
            self._show(row, text)

    def _show(self, row: TryRow, text: str) -> None:
        number = row.key.rpartition(".")[2] if row.key else ""
        where = f", try {number} on {row.machine}" if number else ""
        self._heading.setText(f"Job {row.jid}, {row.title}{where}")
        self._heading.setEnabled(True)
        self._log.setPlainText(text)
        start, end = _try_span(text, row.key)
        self._shade(start, end)
        cursor = self._log.textCursor()
        cursor.setPosition(start if start >= 0 else len(text))
        self._log.setTextCursor(cursor)
        # The try's header at the top of the view, rather than its bottom edge.
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())
        self._log.ensureCursorVisible()

    def _shade(self, start: int, end: int) -> None:
        """Shade the chosen try, and every error line of the log."""
        selections = []
        if start >= 0:
            span = QtWidgets.QTextEdit.ExtraSelection()
            span.format.setBackground(_TRY_SHADE)
            span.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            span.cursor = QtGui.QTextCursor(self._log.document())
            span.cursor.setPosition(start)
            span.cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
            selections.append(span)
        block = self._log.document().begin()
        while block.isValid():
            if _ERROR_LINE.search(block.text()):
                line = QtWidgets.QTextEdit.ExtraSelection()
                line.format.setBackground(_ERROR_SHADE)
                line.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
                line.cursor = QtGui.QTextCursor(block)
                selections.append(line)
            block = block.next()
        self._log.setExtraSelections(selections)


def _try_span(text: str, key: str) -> tuple[int, int]:
    """Where the try `key` starts and ends in `text`, or (-1, -1) if it isn't there."""
    if not key:
        return -1, -1
    start = text.find(f"/{key}/")
    if start < 0:
        return -1, -1
    start = text.rfind("\n", 0, start) + 1
    after = _TRY_HEADER.search(text, start + 1)
    return start, after.start() if after else len(text)


def _same(a: TryRow, b: TryRow) -> bool:
    return (a.jid, a.tid, a.key) == (b.jid, b.tid, b.key)
