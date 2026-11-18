"""The Sandwich Renderer splash, shown while the window reads ShotGrid.

The artwork has a transparent background, so the splash has no frame or fill
of its own and floats over the desktop. Without a compositor, as over some SSH
X11 forwarding, the transparent parts show black instead.
"""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.util.paths import get_repo_root

IMAGE = get_repo_root() / "resources/splash/sandwich_renderer_splash.png"
# On screen, in logical pixels; the artwork is drawn from its full size.
_SIZE = 520
_TEXT = QtGui.QColor("#ffffff")
_SHADOW = QtGui.QColor(0, 0, 0, 200)


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
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        # Under the title, in the artwork's empty band at the bottom.
        area = self.rect().adjusted(0, 0, 0, -18)
        flags = int(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom)
        painter.setPen(_SHADOW)
        painter.drawText(area.translated(1, 1), flags, self._message)
        painter.setPen(_TEXT)
        painter.drawText(area, flags, self._message)


def show() -> Splash | None:
    """Show the splash, or None when its image is missing."""
    source = QtGui.QPixmap(str(IMAGE))
    if source.isNull():
        return None
    screen = QtWidgets.QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = source.scaled(
        int(_SIZE * ratio),
        int(_SIZE * ratio),
        QtCore.Qt.KeepAspectRatio,
        QtCore.Qt.SmoothTransformation,
    )
    # Sharp on high-density screens.
    pixmap.setDevicePixelRatio(ratio)
    splash = Splash(pixmap)
    splash.show()
    splash.say("Starting…")
    return splash
