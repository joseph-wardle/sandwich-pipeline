"""A graph of each render layer's EXR size across a job's frames, anomalies in red."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.app.canvas import event_position
from pipe.core.render.sizes import FrameSize, describe, human

_LINES = ("#6ca0e0", "#7fd18b", "#c9a14a", "#b58ce0", "#5cc8c8", "#e08a5c")
_ANOMALY = QtGui.QColor("#e05555")
_GRID = QtGui.QColor("#3a3d43")
_AXIS_TEXT = QtGui.QColor("#9a9da4")
_BACKGROUND = QtGui.QColor("#1c1d20")
_MARGIN = QtCore.QMargins(70, 20, 20, 40)
_HOVER_PIXELS = 12


class SizeGraph(QtWidgets.QWidget):
    """Hover a point to read its frame and size."""

    _series: dict[str, list[FrameSize]]
    _hidden: set[str]

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._series, self._hidden = {}, set()
        self.setMouseTracking(True)
        self.setMinimumSize(520, 300)

    def set_series(self, series: Mapping[str, Sequence[FrameSize]]) -> None:
        self._series = {name: list(sizes) for name, sizes in series.items() if sizes}
        self.update()

    def set_hidden(self, hidden: set[str]) -> None:
        self._hidden = hidden
        self.update()

    def colors(self) -> dict[str, str]:
        return {
            name: _LINES[index % len(_LINES)] for index, name in enumerate(self._series)
        }

    # ---- drawing -------------------------------------------------------

    def _shown(self) -> dict[str, list[FrameSize]]:
        return {n: s for n, s in self._series.items() if n not in self._hidden}

    def _bounds(self) -> tuple[int, int, float]:
        every = [size for sizes in self._shown().values() for size in sizes]
        if not every:
            return 0, 1, 1.0
        first = min(s.frame for s in every)
        last = max(s.frame for s in every)
        top = max(s.bytes for s in every) / 2**20
        return first, max(last, first + 1), _nice(top * 1.1)

    def _plot(self) -> QtCore.QRect:
        return self.rect().marginsRemoved(_MARGIN)

    def _point(self, size: FrameSize) -> QtCore.QPointF:
        first, last, top = self._bounds()
        plot = self._plot()
        x = plot.left() + (size.frame - first) / (last - first) * plot.width()
        y = plot.bottom() - (size.bytes / 2**20) / top * plot.height()
        return QtCore.QPointF(x, y)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt's name)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), _BACKGROUND)
        plot = self._plot()
        first, last, top = self._bounds()
        if not self._shown():
            painter.setPen(_AXIS_TEXT)
            painter.drawText(
                self.rect(), QtCore.Qt.AlignCenter, "No frames on disk yet."
            )
            return
        # Size lines across, frame ticks along.
        for step in range(5):
            value = top * step / 4
            y = plot.bottom() - plot.height() * step / 4
            painter.setPen(_GRID)
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))
            painter.setPen(_AXIS_TEXT)
            painter.drawText(
                QtCore.QRect(0, int(y) - 8, _MARGIN.left() - 8, 16),
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                f"{value:.0f} MB" if top >= 10 else f"{value:.1f} MB",
            )
        for step in range(6):
            frame = round(first + (last - first) * step / 5)
            x = plot.left() + plot.width() * step / 5
            painter.drawText(
                QtCore.QRect(int(x) - 30, plot.bottom() + 6, 60, 16),
                QtCore.Qt.AlignCenter,
                str(frame),
            )
        painter.drawText(
            QtCore.QRect(plot.left(), plot.bottom() + 22, plot.width(), 16),
            QtCore.Qt.AlignCenter,
            "Frame",
        )
        colors = self.colors()
        for name, sizes in self._shown().items():
            pen = QtGui.QPen(QtGui.QColor(colors[name]), 2)
            painter.setPen(pen)
            painter.drawPolyline(QtGui.QPolygonF([self._point(s) for s in sizes]))
            painter.setBrush(QtGui.QColor(colors[name]))
            for size in sizes:
                if not size.anomaly:
                    painter.drawEllipse(self._point(size), 2.5, 2.5)
        # On top of every line, so none hides one.
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 1))
        painter.setBrush(_ANOMALY)
        for sizes in self._shown().values():
            for size in sizes:
                if size.anomaly:
                    painter.drawEllipse(self._point(size), 5, 5)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802 (Qt's name)
        position = event_position(event)
        nearest: tuple[float, str, FrameSize] | None = None
        for name, sizes in self._shown().items():
            for size in sizes:
                point = self._point(size)
                distance = math.hypot(
                    point.x() - position.x(), point.y() - position.y()
                )
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, name, size)
        if nearest is None or nearest[0] > _HOVER_PIXELS:
            QtWidgets.QToolTip.hideText()
            return
        _, name, size = nearest
        note = describe(size)
        text = f"{name}  frame {size.frame}\n{human(size.bytes)}" + (
            f"\n{note}" if note else ""
        )
        QtWidgets.QToolTip.showText(self.mapToGlobal(position.toPoint()), text, self)


class SizeGraphDialog(QtWidgets.QDialog):
    def __init__(
        self,
        title: str,
        series: Mapping[str, Sequence[FrameSize]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 480)
        self._graph = SizeGraph()
        self._graph.set_series(series)
        legend = QtWidgets.QHBoxLayout()
        self._boxes: dict[str, QtWidgets.QCheckBox] = {}
        for name, color in self._graph.colors().items():
            flagged = sum(1 for s in series[name] if s.anomaly)
            box = QtWidgets.QCheckBox(f"{name}  ({flagged} odd)" if flagged else name)
            box.setChecked(True)
            box.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: bold; }}")
            box.toggled.connect(lambda _: self._filter())
            legend.addWidget(box)
            self._boxes[name] = box
        legend.addStretch(1)
        key = QtWidgets.QLabel(
            f"<span style='color:{_ANOMALY.name()}'>●</span> far from the frames "
            "around it"
        )
        legend.addWidget(key)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(legend)
        layout.addWidget(self._graph, 1)

    def _filter(self) -> None:
        self._graph.set_hidden(
            {name for name, box in self._boxes.items() if not box.isChecked()}
        )


def _nice(value: float) -> float:
    """`value` rounded up to 1, 2 or 5 times a power of ten, for the axis."""
    if value <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if value <= step * power:
            return step * power
    return 10 * power
