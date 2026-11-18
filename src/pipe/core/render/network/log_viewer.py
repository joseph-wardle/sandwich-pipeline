"""The Log Viewer: what Houdini says while it works, folded away under a tab
until the artist opens it, or until something fails."""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.network import theme

TITLE = "Log Viewer"
_OPEN_HEIGHT = 220


class LogViewer(QtWidgets.QWidget):
    """Put in a vertical QSplitter: open, its top edge drags to resize it."""

    _tab: QtWidgets.QToolButton
    _text: QtWidgets.QPlainTextEdit
    _unread: int

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._unread = 0
        self._tab = QtWidgets.QToolButton()
        self._tab.setCheckable(True)
        self._tab.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._tab.setStyleSheet(
            "QToolButton { padding: 3px 12px; border: 1px solid #45484f;"
            " border-bottom: none; background: #232428; }"
            "QToolButton:checked { background: #17181a; }"
        )
        self._tab.toggled.connect(self._toggled)

        self._text = QtWidgets.QPlainTextEdit()
        self._text.setObjectName(theme.LOG)
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(5000)
        self._text.setPlaceholderText("What Houdini says while it works shows here.")
        self._text.setFont(
            QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        )

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(6, 0, 0, 0)
        bar.addWidget(self._tab)
        bar.addStretch(1)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self._text, 1)
        self._toggled(False)

    def append(self, line: str) -> None:
        self._text.appendPlainText(line)
        if not self._tab.isChecked():
            self._unread += 1
            self._label()

    def expand(self) -> None:
        self._tab.setChecked(True)

    def _toggled(self, open_: bool) -> None:
        self._text.setVisible(open_)
        if open_:
            self._unread = 0
            self.setMaximumHeight(16_777_215)
            self._make_room()
        else:
            # Only the tab, which the splitter can't drag open.
            self.setMaximumHeight(self._tab.sizeHint().height())
        self._label()

    def _label(self) -> None:
        self._tab.setArrowType(
            QtCore.Qt.DownArrow if self._tab.isChecked() else QtCore.Qt.UpArrow
        )
        new = f"  ({self._unread} new lines)" if self._unread else ""
        self._tab.setText(TITLE + new)

    def _make_room(self) -> None:
        splitter = self.parentWidget()
        if not isinstance(splitter, QtWidgets.QSplitter):
            return
        index = splitter.indexOf(self)
        sizes = splitter.sizes()
        if index < 0 or sizes[index] >= _OPEN_HEIGHT:
            return
        taken = _OPEN_HEIGHT - sizes[index]
        sizes[index] = _OPEN_HEIGHT
        sizes[index - 1] = max(sizes[index - 1] - taken, 0)
        splitter.setSizes(sizes)
