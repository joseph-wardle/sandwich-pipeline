"""A progress bar drawn in a table cell, for a task's or a job's percentage."""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

# The cell's percentage, 0 to 100, or None for no bar.
PERCENT_ROLE = QtCore.Qt.UserRole + 1
_TRACK = QtGui.QColor("#1b1c1f")
_FILL = QtGui.QColor("#3d79d6")
_DONE = QtGui.QColor("#4f8f5a")


class ProgressDelegate(QtWidgets.QStyledItemDelegate):
    """Draws the cell's PERCENT_ROLE as a bar with its number on it."""

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionViewItem,
        index: QtCore.QModelIndex,
    ) -> None:
        percent = index.data(PERCENT_ROLE)
        if percent is None:
            super().paint(painter, option, index)
            return
        # The row's selection and stripes first, as for any other cell.
        super().paint(painter, option, index)
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
