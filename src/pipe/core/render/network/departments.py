"""The shot USD layers the render hip loads: each department's publish, and at
which version.

Each row is a row of the render hip's load layers node. Turning one off drops
that department's work from every render layer, such as a CFX publish that
breaks the render; pinning one renders an older version of it.
"""

from __future__ import annotations

from collections.abc import Sequence

from Qt import QtCore, QtWidgets

from pipe.core.publish import CURRENT
from pipe.core.render.network.marks import ChangeDot
from pipe.core.render.network.protocol import DepartmentChoice, DepartmentLayer

TITLE = "Loaded Shot USD Layers"
_PINNED_STYLE = "QComboBox { color: #e0a050; }"
# The row the lighting tools sit under, matched against each row's label.
_LIGHTING = "light"


class DepartmentPanel(QtWidgets.QGroupBox):
    """`changed` fires on every edit; `choices()` is what Save and Send set.

    `lighting_tools`, such as the Publish Lighting button, sit under the
    lighting row, or at the foot of the panel when there is none.
    """

    changed = QtCore.Signal()

    _rows: dict[str, tuple[QtWidgets.QCheckBox, QtWidgets.QComboBox, ChangeDot]]
    _loaded: dict[str, DepartmentChoice]
    _grid: QtWidgets.QGridLayout
    _hint: QtWidgets.QLabel
    _empty: QtWidgets.QLabel
    _tools: QtWidgets.QWidget | None

    def __init__(
        self,
        lighting_tools: QtWidgets.QWidget | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(TITLE, parent)
        self.setToolTip(
            "The publishes the render hip loads. Untick one to render without it, "
            "or pick an older version to pin it."
        )
        self._rows, self._loaded, self._tools = {}, {}, lighting_tools
        self._grid = QtWidgets.QGridLayout()
        self._grid.setColumnStretch(2, 1)
        self._hint = QtWidgets.QLabel("Save to see the stage with these layers.")
        self._hint.setEnabled(False)
        self._hint.hide()
        self._empty = QtWidgets.QLabel("The render hip loads no department layers.")
        self._empty.setEnabled(False)
        self._empty.hide()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._empty)
        layout.addLayout(self._grid)
        layout.addWidget(self._hint)

    def show_departments(self, departments: Sequence[DepartmentLayer]) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            # The lighting tools belong to the window, and move to the new rows.
            if widget is not None and widget is not self._tools:
                widget.deleteLater()
        self._rows, self._loaded = {}, {}
        self._hint.hide()
        row = 0
        lighting_row: int | None = None
        for department in departments:
            dot = ChangeDot()
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
            self._grid.addWidget(dot, row, 0)
            self._grid.addWidget(box, row, 1)
            self._grid.addWidget(versions, row, 2)
            self._rows[department.row] = (box, versions, dot)
            self._loaded[department.row] = DepartmentChoice(
                enabled=department.enabled, version=department.version
            )
            row += 1
            if lighting_row is None and _LIGHTING in department.label.lower():
                lighting_row = row
                # Leave a row under it for the lighting tools.
                row += 1
        if self._tools is not None:
            at = lighting_row if lighting_row is not None else row
            self._grid.addWidget(self._tools, at, 1, 1, 2)
            self._tools.show()
        self._empty.setVisible(not departments)
        self.show()

    def choices(self) -> dict[str, DepartmentChoice]:
        return {
            row: DepartmentChoice(
                enabled=box.isChecked(), version=str(versions.currentData())
            )
            for row, (box, versions, _) in self._rows.items()
        }

    def _edited(self, versions: QtWidgets.QComboBox, on: bool) -> None:
        versions.setEnabled(on)
        _style_pin(versions)
        self._hint.show()
        for row, choice in self.choices().items():
            self._rows[row][2].set_changed(choice != self._loaded[row])
        self.changed.emit()


def _style_pin(versions: QtWidgets.QComboBox) -> None:
    pinned = versions.isEnabled() and versions.currentData() != CURRENT
    versions.setStyleSheet(_PINNED_STYLE if pinned else "")
    versions.setToolTip("Pinned to an older version" if pinned else "")
