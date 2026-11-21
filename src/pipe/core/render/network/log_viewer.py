"""The Log Viewer: what Houdini says while it works, folded away under a tab
until the artist opens it, or until something fails."""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.network import icons, theme

TITLE = "Log Viewer"
_OPEN_HEIGHT = 220


class LogViewer(QtWidgets.QWidget):
    """Put in a vertical QSplitter: open, its top edge drags to resize it."""

    _tab: QtWidgets.QToolButton
    _chevron: QtWidgets.QToolButton
    _text: QtWidgets.QPlainTextEdit
    _unread: int

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._unread = 0
        self._tab = QtWidgets.QToolButton()
        self._tab.setCheckable(True)
        self._tab.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._tab.setIcon(icons.get("terminal", theme.DIM_TEXT))
        self._tab.setCursor(QtCore.Qt.PointingHandCursor)
        self._tab.setToolTip("Show or hide what Houdini says while it works")
        self._tab.setStyleSheet(
            "QToolButton { padding: 4px 10px; border: none; border-radius: 0;"
            f" color: {theme.DIM_TEXT}; background: transparent; }}"
            f"QToolButton:hover {{ color: {theme.TEXT}; }}"
            f"QToolButton:checked {{ color: {theme.TEXT}; background: transparent; }}"
        )
        self._chevron = QtWidgets.QToolButton()
        self._chevron.setCursor(QtCore.Qt.PointingHandCursor)
        self._chevron.setAutoRaise(True)
        self._chevron.clicked.connect(lambda *_: self._tab.toggle())
        self._tab.toggled.connect(self._toggled)

        self._text = QtWidgets.QPlainTextEdit()
        self._text.setObjectName(theme.LOG)
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(5000)
        self._text.setPlaceholderText("What Houdini says while it works shows here.")
        self._text.setFont(
            QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        )

        strip = QtWidgets.QWidget()
        theme.shade(strip, theme.HEADER)
        bar = QtWidgets.QHBoxLayout(strip)
        bar.setContentsMargins(6, 0, 10, 0)
        bar.addWidget(self._tab)
        bar.addStretch(1)
        bar.addWidget(self._chevron)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(strip)
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
            self.setMaximumHeight(self._tab.sizeHint().height() + 1)
        self._label()

    def _label(self) -> None:
        self._chevron.setIcon(
            icons.get(
                "chevron-down" if self._tab.isChecked() else "chevron-up",
                theme.DIM_TEXT,
            )
        )
        new = f"  ·  {self._unread} new lines" if self._unread else ""
        self._tab.setText(" " + TITLE + new)

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
