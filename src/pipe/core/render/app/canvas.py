"""An image to zoom and pan, with Nuke's Viewer controls.

    Scroll                     zoom about the cursor
    Alt + left drag            pan          (or middle drag)
    Alt + middle drag          zoom about where the drag began
    + / -                      zoom about the centre
    F                          fit the image to the view

Past 100% the pixels stay square, so single pixels can be inspected.
"""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

_BACKGROUND = QtGui.QColor(32, 32, 32)
_MIN_SCALE, _MAX_SCALE = 0.02, 64.0
# Each wheel notch, + or - press zooms this much; Nuke's step is about the same.
_STEP = 1.25
# Zoom per pixel of Alt + middle drag.
_DRAG_ZOOM = 1.01
_WHEEL_NOTCH = 120


class ImageCanvas(QtWidgets.QWidget):
    """`hovered` carries the image pixel under the cursor, which may lie off the
    image; `zoomed` the scale, where 1.0 shows one image pixel per screen pixel."""

    hovered = QtCore.Signal(int, int)
    zoomed = QtCore.Signal(float)

    _image: QtGui.QImage | None
    _scale: float
    # Where the image's top-left corner sits in the widget.
    _offset: QtCore.QPointF
    # Fitted views re-fit as the widget resizes, until the artist zooms or pans.
    _fitted: bool
    _drag: str | None
    _last: QtCore.QPointF
    _anchor: QtCore.QPointF

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._image, self._drag = None, None
        self._scale, self._fitted = 1.0, True
        self._offset = QtCore.QPointF()
        self._last = QtCore.QPointF()
        self._anchor = QtCore.QPointF()
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMinimumSize(200, 150)
        self.setToolTip(
            "Scroll or Alt + middle-drag to zoom, Alt + drag or middle-drag to "
            "pan, F to fit, + and - to zoom."
        )

    def set_image(self, image: QtGui.QImage) -> None:
        """Show `image`, keeping the zoom and pan unless its size changed, so
        frames and AOVs compare at the same spot."""
        resized = self._image is None or self._image.size() != image.size()
        self._image = image
        if resized or self._fitted:
            self.fit()
        self.update()

    def clear(self) -> None:
        self._image = None
        self.update()

    def fit(self) -> None:
        if self._image is None or self._image.isNull():
            return
        scale = min(
            self.width() / self._image.width(), self.height() / self._image.height()
        )
        self._set_scale(scale)
        self._offset = QtCore.QPointF(
            (self.width() - self._image.width() * self._scale) / 2,
            (self.height() - self._image.height() * self._scale) / 2,
        )
        self._fitted = True
        self.update()

    def actual_size(self) -> None:
        """One image pixel per screen pixel, about the centre."""
        self._zoom_about(self._centre(), 1.0 / self._scale)

    # ---- painting ------------------------------------------------------

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 (Qt's name)
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), _BACKGROUND)
        if self._image is not None:
            # Smooth when shrinking; square pixels when enlarging, as in Nuke.
            painter.setRenderHint(
                QtGui.QPainter.SmoothPixmapTransform, self._scale < 1.0
            )
            painter.translate(self._offset)
            painter.scale(self._scale, self._scale)
            painter.drawImage(QtCore.QPointF(0, 0), self._image)
        painter.end()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802 (Qt's name)
        super().resizeEvent(event)
        if self._fitted:
            self.fit()

    # ---- mouse and keys ------------------------------------------------

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:  # noqa: N802 (Qt's name)
        notches = event.angleDelta().y() / _WHEEL_NOTCH
        if notches:
            self._zoom_about(event_position(event), _STEP**notches)
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802 (Qt's name)
        alt = bool(event.modifiers() & QtCore.Qt.AltModifier)
        button = event.button()
        if button == QtCore.Qt.MiddleButton and alt:
            self._drag = "zoom"
        elif button == QtCore.Qt.MiddleButton or (
            button == QtCore.Qt.LeftButton and alt
        ):
            self._drag = "pan"
        else:
            super().mousePressEvent(event)
            return
        self._last = self._anchor = event_position(event)
        self.setCursor(
            QtCore.Qt.ClosedHandCursor
            if self._drag == "pan"
            else QtCore.Qt.SizeHorCursor
        )
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802 (Qt's name)
        position = event_position(event)
        if self._drag == "pan":
            self._offset += position - self._last
            self._fitted = False
            self.update()
        elif self._drag == "zoom":
            moved = position.x() - self._last.x()
            self._zoom_about(self._anchor, _DRAG_ZOOM**moved)
        self._last = position
        image_point = (position - self._offset) / self._scale
        self.hovered.emit(int(image_point.x() // 1), int(image_point.y() // 1))

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802 (Qt's name)
        if self._drag is not None:
            self._drag = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802 (Qt's name)
        key = event.key()
        if key == QtCore.Qt.Key_F:
            self.fit()
        elif key in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self._zoom_about(self._centre(), _STEP)
        elif key == QtCore.Qt.Key_Minus:
            self._zoom_about(self._centre(), 1 / _STEP)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def enterEvent(self, event: QtCore.QEvent) -> None:  # noqa: N802 (Qt's name)
        # Keys reach the view the mouse is over, as in Nuke.
        self.setFocus()
        super().enterEvent(event)

    # ---- helpers -------------------------------------------------------

    def _zoom_about(self, point: QtCore.QPointF, factor: float) -> None:
        """Zoom by `factor`, keeping the image pixel under `point` under it."""
        if self._image is None:
            return
        old = self._scale
        self._set_scale(old * factor)
        self._offset = point - (point - self._offset) * (self._scale / old)
        self._fitted = False
        self.update()

    def _set_scale(self, scale: float) -> None:
        self._scale = min(max(scale, _MIN_SCALE), _MAX_SCALE)
        self.zoomed.emit(self._scale)

    def _centre(self) -> QtCore.QPointF:
        return QtCore.QPointF(self.width() / 2, self.height() / 2)


def event_position(event: QtGui.QMouseEvent | QtGui.QWheelEvent) -> QtCore.QPointF:
    """Where the event happened in the widget. Qt 6 names it position(), and
    its wheel events have no pos(); Qt 5, which the stubs describe, only pos()."""
    position = getattr(event, "position", None)
    if position is not None:
        return QtCore.QPointF(position())
    return QtCore.QPointF(event.pos())
