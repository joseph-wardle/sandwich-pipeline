"""Which departments' publishes the render hip loads, and at which version.

Each row is a row of the render hip's load layers node. Turning one off drops
that department's work from every render layer, such as a CFX publish that
breaks the render; pinning one renders an older version of it.
"""

from __future__ import annotations

from collections.abc import Sequence

from Qt import QtCore, QtWidgets

from pipe.core.publish import CURRENT
from pipe.core.render.network.protocol import DepartmentChoice, DepartmentLayer

_PINNED_STYLE = "QComboBox { color: #e0a050; }"


class DepartmentPanel(QtWidgets.QGroupBox):
    """`changed` fires on every edit; `choices()` is what Save and Send set."""

    changed = QtCore.Signal()

    _rows: dict[str, tuple[QtWidgets.QCheckBox, QtWidgets.QComboBox]]
    _grid: QtWidgets.QGridLayout
    _hint: QtWidgets.QLabel

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Department Layers", parent)
        self.setToolTip(
            "The publishes the render hip loads. Untick one to render without it, "
            "or pick an older version to pin it."
        )
        self._rows = {}
        self._grid = QtWidgets.QGridLayout()
        self._grid.setColumnStretch(1, 1)
        self._hint = QtWidgets.QLabel("Save to see the stage with these layers.")
        self._hint.setEnabled(False)
        self._hint.hide()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self._grid)
        layout.addWidget(self._hint)

    def show_departments(self, departments: Sequence[DepartmentLayer]) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        self._rows = {}
        self._hint.hide()
        for index, department in enumerate(departments):
            box = QtWidgets.QCheckBox(department.label)
            box.setChecked(department.enabled)
            versions = QtWidgets.QComboBox()
            for token, label in department.choices:
                versions.addItem(label, token)
            if (found := versions.findData(department.version)) < 0:
                # A pin the menu no longer lists; Send refuses it until changed.
                versions.addItem(
                    f"{department.version} (not published)", department.version
                )
                found = versions.count() - 1
            versions.setCurrentIndex(found)
            versions.setEnabled(department.enabled)
            _style_pin(versions)
            box.toggled.connect(lambda on, v=versions: self._edited(v, on))
            versions.currentIndexChanged.connect(
                lambda _, v=versions: self._edited(v, v.isEnabled())
            )
            self._grid.addWidget(box, index, 0)
            self._grid.addWidget(versions, index, 1)
            self._rows[department.row] = (box, versions)
        self.setVisible(bool(departments))

    def choices(self) -> dict[str, DepartmentChoice]:
        return {
            row: DepartmentChoice(
                enabled=box.isChecked(), version=str(versions.currentData())
            )
            for row, (box, versions) in self._rows.items()
        }

    def _edited(self, versions: QtWidgets.QComboBox, on: bool) -> None:
        versions.setEnabled(on)
        _style_pin(versions)
        self._hint.show()
        self.changed.emit()


def _style_pin(versions: QtWidgets.QComboBox) -> None:
    pinned = versions.isEnabled() and versions.currentData() != CURRENT
    versions.setStyleSheet(_PINNED_STYLE if pinned else "")
    versions.setToolTip("Pinned to an older version" if pinned else "")
