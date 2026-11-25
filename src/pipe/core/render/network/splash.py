"""The Ranch splash, shown while the window reads ShotGrid.

The artwork, a bottle of ranch, has a transparent background, so the splash
has no frame or fill of its own and floats over the desktop. The bottle runs
to the artwork's bottom edge, so what startup is doing shows on a dark pill
just below it, which reads over any desktop. Without a compositor, as over
some SSH X11 forwarding, the transparent parts show black instead.
"""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.util.paths import get_repo_root

IMAGE = get_repo_root() / "resources/splash/ranch_splash.png"
# The artwork's height on screen, in logical pixels; it is drawn from its full size.
_HEIGHT = 520
# Below the artwork: the band the message's pill sits in, and how wide the
# splash is at least, so a long message fits beside the narrow bottle.
_BAND = 40
_MIN_WIDTH = 360
_TEXT = QtGui.QColor("#e4e5e9")
_PILL = QtGui.QColor(22, 23, 26, 225)
_PILL_EDGE = QtGui.QColor("#454851")


class Splash(QtWidgets.QSplashScreen):
    """Call `say` as startup moves on, then `finish(window)`."""

    _message: str

    def __init__(self, pixmap: QtGui.QPixmap) -> None:
        super().__init__(pixmap, QtCore.Qt.WindowStaysOnTopHint)
        self._message = ""
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)

    def say(self, message: str) -> None:
        self._message = message
        self.repaint()
        QtWidgets.QApplication.processEvents()

    def drawContents(self, painter: QtGui.QPainter) -> None:  # noqa: N802 (Qt's name)
        if not self._message:
            return
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        width = metrics.horizontalAdvance(self._message) + 28
        height = metrics.height() + 12
        band = QtCore.QRect(0, self.height() - _BAND, self.width(), _BAND)
        pill = QtCore.QRect(0, 0, min(width, self.width()), height)
        pill.moveCenter(band.center())
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(_PILL_EDGE, 1))
        painter.setBrush(_PILL)
        painter.drawRoundedRect(QtCore.QRectF(pill), height / 2, height / 2)
        painter.setPen(_TEXT)
        painter.drawText(pill, int(QtCore.Qt.AlignCenter), self._message)


def show() -> Splash | None:
    """Show the splash, or None when its image is missing."""
    source = QtGui.QPixmap(str(IMAGE))
    if source.isNull():
        return None
    screen = QtWidgets.QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    art = source.scaledToHeight(int(_HEIGHT * ratio), QtCore.Qt.SmoothTransformation)
    # The artwork on top, centred, with the message's band beneath it.
    pixmap = QtGui.QPixmap(
        max(art.width(), int(_MIN_WIDTH * ratio)), art.height() + int(_BAND * ratio)
    )
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.drawPixmap((pixmap.width() - art.width()) // 2, 0, art)
    painter.end()
    # Sharp on high-density screens.
    pixmap.setDevicePixelRatio(ratio)
    splash = Splash(pixmap)
    splash.show()
    splash.say("Starting…")
    return splash
