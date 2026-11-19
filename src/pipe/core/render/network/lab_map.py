"""A map of a lab room, with one machine's desk highlighted, to find it by.

The Farm Doctor opens it when a machine is double-clicked on its Machines
tab. The plan scales to fit the window, and the highlighted desk pulses so
the eye lands on it.
"""

from __future__ import annotations

import math

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.network import labs, theme

# How long one pulse of the highlighted desk takes, and how often it's redrawn.
_PULSE_SECONDS = 1.4
_FRAME_MS = 50
_DESK = QtGui.QColor("#2c2e34")
_DESK_EDGE = QtGui.QColor("#5d6068")
_FOUND = QtGui.QColor(theme.SUCCESS)
# Empty rows of a plan are aisles, drawn this much shorter than a row of desks.
_AISLE = 0.45


class RoomPlan(QtWidgets.QWidget):
    """A room's plan, its desks labelled with their machines, one highlighted."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._room: labs.Room | None = None
        self._found: int | None = None
        self._phase = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._pulse)
        self.setMouseTracking(True)
        self.setMinimumSize(240, 200)

    def show_room(self, room: labs.Room, found: int | None) -> None:
        self._room, self._found = room, found
        self._phase = 0.0
        if found is None:
            self._timer.stop()
        else:
            self._timer.start()
        self.update()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 (Qt's name)
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 (Qt's name)
        if self._found is not None:
            self._timer.start()
        super().showEvent(event)

    def _pulse(self) -> None:
        self._phase = (self._phase + _FRAME_MS / 1000 / _PULSE_SECONDS) % 1.0
        self.update()

    def _cells(self) -> list[tuple[QtCore.QRectF, labs.Spot]]:
        """Each spot of the plan and where it is drawn, scaled to fit."""
        if self._room is None or not self._room.plan:
            return []
        plan = self._room.plan
        heights = [1.0 if any(s is not None for s in row) else _AISLE for row in plan]
        columns = max(len(row) for row in plan)
        # Desks are wider than tall, for their machines' names.
        unit = min(
            (self.width() - 16) / (columns * 1.9), (self.height() - 16) / sum(heights)
        )
        width, height = unit * 1.9, unit
        left = (self.width() - width * columns) / 2
        top = (self.height() - height * sum(heights)) / 2
        cells = []
        for row, row_height in zip(plan, heights):
            for column, spot in enumerate(row):
                if spot is not None:
                    box = QtCore.QRectF(
                        left + column * width, top, width, height * row_height
                    )
                    cells.append((box.adjusted(3, 3, -3, -3), spot))
            top += height * row_height
        return cells

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt's name)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        room = self._room
        for box, spot in self._cells():
            if isinstance(spot, str):
                painter.setPen(QtGui.QColor(theme.DIM_TEXT))
                painter.drawText(box, QtCore.Qt.AlignCenter, spot)
                continue
            names = room.desks.get(spot, ()) if room else ()
            found = spot == self._found
            if found:
                glow = 0.5 + 0.5 * math.cos(self._phase * 2 * math.pi)
                fill = QtGui.QColor(_FOUND)
                fill.setAlphaF(0.35 + 0.45 * glow)
                painter.setPen(QtGui.QPen(_FOUND, 2 + 2 * glow))
                painter.setBrush(fill)
            else:
                painter.setPen(QtGui.QPen(_DESK_EDGE, 1))
                painter.setBrush(_DESK)
            painter.drawRoundedRect(box, 4, 4)
            font = painter.font()
            font.setPixelSize(max(7, int(box.height() * 0.32)))
            font.setBold(found)
            painter.setFont(font)
            painter.setPen(
                QtGui.QColor(theme.TEXT if found or names else theme.DISABLED_TEXT)
            )
            label = _short(names[0]) if names else str(spot)
            shown = painter.fontMetrics().elidedText(
                label, QtCore.Qt.ElideRight, int(box.width() - 4)
            )
            painter.drawText(box, QtCore.Qt.AlignCenter, shown)
        painter.end()

    def event(self, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.ToolTip and isinstance(
            event, QtGui.QHelpEvent
        ):
            tip = self._tip_at(QtCore.QPointF(event.pos()))
            if tip:
                QtWidgets.QToolTip.showText(event.globalPos(), tip, self)
            else:
                QtWidgets.QToolTip.hideText()
            return True
        return super().event(event)

    def _tip_at(self, point: QtCore.QPointF) -> str:
        for box, spot in self._cells():
            if box.contains(point) and isinstance(spot, int) and self._room:
                names = self._room.desks.get(spot, ())
                return f"Desk {spot}: " + (" / ".join(names) or "not on the map")
        return ""


class LabMapWindow(QtWidgets.QWidget):
    """A window with a room's map, showing where one machine is."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent, QtCore.Qt.Window)
        self.setWindowTitle("Lab Map")
        self._title = QtWidgets.QLabel()
        font = self._title.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self._title.setFont(font)
        self._where = QtWidgets.QLabel()
        self._where.setWordWrap(True)
        self._plan = RoomPlan()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._where)
        layout.addWidget(self._plan, 1)
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.resize(min(520, screen.width()), min(760, int(screen.height() * 0.85)))

    def show_machine(self, machine: str) -> None:
        """Show the room `machine` is in, its desk highlighted."""
        place = labs.place_of(machine)
        if place is None:
            self._title.setText("Not on a lab map")
            self.setWindowTitle("Lab Map")
            self._where.setText(
                f"{machine} isn't on the lab's map or list. Add it to labs.py "
                "once you know where it is."
            )
            self._plan.hide()
            return
        room = place.room
        self._title.setText(room.label)
        self.setWindowTitle(f"Lab Map: {room.label}")
        if isinstance(place.desk, int):
            where = f"<b>{machine}</b> is at the highlighted desk ({place.desk})."
        elif isinstance(place.desk, str):
            where = f"<b>{machine}</b> is {place.desk}."
        else:
            where = f"<b>{machine}</b> is in this room, but its desk isn't on the map."
        self._where.setText(where)
        self._plan.setVisible(bool(room.plan))
        self._plan.show_room(room, place.desk if isinstance(place.desk, int) else None)


def _short(name: str) -> str:
    """A machine's name, short enough for a desk: cs-1017524 as 1017524."""
    return name.removeprefix("cs-")
