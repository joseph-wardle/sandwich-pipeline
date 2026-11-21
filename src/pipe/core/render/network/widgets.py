"""Small pieces the Sandwich Renderer's pages share: buttons with icons, page
headings, banners and status chips, so every page reads the same way."""

from __future__ import annotations

import re
from collections.abc import Callable

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.network import icons, theme

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
