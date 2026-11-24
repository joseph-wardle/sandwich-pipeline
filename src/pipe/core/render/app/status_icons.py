"""Icons for ShotGrid statuses, drawn to look like the ones ShotGrid shows.

ShotGrid's own icons sit in a sprite sheet on its website that the API can't
download, so each is drawn here from the icon ShotGrid names for the status,
in the status's ShotGrid colour. An icon this doesn't know is a dot in that
colour with the status's first letter.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cache

from Qt import QtCore, QtGui

from pipe.core.shotgrid import Status

SIZE = 16
# Drawn at twice the size, so they stay sharp on high-density screens.
_SCALE = 2
_GREY = "#9a9da4"
# Colours for statuses ShotGrid gives none, by icon.
_DEFAULTS = {
    "icon_wtg": _GREY,
    "icon_rdy": "#c77ddb",
    "icon_ip": "#5cb85c",
    "icon_rev": "#5bc0de",
    "icon_apr": "#3fae49",
    "icon_cmpt": "#4a90d9",
    "icon_fin": "#8a8d93",
    "icon_hld": "#f0a030",
    "icon_alert": "#e8c43a",
    "icon_delete": "#d9534f",
    "icon_voice_command": "#d9534f",
    "icon_award": "#f0c43c",
    "icon_martini_glass": "#7fd18b",
    "icon_omt": "#8a8d93",
    "icon_restrict": "#8a8d93",
}
# Darker than this, a colour is raised to it, keeping its hue, to show on the
# dark window.
_MIN_LIGHTNESS = 120

Draw = Callable[[QtGui.QPainter, QtGui.QColor], None]


def icon(status: Status | None) -> QtGui.QIcon:
    """The status's icon; a dashed grey ring for a shot with no status."""
    if status is None:
        return _icon("", _GREY, "")
    color = (
        "#{:02x}{:02x}{:02x}".format(*status.color)
        if status.color
        else _DEFAULTS.get(status.icon or "", _GREY)
    )
    return _icon(status.icon or "", color, status.name[:1])


@cache
def _icon(key: str, color: str, letter: str) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(SIZE * _SCALE, SIZE * _SCALE)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.scale(_SCALE, _SCALE)
    shade = QtGui.QColor(color)
    if shade.lightness() < _MIN_LIGHTNESS:
        shade = QtGui.QColor.fromHsl(
            shade.hslHue(), shade.hslSaturation(), _MIN_LIGHTNESS
        )
    _DRAWINGS.get(key, lambda p, c: _letter(p, c, letter))(painter, shade)
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return QtGui.QIcon(pixmap)


# ---- drawings, on a 16 × 16 square ----------------------------------------

_BOX = QtCore.QRectF(1.5, 1.5, 13, 13)
_WHITE = QtGui.QColor("#ffffff")


