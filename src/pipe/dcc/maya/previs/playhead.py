"""The playhead: a pentagon head in the ruler band over a slim full-height line.

Painted rather than a plain rect, so the head reads as the grab point while the
line itself stays thin enough not to fight the track blocks behind it.
"""

from __future__ import annotations

from Qt import QtGui
from Qt.QtCore import QPoint
from Qt.QtGui import QColor, QPainter, QPolygon
from Qt.QtWidgets import QWidget

from . import _qt, style

HANDLE_WIDTH = 11  # odd, so the line and head tip land on a whole centre pixel
_HEAD_BODY = 5  # rectangular shoulders of the head, in the ruler band
_HEAD_HEIGHT = 10  # shoulders plus the downward tip that marks the frame
_LINE_WIDTH = 2


class Playhead(QWidget):
    """Free overlay child of the timeline's scroll content; never enters the grid."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(HANDLE_WIDTH)
        self.setAttribute(_qt.TRANSPARENT_FOR_MOUSE, True)  # clicks fall through
        self.hide()

    def move_to(self, center_x: int, height: int) -> None:
        """Centre the head and line on `center_x`, spanning `height` pixels down."""
        self.setGeometry(center_x - HANDLE_WIDTH // 2, 0, HANDLE_WIDTH, height)
        self.show()
        self.raise_()  # newly rebuilt grid widgets would otherwise cover it

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(style.PLAYHEAD)
        center = self.width() // 2

        painter.fillRect(
            center - _LINE_WIDTH // 2, 0, _LINE_WIDTH, self.height(), color
        )

        painter.setPen(color)
        painter.setBrush(color)
        right = self.width() - 1
        painter.drawPolygon(
            QPolygon(
                [
                    QPoint(0, 0),
                    QPoint(right, 0),
                    QPoint(right, _HEAD_BODY),
                    QPoint(center, _HEAD_HEIGHT),
                    QPoint(0, _HEAD_BODY),
                ]
            )
        )
        event.accept()
