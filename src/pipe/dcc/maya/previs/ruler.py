"""Frame ruler. Per-frame minor ticks, labeled major ticks every 24f.

Dragging anywhere on the ruler scrubs the scene time via the `on_scrub` callback.
"""

from __future__ import annotations

from typing import Callable

from Qt import QtGui
from Qt.QtGui import QColor, QFont, QPainter
from Qt.QtWidgets import QWidget

from . import _qt, style

MAJOR_INTERVAL = 24  # frames between labeled major ticks (1s @ 24fps)
RULER_HEIGHT = 32

_MINOR_DENSITY_PX = (
    2  # only draw per-frame ticks when each frame gets at least this many px
)


class Ruler(QWidget):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        on_scrub: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._first_frame = 1001
        self._last_frame = 1001
        self._on_scrub = on_scrub
        self.setFixedHeight(RULER_HEIGHT)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor(style.PANEL_BG_DEEP))
        self.setPalette(palette)
        if on_scrub is not None:
            self.setCursor(_qt.POINTING_HAND)

    def set_range(self, first: int, last: int) -> None:
        self._first_frame = first
        self._last_frame = max(first, last)
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self._scrub(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        self._scrub(event)

    def _scrub(self, event: QtGui.QMouseEvent) -> None:
        if self._on_scrub is None:
            return
        self._on_scrub(self._frame_at(event.pos().x()))

    def _frame_at(self, x: int) -> int:
        """Inverse of `paintEvent`'s frame→x mapping, clamped to the ruler's range."""
        total = self._last_frame - self._first_frame + 1
        width = self.width()
        if total <= 0 or width <= 0:
            return self._first_frame
        frame = self._first_frame + round(x * total / width)
        return max(self._first_frame, min(self._last_frame, frame))

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        width = self.width()
        height = self.height()
        total = self._last_frame - self._first_frame + 1
        if total <= 0 or width <= 0:
            return
        px_per_frame = width / total

        minor_pen = QColor(255, 255, 255, 25)
        major_pen = QColor(255, 255, 255, 140)
        label_color = QColor(style.PANEL_TEXT_DIM)

        if px_per_frame >= _MINOR_DENSITY_PX:
            painter.setPen(minor_pen)
            for frame in range(self._first_frame, self._last_frame + 1):
                x = int((frame - self._first_frame) * px_per_frame)
                painter.drawLine(x, height - 3, x, height)

        painter.setFont(QFont("monospace", 8))
        painter.setPen(major_pen)
        frame = self._first_frame
        while frame <= self._last_frame:
            x = int((frame - self._first_frame) * px_per_frame)
            painter.drawLine(x, height - 11, x, height)
            painter.setPen(label_color)
            painter.drawText(x + 4, height - 16, str(frame))
            painter.setPen(major_pen)
            frame += MAJOR_INTERVAL

        # right edge marker
        painter.setPen(major_pen)
        painter.drawLine(width - 1, 0, width - 1, height)
        event.accept()
