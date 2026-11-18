"""Every AOV and LPE of one frame side by side, so a missing or broken one shows
at a glance: those that rendered nothing, or broke, carry a big red X."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.network.frames import Thumbnail

# How wide each picture is read and drawn.
THUMB_WIDTH = 256
_THUMB = QtCore.QSize(THUMB_WIDTH, 144)
_BACKGROUND = QtGui.QColor("#141517")
_X = QtGui.QColor("#e03c3c")
_DATA_ROLE = QtCore.Qt.UserRole


class ContactSheet(QtWidgets.QListWidget):
    """`chosen` names the group double-clicked, to show it on its own."""

    chosen = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setViewMode(QtWidgets.QListView.IconMode)
        self.setResizeMode(QtWidgets.QListView.Adjust)
        self.setMovement(QtWidgets.QListView.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setIconSize(_THUMB)
        self.setGridSize(QtCore.QSize(_THUMB.width() + 16, _THUMB.height() + 40))
        self.setSpacing(4)
        self.setWordWrap(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setStyleSheet(f"QListWidget {{ background-color: {_BACKGROUND.name()}; }}")
        self.setToolTip("Double-click one to look at it on its own")
        self.itemDoubleClicked.connect(
            lambda item: self.chosen.emit(item.data(_DATA_ROLE))
        )

    def show_thumbnails(
        self,
        thumbs: Sequence[Thumbnail],
        to_screen: Callable[[Thumbnail], np.ndarray],
        current: str = "",
    ) -> None:
        """`to_screen` turns a thumbnail's pixels into 8-bit RGB through its view."""
        self.clear()
        for thumb in thumbs:
            name = thumb.group.name
            if thumb.pixels is None:
                icon = _crossed()
                text = f"{name}\n✕ {thumb.problem}"
            else:
                icon = _picture(to_screen(thumb))
                text = name
            item = QtWidgets.QListWidgetItem(QtGui.QIcon(icon), text)
            item.setData(_DATA_ROLE, name)
            item.setToolTip(f"{name}: {thumb.problem}" if thumb.problem else f"{name}")
            if thumb.problem:
                item.setForeground(QtGui.QBrush(_X))
            self.addItem(item)
            if name == current:
                item.setSelected(True)

    def show_message(self, message: str) -> None:
        self.clear()
        item = QtWidgets.QListWidgetItem(message)
        item.setFlags(QtCore.Qt.NoItemFlags)
        self.addItem(item)


def _picture(rgb: np.ndarray) -> QtGui.QPixmap:
    height, width, _ = rgb.shape
    image = QtGui.QImage(
        np.ascontiguousarray(rgb).tobytes(),
        width,
        height,
        3 * width,
        QtGui.QImage.Format_RGB888,
    ).copy()
    canvas = _blank()
    painter = QtGui.QPainter(canvas)
    scaled = image.scaled(
        _THUMB, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
    )
    painter.drawImage(
        (_THUMB.width() - scaled.width()) // 2,
        (_THUMB.height() - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    return canvas


def _crossed() -> QtGui.QPixmap:
    canvas = _blank()
    painter = QtGui.QPainter(canvas)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(_X, 14)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    painter.setPen(pen)
    # A square X in the middle, as big as the picture is tall.
    side = _THUMB.height() - 24
    left = (_THUMB.width() - side) // 2
    top = 12
    painter.drawLine(left, top, left + side, top + side)
    painter.drawLine(left + side, top, left, top + side)
    painter.end()
    return canvas


def _blank() -> QtGui.QPixmap:
    canvas = QtGui.QPixmap(_THUMB)
    canvas.fill(QtGui.QColor("#202124"))
    return canvas
