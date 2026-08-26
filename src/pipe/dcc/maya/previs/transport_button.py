"""The cut transport's play/pause button."""

from __future__ import annotations

from Qt.QtCore import QPoint, QSize
from Qt.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygon
from Qt.QtWidgets import QPushButton, QWidget

from . import style

_WIDTH = 38
_ICON = 12  # px square the glyph is drawn into
_INSET = 1  # blank margin, so antialiasing has somewhere to land
_BAR = 3  # width of one pause bar


class TransportButton(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setCheckable(True)
        self.setFixedWidth(_WIDTH)
        self.setStyleSheet(style.TOOLBAR_BUTTON)
        self.setToolTip("Play the cut in real time, looping at the end")
        self.setIconSize(QSize(_ICON, _ICON))
        # The pause glyph is only ever seen on the checked button, which the
        # stylesheet fills with the accent; the play glyph never is.
        self._play = _play_icon(style.PANEL_TEXT)
        self._pause = _pause_icon(style.ACCENT_TEXT)
        self.set_playing(False)

    def set_playing(self, playing: bool) -> None:
        self.setChecked(playing)
        self.setIcon(self._pause if playing else self._play)


def _play_icon(color: str) -> QIcon:
    pixmap, painter = _blank(color)
    far = _ICON - _INSET
    painter.drawPolygon(
        QPolygon(
            [
                QPoint(_INSET + 1, _INSET),
                QPoint(far, _ICON // 2),
                QPoint(_INSET + 1, far),
            ]
        )
    )
    painter.end()
    return QIcon(pixmap)


def _pause_icon(color: str) -> QIcon:
    pixmap, painter = _blank(color)
    for x in (_INSET + 1, _ICON - _INSET - _BAR - 1):
        painter.fillRect(x, _INSET, _BAR, _ICON - 2 * _INSET, painter.brush())
    painter.end()
    return QIcon(pixmap)


def _blank(color: str) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(_ICON, _ICON)
    pixmap.fill(QColor(0, 0, 0, 0))  # transparent, so only the glyph shows
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QColor(color))
    painter.setBrush(QColor(color))
    return pixmap, painter
