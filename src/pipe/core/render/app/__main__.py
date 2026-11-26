"""Open the Rancher window, optionally on one shot.

Needs a Python with a Qt binding, which the pipeline venv lacks, so the
desktop launcher runs it in mayapy:

    PYTHONPATH=src /usr/autodesk/maya2026/bin/mayapy -m pipe.core.render.app [A_080]
"""

from __future__ import annotations

import argparse
import logging
import sys

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.lighters import current_user, is_admin, lit_shots
from pipe.core.render.app import theme
from pipe.core.render.app.widgets import TITLE
from pipe.core.render.app.window import RenderNetworkWindow
from pipe.core.render import render_status
from pipe.core.render.render_status import RenderBoard
from pipe.core.shotgrid import Shot, ShotGrid, is_previs_shot_code
from pipe.core.ui import MessageDialog
from pipe.core.util.paths import get_repo_root

log = logging.getLogger(__name__)

ICON = get_repo_root() / "resources/icon/rancher.png"
_SPLASH = get_repo_root() / "resources/splash/rancher_splash.png"
# The artwork's height on screen, in logical pixels; it is drawn from its full size.
_SPLASH_HEIGHT = 520
# Below the artwork: the band the message's pill sits in, and how wide the
# splash is at least, so a long message fits beside the narrow bottle.
_SPLASH_BAND = 40
_SPLASH_MIN_WIDTH = 360
_SPLASH_TEXT = QtGui.QColor("#e4e5e9")
_SPLASH_PILL = QtGui.QColor(22, 23, 26, 225)
_SPLASH_PILL_EDGE = QtGui.QColor("#454851")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Edit and send a shot's render layers."
    )
    parser.add_argument("shot", nargs="?", help="open this shot, e.g. A_080")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    # The taskbar matches the window to its desktop launcher by these.
    app.setApplicationName(TITLE)
    app.setDesktopFileName("rancher")
    app.setWindowIcon(QtGui.QIcon(str(ICON)))
    theme.apply(app)
    shown = _show_splash()

    def say(message: str) -> None:
        if shown is not None:
            shown.say(message)

    say("Reading shots from ShotGrid…")
    try:
        shots = _load_shots()
    except _StartupError as exc:
        if shown is not None:
            shown.close()
        MessageDialog(None, str(exc), TITLE).exec_()
        sys.exit(1)

    say("Finding the shots you light…")
    lit = _load_lit_shots()
    say("Reading Render statuses…")
    render = _load_render_statuses()
    say(f"Opening {TITLE}…")
    window = RenderNetworkWindow(shots, args.shot, lit, is_admin(), render)
    window.show()
    if shown is not None:
        shown.finish(window)
    sys.exit(app.exec_())


class _StartupError(Exception):
    """Why the window cannot open, worded for the artist."""


def _load_shots() -> list[Shot]:
    """Every active shot in ShotGrid, previs aside."""
    # env_sg holds gitignored production credentials; import lazily so
    # importing this module never requires them.
    from env_sg import DB_Config

    try:
        shots = ShotGrid.connect(DB_Config).find_shots()
    # shotgun_api3 raises its own untyped errors while connecting and reading.
    except Exception as exc:
        log.exception("Could not read shots from ShotGrid")
        raise _StartupError(
            "Could not reach ShotGrid. Check your network connection and try again."
        ) from exc
    shots = [s for s in shots if s.code and not is_previs_shot_code(s.code)]
    if not shots:
        raise _StartupError("ShotGrid lists no shots for this project.")
    return sorted(shots, key=lambda shot: shot.code or "")


def _load_lit_shots() -> set[str] | None:
    """The codes of the shots the artist lights, or None when ShotGrid can't say."""
    from env_sg import DB_Config

    try:
        conn = ShotGrid.connect(DB_Config)
        return {shot.code or "" for shot in lit_shots(conn, current_user(conn))}
    # Only Publish Lighting needs it, so the window opens without it.
    except Exception:
        log.exception("Could not read which shots this artist lights")
        return None


def _load_render_statuses() -> RenderBoard:
    """Each shot's Render task status, or none when ShotGrid can't say."""
    from env_sg import DB_Config

    try:
        return render_status.load(ShotGrid.connect(DB_Config))
    # Only the shot list's icons need them, so the window opens without them.
    except Exception:
        log.exception("Could not read the shots' Render statuses")
        return RenderBoard()


class _Splash(QtWidgets.QSplashScreen):
    """Floats over the desktop while the window reads ShotGrid.

    The artwork, a bottle of ranch, has a transparent background and runs to
    its bottom edge, so what startup is doing shows on a dark pill just below
    it, which reads over any desktop. Without a compositor, as over some SSH
    X11 forwarding, the transparent parts show black.
    """

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
        band = QtCore.QRect(0, self.height() - _SPLASH_BAND, self.width(), _SPLASH_BAND)
        pill = QtCore.QRect(0, 0, min(width, self.width()), height)
        pill.moveCenter(band.center())
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(_SPLASH_PILL_EDGE, 1))
        painter.setBrush(_SPLASH_PILL)
        painter.drawRoundedRect(QtCore.QRectF(pill), height / 2, height / 2)
        painter.setPen(_SPLASH_TEXT)
        painter.drawText(pill, int(QtCore.Qt.AlignCenter), self._message)


def _show_splash() -> _Splash | None:
    """Show the splash, or None when its image is missing."""
    source = QtGui.QPixmap(str(_SPLASH))
    if source.isNull():
        return None
    screen = QtWidgets.QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    art = source.scaledToHeight(
        int(_SPLASH_HEIGHT * ratio), QtCore.Qt.SmoothTransformation
    )
    # The artwork on top, centred, with the message's band beneath it.
    pixmap = QtGui.QPixmap(
        max(art.width(), int(_SPLASH_MIN_WIDTH * ratio)),
        art.height() + int(_SPLASH_BAND * ratio),
    )
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.drawPixmap((pixmap.width() - art.width()) // 2, 0, art)
    painter.end()
    # Sharp on high-density screens.
    pixmap.setDevicePixelRatio(ratio)
    splash = _Splash(pixmap)
    splash.show()
    splash.say("Starting…")
    return splash


if __name__ == "__main__":
    main()
