"""The Cache Send dialog: how the job is named and run, and which caches go first."""

from __future__ import annotations

from dataclasses import dataclass

import hou
from Qt import QtCore, QtWidgets

from pipe.core.ui.dialogs import DialogButtons
from pipe.dcc.houdini.tractor import job
from pipe.dcc.houdini.tractor.caches import LOAD_FROM_DISK

_WIDTH = 420


@dataclass(frozen=True)
class CacheChoice:
    title: str
    priority: int
    service: str
    # Written before the sent cache, upstream first.
    first: list[hou.Node]


def prompt(
    parent: QtWidgets.QWidget | None,
    node: hou.Node,
    upstream: list[hou.Node],
    title: str,
    frames: str,
) -> CacheChoice | None:
    """Open the dialog. None means send nothing."""
    dialog = _CacheDialog(parent, node, upstream, title, frames)
    if not dialog.exec_():
        return None
    return dialog.choice()


class _CacheDialog(QtWidgets.QDialog, DialogButtons):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        node: hou.Node,
        upstream: list[hou.Node],
        title: str,
        frames: str,
    ) -> None:
        super().__init__(parent)
        self._init_buttons(True, "Send", "Cancel")
        self.setWindowTitle(f"Save {node.name()} on Tractor")
        self.setMinimumWidth(_WIDTH)
        self._default_title = title
        self._upstream = upstream

        self._title = QtWidgets.QLineEdit()
        self._title.setPlaceholderText(title)

        self._priority = QtWidgets.QSpinBox()
        self._priority.setRange(*job.PRIORITY_RANGE)
        self._priority.setValue(job.DIALOG_PRIORITY)

        self._blades = QtWidgets.QComboBox()
        self._blades.addItems(list(job.BLADES))

        form = QtWidgets.QFormLayout()
        form.addRow("Title", self._title)
        form.addRow("Priority", self._priority)
        form.addRow("Blades", self._blades)
        form.addRow("Frames", QtWidgets.QLabel(frames))

        # Checked unless it already loads from disk, which means it was cached.
        self._first = QtWidgets.QListWidget()
        for cache in upstream:
            item = QtWidgets.QListWidgetItem(cache.path())
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            loads = cache.evalParm(LOAD_FROM_DISK)
            item.setCheckState(QtCore.Qt.Unchecked if loads else QtCore.Qt.Checked)
            self._first.addItem(item)
        if upstream:
            form.addRow("Cache first", self._first)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

    def choice(self) -> CacheChoice:
        checked = [
            cache
            for row, cache in enumerate(self._upstream)
            if self._first.item(row).checkState() == QtCore.Qt.Checked
        ]
        return CacheChoice(
            title=self._title.text().strip() or self._default_title,
            priority=self._priority.value(),
            service=job.BLADES[self._blades.currentText()],
            first=checked,
        )
