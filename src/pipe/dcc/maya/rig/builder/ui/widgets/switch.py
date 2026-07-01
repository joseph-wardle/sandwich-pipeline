from Qt.QtCore import Property, QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from Qt.QtGui import QColor, QPainter, QPainterPath
from Qt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from ..core import blend_color


class SwitchWithLabel(QWidget):
    toggled = Signal(bool)

    def __init__(
        self,
        text: str = "",
        parent=None,
        color_on: QColor | None = None,
        color_off: QColor | None = None,
        color_thumb: QColor | None = None,
    ):
        super().__init__(parent=parent)

        self.switch = Switch(
            self, color_on=color_on, color_off=color_off, color_thumb=color_thumb
        )
        self.label = QLabel(text, self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(self.switch)
        layout.addWidget(self.label)

        self.setLayout(layout)

        self.switch.toggled.connect(self.toggled)

    def isChecked(self):
        return self.switch.isChecked()

    def setChecked(self, value: bool):
        self.switch.setChecked(value)

    def text(self):
        return self.label.text()

    def setText(self, text: str):
        self.label.setText(text)


class Switch(QWidget):
    toggled = Signal(bool)

    _TRACK_HEIGHT = 14
    _THUMB_MARGIN = 2
    _TRACK_RADIUS = 7

    def __init__(
        self,
        parent: QWidget | None = None,
        checked: bool = False,
        color_on: QColor | None = None,
        color_off: QColor | None = None,
        color_thumb: QColor | None = None,
    ):
        super().__init__(parent)
        self._checked = checked
        self._thumb_pos: float = 1.0 if checked else 0.0

        self._color_on = color_on or QColor("#5b9bd5")
        self._color_off = color_off or QColor("#555555")
        self._color_thumb = color_thumb or QColor("#dddddd")

        self._anim = QPropertyAnimation(self, b"thumb_pos", self)
        self._anim.setDuration(96)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        h = self._TRACK_HEIGHT
        return QSize(h * 2, h)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        if self._checked == checked:
            return
        self._checked = checked
        self._animate_to(1.0 if checked else 0.0)
        self.toggled.emit(checked)

    def mousePressEvent(self, event):
        self.setChecked(not self._checked)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        track_h = self._TRACK_HEIGHT
        track_y = (h - track_h) // 2

        # track
        color = blend_color(self._color_off, self._color_on, self._thumb_pos)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        path = QPainterPath()
        path.addRoundedRect(
            0, track_y, w, track_h, self._TRACK_RADIUS, self._TRACK_RADIUS
        )
        painter.drawPath(path)

        # thumb
        m = self._THUMB_MARGIN
        thumb_d = track_h - m * 2
        travel = w - thumb_d - m * 2
        thumb_x = m + self._thumb_pos * travel
        thumb_y = track_y + m

        painter.setBrush(self._color_thumb)
        painter.drawEllipse(int(thumb_x), int(thumb_y), thumb_d, thumb_d)

    def _animate_to(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._thumb_pos)
        self._anim.setEndValue(target)
        self._anim.start()

    def _get_thumb_pos(self) -> float:
        return self._thumb_pos

    def _set_thumb_pos(self, pos: float):
        self._thumb_pos = pos
        self.update()

    thumb_pos = Property(float, _get_thumb_pos, _set_thumb_pos)
