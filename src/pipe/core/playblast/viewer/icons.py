"""Transport glyphs drawn with QPainter."""

from __future__ import annotations

from collections.abc import Callable

from Qt.QtCore import QPointF, QRectF, Qt
from Qt.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF

# Painted at twice the logical size so antialiased edges stay sharp on
# high-DPI displays.
_SUPERSAMPLE = 2


def _icon(size: int, color: str, paint: Callable[[QPainter, float], None]) -> QIcon:
    pixmap = QPixmap(size * _SUPERSAMPLE, size * _SUPERSAMPLE)
    pixmap.setDevicePixelRatio(_SUPERSAMPLE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    paint(painter, float(size))
    painter.end()
    return QIcon(pixmap)


def _triangle(*points: tuple[float, float], scale: float) -> QPolygonF:
    return QPolygonF([QPointF(x * scale, y * scale) for x, y in points])


def play(color: str, size: int = 24) -> QIcon:
    def paint(painter: QPainter, s: float) -> None:
        painter.drawPolygon(_triangle((0.34, 0.24), (0.78, 0.5), (0.34, 0.76), scale=s))

    return _icon(size, color, paint)


def pause(color: str, size: int = 24) -> QIcon:
    def paint(painter: QPainter, s: float) -> None:
        painter.drawRect(QRectF(0.32 * s, 0.26 * s, 0.12 * s, 0.48 * s))
        painter.drawRect(QRectF(0.56 * s, 0.26 * s, 0.12 * s, 0.48 * s))

    return _icon(size, color, paint)


def step_back(color: str, size: int = 24) -> QIcon:
    def paint(painter: QPainter, s: float) -> None:
        painter.drawRect(QRectF(0.26 * s, 0.28 * s, 0.10 * s, 0.44 * s))
        painter.drawPolygon(_triangle((0.74, 0.28), (0.40, 0.5), (0.74, 0.72), scale=s))

    return _icon(size, color, paint)


def step_forward(color: str, size: int = 24) -> QIcon:
    def paint(painter: QPainter, s: float) -> None:
        painter.drawPolygon(_triangle((0.26, 0.28), (0.60, 0.5), (0.26, 0.72), scale=s))
        painter.drawRect(QRectF(0.64 * s, 0.28 * s, 0.10 * s, 0.44 * s))

    return _icon(size, color, paint)


__all__ = ["pause", "play", "step_back", "step_forward"]
