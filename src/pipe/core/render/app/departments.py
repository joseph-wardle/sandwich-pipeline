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
from pipe.core.render.app.marks import ChangeDot
from pipe.core.render.protocol import DepartmentLayer

TITLE = "Loaded Shot USD Layers"
_PINNED_STYLE = "QComboBox { color: #e0a050; }"
# The row the lighting tools sit under, matched against each row's label.
_LIGHTING = "light"


class DepartmentPanel(QtWidgets.QGroupBox):
    """`changed` asks Houdini to set a row: (row, enabled, version)."""

    changed = QtCore.Signal(str, bool, str)

    # Each row's (enabled, version), as shown and as last saved.
    _shown: dict[str, tuple[bool, str]]
    _saved: dict[str, tuple[bool, str]]
    _dots: dict[str, ChangeDot]
    _grid: QtWidgets.QGridLayout
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
        self._shown, self._saved, self._dots = {}, {}, {}
        self._tools = lighting_tools
        self._grid = QtWidgets.QGridLayout()
        self._grid.setColumnStretch(2, 1)
        self._empty = QtWidgets.QLabel("The render hip loads no department layers.")
        self._empty.setEnabled(False)
        self._empty.hide()
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._empty)
        layout.addLayout(self._grid)

    def show_departments(self, departments: Sequence[DepartmentLayer]) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            # The lighting tools belong to the window, and move to the new rows.
            if widget is not None and widget is not self._tools:
                widget.deleteLater()
        self._shown, self._dots = {}, {}
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
            box.clicked.connect(
                lambda on, r=department.row, v=versions: self.changed.emit(
                    r, on, str(v.currentData())
                )
            )
            versions.activated.connect(
                lambda index, r=department.row, v=versions: self.changed.emit(
                    r, True, str(v.itemData(index))
                )
            )
            self._grid.addWidget(dot, row, 0)
            self._grid.addWidget(box, row, 1)
            self._grid.addWidget(versions, row, 2)
            self._shown[department.row] = (department.enabled, department.version)
            self._dots[department.row] = dot
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
        self._refresh_dots()
        self.show()

    def mark_saved(self) -> None:
        self._saved = dict(self._shown)
        self._refresh_dots()

    def _refresh_dots(self) -> None:
        for row, dot in self._dots.items():
            dot.set_changed(self._shown[row] != self._saved.get(row, self._shown[row]))


def _style_pin(versions: QtWidgets.QComboBox) -> None:
    pinned = versions.isEnabled() and versions.currentData() != CURRENT
    versions.setStyleSheet(_PINNED_STYLE if pinned else "")
    versions.setToolTip("Pinned to an older version" if pinned else "")
