"""The timeline scrub bar."""

from __future__ import annotations

from Qt.QtCore import QRect, QRectF, Qt
from Qt.QtGui import QFontDatabase, QMouseEvent, QPainter, QPaintEvent, QPalette
from Qt.QtWidgets import QSlider, QStyle, QStyleOptionSlider

_SCRUB_HEIGHT = 48


class TimelineSlider(QSlider):
    """Scrub bar."""

    _TICK_STEPS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000)
    _MIN_TICK_GAP = 5.0
    _TICK_HALF = 3  # px above/below the groove centre

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setFixedHeight(_SCRUB_HEIGHT)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

    # The parameter is `ev`, not `event`, because the QSlider stubs name it
    # that way and ty holds overrides to the stub's keyword.
    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self.setValue(
                QStyle.sliderValueFromPosition(
                    self.minimum(),
                    self.maximum(),
                    ev.pos().x(),
                    self.width(),
                )
            )
        # The handle now sits under the cursor, so the default handler starts
        # a normal drag from it.
        super().mousePressEvent(ev)

    def paintEvent(self, ev: QPaintEvent) -> None:
        super().paintEvent(ev)
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        control = QStyle.ComplexControl.CC_Slider
        groove = self.style().subControlRect(
            control, option, QStyle.SubControl.SC_SliderGroove, self
        )
        handle = self.style().subControlRect(
            control, option, QStyle.SubControl.SC_SliderHandle, self
        )
        painter = QPainter(self)
        self._paint_ticks(painter, groove, handle)
        self._paint_readout(painter, handle)
        painter.end()

    def _paint_ticks(self, painter: QPainter, groove: QRect, handle: QRect) -> None:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return
        # Values map onto the track the handle centre can actually reach.
        available = groove.width() - handle.width()
        if available <= 0:
            return
        px_per_frame = available / span
        step = next(
            (s for s in self._TICK_STEPS if s * px_per_frame >= self._MIN_TICK_GAP),
            0,
        )
        if step == 0:
            return  # even the coarsest step would smear

        left = groove.x() + handle.width() / 2

        # Snap every edge to the physical pixel grid. Without this, ticks get anti aliased into oblivion
        dpr = self.devicePixelRatioF()

        def snap(value: float) -> float:
            return round(value * dpr) / dpr

        tick_w = max(1, round(dpr)) / dpr
        top = snap(groove.center().y() - self._TICK_HALF)
        height = snap(self._TICK_HALF * 2)

        tick_color = self.palette().color(QPalette.ColorRole.WindowText)
        tick_color.setAlpha(110)
        frame = self.minimum()
        while frame <= self.maximum():
            x = left + (frame - self.minimum()) * px_per_frame
            frame += step
            if handle.left() <= x <= handle.right():
                continue
            painter.fillRect(QRectF(snap(x), top, tick_w, height), tick_color)

    def _paint_readout(self, painter: QPainter, handle: QRect) -> None:
        # The slider value is the current frame
        text = str(self.value())
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(text)
        x = min(max(handle.center().x() - text_w / 2, 0), self.width() - text_w)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(int(x), metrics.ascent(), text)


__all__ = ["TimelineSlider"]