def _pen(color: QtGui.QColor, width: float = 1.6) -> QtGui.QPen:
    pen = QtGui.QPen(color, width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    return pen


def _disc(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(_BOX)


def _ring(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    painter.setPen(_pen(color))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(_BOX)


def _waiting(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    _ring(painter, color)
    painter.drawLine(QtCore.QPointF(5, 8), QtCore.QPointF(11, 8))


def _ready(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    _ring(painter, color)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(QtCore.QPointF(8, 8), 2.2, 2.2)


def _in_progress(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    _ring(painter, color)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    # Half full, as work under way.
    painter.drawPie(_BOX, 90 * 16, -180 * 16)


def _check(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    _disc(painter, color)
    painter.setPen(_pen(_WHITE, 1.8))
    painter.drawPolyline(
        QtGui.QPolygonF(
            [
                QtCore.QPointF(4.8, 8.3),
                QtCore.QPointF(7, 10.5),
                QtCore.QPointF(11.2, 5.6),
            ]
        )
    )


def _hold(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    _disc(painter, color)
    painter.setPen(_pen(_WHITE, 2))
    painter.drawLine(QtCore.QPointF(6.3, 5), QtCore.QPointF(6.3, 11))
    painter.drawLine(QtCore.QPointF(9.7, 5), QtCore.QPointF(9.7, 11))


def _review(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    # An eye: waiting for someone to look.
    painter.setPen(_pen(color, 1.4))
    painter.setBrush(QtCore.Qt.NoBrush)
    path = QtGui.QPainterPath(QtCore.QPointF(1.5, 8))
    path.quadTo(QtCore.QPointF(8, 1), QtCore.QPointF(14.5, 8))
    path.quadTo(QtCore.QPointF(8, 15), QtCore.QPointF(1.5, 8))
    painter.drawPath(path)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(QtCore.QPointF(8, 8), 2.4, 2.4)


def _alert(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(
        QtGui.QPolygonF(
            [QtCore.QPointF(8, 1.5), QtCore.QPointF(15, 14), QtCore.QPointF(1, 14)]
        )
    )
    painter.setPen(_pen(QtGui.QColor("#1c1d20"), 1.8))
    painter.drawLine(QtCore.QPointF(8, 6), QtCore.QPointF(8, 9.5))
    painter.drawPoint(QtCore.QPointF(8, 12))


def _cross(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    painter.setPen(_pen(color, 2.4))
    painter.drawLine(QtCore.QPointF(3.5, 3.5), QtCore.QPointF(12.5, 12.5))
    painter.drawLine(QtCore.QPointF(12.5, 3.5), QtCore.QPointF(3.5, 12.5))


def _kickback(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    # A U-turn arrow: sent back.
    _disc(painter, color)
    painter.setPen(_pen(_WHITE, 1.6))
    painter.setBrush(QtCore.Qt.NoBrush)
    path = QtGui.QPainterPath(QtCore.QPointF(11, 11))
    path.lineTo(QtCore.QPointF(11, 7.5))
    path.arcTo(QtCore.QRectF(5, 4.5, 6, 6), 0, 180)
    path.lineTo(QtCore.QPointF(5, 9))
    painter.drawPath(path)
    painter.drawPolyline(
        QtGui.QPolygonF(
            [QtCore.QPointF(3.3, 7.6), QtCore.QPointF(5, 9.6), QtCore.QPointF(6.7, 7.6)]
        )
    )


def _star(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    points = []
    for index in range(10):
        radius = 7 if index % 2 == 0 else 3
        angle = math.radians(-90 + index * 36)
        points.append(
            QtCore.QPointF(8 + radius * math.cos(angle), 8.6 + radius * math.sin(angle))
        )
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(QtGui.QPolygonF(points))


def _martini(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    painter.setPen(_pen(color, 1.5))
    painter.setBrush(color)
    painter.drawPolygon(
        QtGui.QPolygonF(
            [QtCore.QPointF(2, 2.5), QtCore.QPointF(14, 2.5), QtCore.QPointF(8, 8.5)]
        )
    )
    painter.drawLine(QtCore.QPointF(8, 8.5), QtCore.QPointF(8, 13.5))
    painter.drawLine(QtCore.QPointF(5, 13.8), QtCore.QPointF(11, 13.8))


def _slashed(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    # A ring struck through: left out.
    _ring(painter, color)
    painter.drawLine(QtCore.QPointF(4, 12), QtCore.QPointF(12, 4))


def _flag(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    painter.setPen(_pen(color, 1.6))
    painter.drawLine(QtCore.QPointF(3.5, 2), QtCore.QPointF(3.5, 14.5))
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(
        QtGui.QPolygonF(
            [
                QtCore.QPointF(4.3, 2.3),
                QtCore.QPointF(13.5, 5.3),
                QtCore.QPointF(4.3, 8.8),
            ]
        )
    )


def _arrow(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    _disc(painter, color)
    painter.setPen(_pen(QtGui.QColor("#1c1d20"), 1.8))
    painter.drawLine(QtCore.QPointF(4.5, 8), QtCore.QPointF(11, 8))
    painter.drawPolyline(
        QtGui.QPolygonF(
            [QtCore.QPointF(8, 5), QtCore.QPointF(11.2, 8), QtCore.QPointF(8, 11)]
        )
    )


def _hot(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    _disc(painter, color)
    painter.setPen(_pen(_WHITE, 1.9))
    painter.drawLine(QtCore.QPointF(8, 4.5), QtCore.QPointF(8, 9))
    painter.drawPoint(QtCore.QPointF(8, 11.8))


def _confirmed(painter: QtGui.QPainter, color: QtGui.QColor) -> None:
    _ring(painter, color)
    painter.setPen(_pen(color, 1.8))
    painter.drawPolyline(
        QtGui.QPolygonF(
            [
                QtCore.QPointF(4.8, 8.3),
                QtCore.QPointF(7, 10.5),
                QtCore.QPointF(11.2, 5.6),
            ]
        )
    )


def _letter(painter: QtGui.QPainter, color: QtGui.QColor, letter: str) -> None:
    if not letter:
        pen = _pen(color, 1.4)
        pen.setStyle(QtCore.Qt.DotLine)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(_BOX)
        return
    _disc(painter, color)
    font = QtGui.QFont()
    font.setPixelSize(10)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(_WHITE if color.lightness() < 160 else QtGui.QColor("#1c1d20"))
    painter.drawText(_BOX, QtCore.Qt.AlignCenter, letter.upper())


_DRAWINGS: dict[str, Draw] = {
    "icon_wtg": _waiting,
    "icon_rdy": _ready,
    "icon_ip": _in_progress,
    "icon_rev": _review,
    "icon_apr": _check,
    "icon_cmpt": _check,
    "icon_fin": _check,
    "icon_hld": _hold,
    "icon_alert": _alert,
    "icon_delete": _cross,
    "icon_voice_command": _kickback,
    "icon_award": _star,
    "icon_martini_glass": _martini,
    "icon_omt": _slashed,
    "icon_restrict": _slashed,
    "icon_flag": _flag,
    "icon_arrow_right2": _arrow,
    "icon_hot": _hot,
    "icon_thumb_up": _confirmed,
    "icon_announcement": _alert,
}
