"""The yellow dot beside a parm or layer the artist changed since it was saved."""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

COLOR = "#f0c43c"
TIP = "Changed since the render hip was saved"
_SIZE = 12


class ChangeDot(QtWidgets.QLabel):
    """Always as wide as a dot, so marking a row never shifts its fields."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_SIZE)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setStyleSheet(f"QLabel {{ color: {COLOR}; }}")
        self.set_changed(False)

    def set_changed(self, changed: bool) -> None:
        self.setText("●" if changed else "")
        self.setToolTip(TIP if changed else "")


def dot_icon() -> QtGui.QIcon:
    """The dot as an icon, for a tab whose parms changed."""
    pixmap = QtGui.QPixmap(_SIZE, _SIZE)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(COLOR))
    painter.drawEllipse(2, 2, _SIZE - 4, _SIZE - 4)
    painter.end()
    return QtGui.QIcon(pixmap)
