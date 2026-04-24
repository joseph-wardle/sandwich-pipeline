from typing import Sequence

from Qt.QtCore import Signal
from Qt.QtWidgets import QButtonGroup, QHBoxLayout, QToolButton, QWidget

CHIP_STYLE = """
    QToolButton {
        border: 1px solid #555;
        border-radius: 6px;
        padding: 2px 4px;
        background: #2b2b2b;
        color: #aaa;
        font-size: 10px;
    }
    QToolButton:checked {
        background: #1e4a7a;
        border-color: #5b9bd5;
        color: #8fc4f0;
    }
"""


class ChipBar(QWidget):
    selection_changed = Signal(str)

    def __init__(
        self,
        options: Sequence[str],
        parent=None,
        initial_selection: str | None = None,
    ):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.buttonClicked.connect(
            lambda b: self.selection_changed.emit(b.text())
        )

        for label in options:
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setStyleSheet(CHIP_STYLE)
            self.group.addButton(button)
            layout.addWidget(button)

        # restore the selection or default to first chip
        self.select_chip(initial_selection)
        layout.addStretch()

    def select_chip(self, chip: str | None):
        match = next(
            (b for b in self.group.buttons() if b.text() == chip),
            self.group.buttons()[0],
        )
        match.setChecked(True)
        self.selection_changed.emit(match.text())

    def selected(self) -> str | None:
        b = self.group.checkedButton()
        return b.text() if b else None
