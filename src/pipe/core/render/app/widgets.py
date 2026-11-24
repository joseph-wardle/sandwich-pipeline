"""The window's dark theme, and small widgets several of its parts use."""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

TITLE = "Sandwich Renderer"

# Panels with their own shade, so the eye finds each without reading its title.
SHOTS = "shotsPanel"
PARMS = "parmsPanel"
STAGE = "stagePanel"
LOG = "logPanel"

_WINDOW = "#2b2d31"
_BASE = "#232428"
_ALTERNATE = "#2d2f34"
_TEXT = "#dcdde0"
_DIM_TEXT = "#7d8087"
_BUTTON = "#3a3d43"
_HIGHLIGHT = "#3d79d6"
_BORDER = "#45484f"

_SHOTS_SHADE = "#1c1d20"
_PARMS_SHADE = "#313338"
_STAGE_SHADE = "#26282c"
_LOG_SHADE = "#17181a"

_STYLE = f"""
QToolTip {{ color: {_TEXT}; background-color: #1a1b1e; border: 1px solid {_BORDER}; padding: 4px; }}
QWidget#{SHOTS}, QWidget#{SHOTS} QTreeWidget {{ background-color: {_SHOTS_SHADE}; }}
QWidget#{SHOTS} QTreeWidget {{ border: none; }}
QScrollArea#{PARMS}, QScrollArea#{PARMS} > QWidget > QWidget {{ background-color: {_PARMS_SHADE}; }}
QWidget#{STAGE} {{ background-color: {_STAGE_SHADE}; }}
QPlainTextEdit#{LOG} {{ background-color: {_LOG_SHADE}; color: #b8bbc2; border: none; }}
QTabWidget::pane {{ border: 1px solid {_BORDER}; border-top: 2px solid {_HIGHLIGHT}; }}
QTabBar::tab {{ background: {_BASE}; color: {_DIM_TEXT}; padding: 6px 16px; border: 1px solid {_BORDER}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {_PARMS_SHADE}; color: {_TEXT}; }}
QGroupBox {{ border: 1px solid {_BORDER}; border-radius: 4px; margin-top: 12px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {_TEXT}; }}
QHeaderView::section {{ background-color: {_BUTTON}; color: {_TEXT}; border: none; border-right: 1px solid {_BORDER}; padding: 3px 6px; }}
QSplitter::handle {{ background-color: #111214; }}
QSplitter::handle:hover {{ background-color: {_HIGHLIGHT}; }}
"""

_DOT_COLOR = "#f0c43c"
_DOT_TIP = "Changed since the render hip was saved"
_DOT_SIZE = 12

# A table cell's percentage, 0 to 100, or None for no bar.
PERCENT_ROLE = QtCore.Qt.UserRole + 1
_TRACK = QtGui.QColor("#1b1c1f")
_FILL = QtGui.QColor("#3d79d6")
_DONE = QtGui.QColor("#4f8f5a")

_LOG_TITLE = "Log Viewer"
_LOG_OPEN_HEIGHT = 220


def apply_theme(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    palette = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.Window, _WINDOW),
        (QtGui.QPalette.WindowText, _TEXT),
        (QtGui.QPalette.Base, _BASE),
        (QtGui.QPalette.AlternateBase, _ALTERNATE),
        (QtGui.QPalette.ToolTipBase, "#1a1b1e"),
        (QtGui.QPalette.ToolTipText, _TEXT),
        (QtGui.QPalette.Text, _TEXT),
        (QtGui.QPalette.Button, _BUTTON),
        (QtGui.QPalette.ButtonText, _TEXT),
        (QtGui.QPalette.BrightText, "#ff6b6b"),
        (QtGui.QPalette.Link, "#6ea8ff"),
        (QtGui.QPalette.Highlight, _HIGHLIGHT),
        (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.PlaceholderText, _DIM_TEXT),
    ):
        palette.setColor(role, QtGui.QColor(color))
    for role in (
        QtGui.QPalette.WindowText,
        QtGui.QPalette.Text,
        QtGui.QPalette.ButtonText,
    ):
        palette.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor(_DIM_TEXT))
    palette.setColor(
        QtGui.QPalette.Disabled, QtGui.QPalette.Button, QtGui.QColor("#303237")
    )
    app.setPalette(palette)
    app.setStyleSheet(_STYLE)


def tell(
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


def shade(widget: QtWidgets.QWidget, panel: str) -> None:
    """Give `widget` its panel's background."""
    widget.setObjectName(panel)
    # A plain QWidget draws a style sheet's background only when told to.
    widget.setAttribute(QtCore.Qt.WA_StyledBackground, True)


class ChangeDot(QtWidgets.QLabel):
    """The yellow dot beside a parm or layer changed since the last save.

    Always as wide as a dot, so marking a row never shifts its fields.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_DOT_SIZE)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setStyleSheet(f"QLabel {{ color: {_DOT_COLOR}; }}")
        self.set_changed(False)

    def set_changed(self, changed: bool) -> None:
        self.setText("●" if changed else "")
        self.setToolTip(_DOT_TIP if changed else "")


def dot_icon() -> QtGui.QIcon:
    """The dot as an icon, for a tab whose parms changed."""
    pixmap = QtGui.QPixmap(_DOT_SIZE, _DOT_SIZE)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(_DOT_COLOR))
    painter.drawEllipse(2, 2, _DOT_SIZE - 4, _DOT_SIZE - 4)
    painter.end()
    return QtGui.QIcon(pixmap)


class ProgressDelegate(QtWidgets.QStyledItemDelegate):
    """Draws a cell's PERCENT_ROLE as a bar with its number on it."""

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        # The row's selection and stripes first, as for any other cell.
        super().paint(painter, option, index)
        percent = index.data(PERCENT_ROLE)
        if percent is None:
            return
        bar = option.rect.adjusted(3, 3, -3, -3)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(_TRACK)
        painter.drawRoundedRect(bar, 3, 3)
        filled = QtCore.QRect(bar)
        filled.setWidth(int(bar.width() * max(0, min(int(percent), 100)) / 100))
        painter.setBrush(_DONE if percent >= 100 else _FILL)
        painter.drawRoundedRect(filled, 3, 3)
        painter.setPen(QtGui.QColor("#ffffff"))
        painter.drawText(bar, QtCore.Qt.AlignCenter, f"{int(percent)}%")
        painter.restore()

    def sizeHint(  # noqa: N802 (Qt's name)
        self, option: QtWidgets.QStyleOptionViewItem, index: QtCore.QModelIndex
    ) -> QtCore.QSize:
        hint = super().sizeHint(option, index)
        return QtCore.QSize(max(hint.width(), 90), hint.height())


class LogViewer(QtWidgets.QWidget):
    """What Houdini says while it works, folded under a tab until opened or
    something fails. Put it in a vertical QSplitter, so its top edge drags."""

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
        self._text.setObjectName(LOG)
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
        self._tab.setText(_LOG_TITLE + new)

    def _make_room(self) -> None:
        splitter = self.parentWidget()
        if not isinstance(splitter, QtWidgets.QSplitter):
            return
        index = splitter.indexOf(self)
        sizes = splitter.sizes()
        if index < 0 or sizes[index] >= _LOG_OPEN_HEIGHT:
            return
        taken = _LOG_OPEN_HEIGHT - sizes[index]
        sizes[index] = _LOG_OPEN_HEIGHT
        sizes[index - 1] = max(sizes[index - 1] - taken, 0)
        splitter.setSizes(sizes)
