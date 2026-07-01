"""The dashed `+ add alternate` button shown under each shot column."""

from __future__ import annotations

from Qt import QtCore, QtGui
from Qt.QtWidgets import QPushButton, QWidget

# Below this width the label collapses to just `+`, so it doesn't get clipped
_COMPACT_WIDTH = 110


class AddAltButton(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("+  add alternate", parent)
        self.setObjectName("addAlt")
        self.setToolTip("Add alternate camera")

    def minimumSizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1, super().minimumSizeHint().height())

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(1, super().sizeHint().height())

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.setText("+  add alternate" if self.width() >= _COMPACT_WIDTH else "+")
