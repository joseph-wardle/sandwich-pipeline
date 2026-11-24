"""Small widgets the Sandwich Renderer's pages share: buttons with icons, page
headings, banners, status chips, sortable tables and the Log Viewer."""

from __future__ import annotations

import re
from collections.abc import Callable

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.app import icons, theme

TITLE = "Sandwich Renderer"

ICON_SIZE = QtCore.QSize(16, 16)
# A cell's value to sort by, when its text doesn't sort right, such as a date
# shown as "Nov 18 16:26" or a progress bar with no text.
SORT_ROLE = QtCore.Qt.UserRole + 7
_CHUNKS = re.compile(r"(\d+(?:\.\d+)?)")
# Styled buttons draw their icon flush against their text, so text beside an
# icon starts with this.
GAP = " "

# Banner kinds, by their colour and icon.
INFO = "info"
WARNING = "warning"
ERROR = "error"
_BANNERS = {
    INFO: (theme.INFO, "info", "#16263b"),
    WARNING: (theme.WARNING, "alert", "#2e2614"),
    ERROR: (theme.DANGER_COLOR, "circle-alert", "#341a1a"),
}


def button(
    text: str,
    icon: str | None = None,
    tooltip: str = "",
    role: str | None = None,
    clicked: Callable[[], object] | None = None,
) -> QtWidgets.QPushButton:
    """A push button with a line icon before its text."""
    widget = QtWidgets.QPushButton(GAP + text if icon else text)
    if icon:
        color = "#ffffff" if role == theme.PRIMARY else theme.TEXT
        widget.setIcon(icons.get(icon, color))
        widget.setIconSize(ICON_SIZE)
    if tooltip:
        widget.setToolTip(tooltip)
    if role:
        theme.role(widget, role)
    if clicked is not None:
        widget.clicked.connect(lambda *_: clicked())
    widget.setCursor(QtCore.Qt.PointingHandCursor)
    return widget


def tool_button(
    icon: str,
    tooltip: str,
    text: str = "",
    clicked: Callable[[], object] | None = None,
) -> QtWidgets.QToolButton:
    """A flat button, an icon alone unless `text` is given."""
    widget = QtWidgets.QToolButton()
    widget.setIcon(icons.get(icon))
    widget.setIconSize(ICON_SIZE)
    widget.setToolTip(tooltip)
    if text:
        widget.setText(GAP + text)
        widget.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
    if clicked is not None:
        widget.clicked.connect(lambda *_: clicked())
    widget.setCursor(QtCore.Qt.PointingHandCursor)
    return widget


def icon_label(
    name: str, color: str = theme.DIM_TEXT, size: int = 16
) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel()
    label.setPixmap(icons.pixmap(name, color, size))
    label.setFixedSize(size, size)
    return label


class PageHeading(QtWidgets.QWidget):
    """A page's icon, title and one line on what it is for."""

    def __init__(
        self,
        icon: str,
        title: str,
        subtitle: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        badge = QtWidgets.QLabel()
        badge.setPixmap(icons.pixmap(icon, theme.ACCENT, 20))
        badge.setFixedSize(34, 34)
        badge.setAlignment(QtCore.Qt.AlignCenter)
        badge.setStyleSheet(
            f"QLabel {{ background-color: {theme.ACCENT_MUTED}; border-radius: 8px; }}"
        )
        name = QtWidgets.QLabel(title)
        name.setStyleSheet("QLabel { font-size: 14pt; font-weight: 600; }")
        words = QtWidgets.QVBoxLayout()
        words.setSpacing(0)
        words.addWidget(name)
        if subtitle:
            line = QtWidgets.QLabel(subtitle)
            line.setStyleSheet(f"QLabel {{ color: {theme.DIM_TEXT}; }}")
            words.addWidget(line)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(badge, 0, QtCore.Qt.AlignVCenter)
        layout.addLayout(words)
        layout.addStretch(1)


class Banner(QtWidgets.QFrame):
    """A tinted strip with an icon, for a note the artist should read."""

    def __init__(
        self, text: str = "", kind: str = INFO, parent: QtWidgets.QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(16, 16)
        self._text = QtWidgets.QLabel(text)
        self._text.setWordWrap(True)
        self._text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(9)
        layout.addWidget(self._icon, 0, QtCore.Qt.AlignTop)
        layout.addWidget(self._text, 1)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        color, icon, fill = _BANNERS[kind]
        self._icon.setPixmap(icons.pixmap(icon, color, 16))
        self.setObjectName("banner")
        self.setStyleSheet(
            f"QFrame#banner {{ background-color: {fill}; border: 1px solid {rgba(color, 70)};"
            f" border-left: 3px solid {color}; border-radius: 6px; }}"
            f"QFrame#banner QLabel {{ color: {theme.TEXT}; background: transparent; }}"
        )

    def setText(self, text: str) -> None:  # noqa: N802 (Qt's name)
        self._text.setText(text)


class Chip(QtWidgets.QLabel):
    """A small rounded tag in one colour, such as "Unsaved changes"."""

    def __init__(
        self,
        text: str = "",
        color: str = theme.DIM_TEXT,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.set(text, color)

    def set(self, text: str, color: str) -> None:
        self.setText(text)
        self.setStyleSheet(
            f"QLabel {{ color: {color}; background-color: {rgba(color, 34)};"
            f" border: 1px solid {rgba(color, 100)}; border-radius: 9px; padding: 2px 10px;"
            " font-weight: 600; }"
        )


def rgba(color: str, alpha: int) -> str:
    """`color` at `alpha` of 255, for a style sheet, which reads 8-digit hex as ARGB."""
    shade = QtGui.QColor(color)
    return f"rgba({shade.red()}, {shade.green()}, {shade.blue()}, {alpha})"


def separator() -> QtWidgets.QFrame:
    """A thin vertical rule between groups of buttons."""
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.VLine)
    line.setFixedWidth(1)
    line.setStyleSheet("QFrame { background-color: #34363d; border: none; }")
    return line


class SortableItem(QtWidgets.QTreeWidgetItem):
    """A table row that sorts numbers as numbers, so "9" comes before "10" and
    "6 GB" before "122 GB", or by a cell's SORT_ROLE when it has one."""

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, QtWidgets.QTreeWidgetItem):
            return NotImplemented
        table = self.treeWidget()
        column = table.sortColumn() if table is not None else 0
        mine, theirs = self.data(column, SORT_ROLE), other.data(column, SORT_ROLE)
        if mine is not None and theirs is not None:
            return mine < theirs
        return _sort_key(self.text(column)) < _sort_key(other.text(column))


def sortable(table: QtWidgets.QTreeWidget) -> None:
    """Let the artist sort `table` by clicking a column's header. Rows keep the
    order they were added in until then."""
    table.header().setSortIndicator(-1, QtCore.Qt.AscendingOrder)
    table.setSortingEnabled(True)


def _sort_key(text: str) -> tuple[tuple[int, float, str], ...]:
    """`text` split into numbers and words, so each compares as what it is:
    each part is (0, number, "") or (1, 0, word). Blank cells sort last."""
    if not text.strip() or text.strip() == "—":
        return ((2, 0.0, ""),)
    return tuple(
        (0, float(chunk), "") if index % 2 else (1, 0.0, chunk.lower())
        for index, chunk in enumerate(_CHUNKS.split(text))
        if chunk
    )


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
        self._tab.setText(" " + _LOG_TITLE + new)

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
