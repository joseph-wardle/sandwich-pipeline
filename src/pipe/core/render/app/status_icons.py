"""ShotGrid status icons, redrawn as SVGs since its API can't download its own."""

from __future__ import annotations

from functools import cache

from Qt import QtCore, QtGui, QtSvg

from pipe.core.shotgrid import Status
from pipe.core.util.paths import get_repo_root

SIZE = 16
_FOLDER = get_repo_root() / "resources" / "icon" / "shotgrid"
# Becomes the status's colour.
_TINTED = "currentColor"
_NO_STATUS = "no_status"
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
# Darker colours are raised to this, keeping their hue, to show on the dark window.
_MIN_LIGHTNESS = 120


def icon(status: Status | None) -> QtGui.QIcon:
    if status is None:
        return _icon(_NO_STATUS, _GREY, "")
    color = (
        "#{:02x}{:02x}{:02x}".format(*status.color)
        if status.color
        else _DEFAULTS.get(status.icon or "", _GREY)
    )
    return _icon(status.icon or "", color, status.name[:1])


@cache
def _icon(name: str, color: str, letter: str) -> QtGui.QIcon:
    shade = QtGui.QColor(color)
    if shade.lightness() < _MIN_LIGHTNESS:
        shade = QtGui.QColor.fromHsl(
            shade.hslHue(), shade.hslSaturation(), _MIN_LIGHTNESS
        )
    pixmap = QtGui.QPixmap(SIZE * _SCALE, SIZE * _SCALE)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    svg = _FOLDER / f"{name}.svg"
    if name and svg.is_file():
        tinted = svg.read_text().replace(_TINTED, shade.name())
        QtSvg.QSvgRenderer(QtCore.QByteArray(tinted.encode())).render(painter)
    else:
        _letter(painter, shade, letter)
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return QtGui.QIcon(pixmap)


def _letter(painter: QtGui.QPainter, color: QtGui.QColor, letter: str) -> None:
    """A disc with the status's first letter, for an icon there's no SVG of."""
    painter.scale(_SCALE, _SCALE)
    box = QtCore.QRectF(1.5, 1.5, 13, 13)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(box)
    font = QtGui.QFont()
    font.setPixelSize(10)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QtGui.QColor("#ffffff" if color.lightness() < 160 else "#1c1d20"))
    painter.drawText(box, QtCore.Qt.AlignCenter, letter.upper())
