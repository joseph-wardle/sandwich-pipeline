from __future__ import annotations

from typing import Iterable

from Qt import QtCore
from Qt.QtCore import Qt
from Qt.QtGui import QPainter, QStandardItem, QStandardItemModel
from Qt.QtWidgets import QHBoxLayout, QListView, QStyledItemDelegate, QWidget

from ..styling import LOCAL_OVERRIDE_COLOR

OVERRIDE_ROLE = QtCore.Qt.UserRole + 1


class RigItemDelegate(QStyledItemDelegate):
    DOT_SIZE = 6
    DOT_COLOR = LOCAL_OVERRIDE_COLOR

    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter: QPainter, option, index):
        super().paint(painter, option, index)
        if not index.data(OVERRIDE_ROLE):
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(self.DOT_COLOR)
        painter.setPen(Qt.NoPen)

        d = self.DOT_SIZE
        x = option.rect.right() - d - 3
        y = option.rect.center().y() - d // 2
        painter.drawEllipse(x, y, d, d)

        painter.restore()


class RigItem(QStandardItem):
    def __init__(
        self, name: str, display_name: str | None = None, use_display_name: bool = False
    ):
        super().__init__(
            display_name if use_display_name and display_name is not None else name
        )
        self.setEditable(False)
        self.setSelectable(True)
        self.setData(name, QtCore.Qt.UserRole)

    def set_override(self, state: bool):
        self.setData(state, OVERRIDE_ROLE)


class RigSelectList(QListView):
    def __init__(self):
        super().__init__()
        self.item_model = QStandardItemModel(self)
        self.setModel(self.item_model)
        self.setSelectionMode(QListView.SingleSelection)

        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setSpacing(2)
        self._local_override_rigs: set[str] = set()
        self.setItemDelegate(RigItemDelegate(self))

    def add_item(self, name: str, display_name: str | None = None):
        item = RigItem(name, display_name)
        item.set_override(name in self._local_override_rigs)
        self.item_model.appendRow(item)

    def get_all_rig_names(self) -> list[str]:
        model = self.item_model
        return [
            model.item(row).data(QtCore.Qt.UserRole) for row in range(model.rowCount())
        ]

    @property
    def local_override_rigs(self) -> set[str]:
        return self._local_override_rigs

    def set_override_rigs(self, rigs: Iterable[str]):
        self._local_override_rigs = set(rigs)
        model = self.item_model
        for row in range(model.rowCount()):
            item = model.item(row)
            name = item.data(QtCore.Qt.UserRole)
            item.setData(name in self._local_override_rigs, OVERRIDE_ROLE)


class RigSelect(QWidget):
    rig_changed = QtCore.Signal(str)
    variant_changed = QtCore.Signal(str)

    def __init__(self, name: str, parent: QWidget | None = None):
        super().__init__(parent=parent)
        self.name = name
        self.setup_ui()
        self._supress_signals: bool = False
        pass

    def setup_ui(self):
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(4)
        self.setMinimumSize(32, 28)
        self.setLayout(self.main_layout)

        self.rig_panel = RigSelectList()
        self.rig_panel.selectionModel().currentChanged.connect(self._on_rig_changed)
        self.main_layout.addWidget(self.rig_panel)

        self.variant_panel = RigSelectList()
        self.main_layout.addWidget(self.variant_panel)
        self.variant_panel.selectionModel().currentChanged.connect(
            self._on_variant_changed
        )
        pass

    def _on_rig_changed(self, current, previous):
        if self._suppress_signals:
            return
        rig = current.data(QtCore.Qt.UserRole)
        if rig:
            self.rig_changed.emit(rig)

    def _on_variant_changed(self, current, previous):
        if self._suppress_signals:
            return
        variant = current.data(QtCore.Qt.UserRole)
        if variant:
            self.variant_changed.emit(variant)

    def _select_in_panel_by_name(self, panel: RigSelectList, value: str) -> bool:
        model = panel.item_model

        for row in range(model.rowCount()):
            index = model.index(row, 0)
            if index.data(QtCore.Qt.UserRole) == value:
                panel.setCurrentIndex(index)
                panel.scrollTo(index, QListView.PositionAtCenter)
                return True

        return False

    def populate_rigs(self, rigs: list[tuple[str, str]]):
        for rig_name, rig_display_name in rigs:
            self.rig_panel.add_item(rig_name, rig_display_name)
        self.select_first_item(self.rig_panel)

    def populate_variants(self, variants: list[str]):
        for variant in variants:
            self.variant_panel.add_item(variant)
        self.select_first_item(self.variant_panel)

    def select_rig(self, rig: str) -> bool:
        self._suppress_signals = True
        found = self._select_in_panel_by_name(self.rig_panel, rig)
        self._suppress_signals = False
        return found

    def select_variant(self, variant: str) -> bool:
        self._suppress_signals = True
        found = self._select_in_panel_by_name(self.variant_panel, variant)
        self._suppress_signals = False
        return found

    def select_first_item(self, panel: RigSelectList):
        if panel.item_model.rowCount() > 0:
            first_index = panel.item_model.index(0, 0)
            self._suppress_signals = True
            panel.setCurrentIndex(first_index)
            self._suppress_signals = False
            panel.scrollTo(first_index, QListView.PositionAtCenter)

    def set_override_rigs(self, rigs: Iterable[str]):
        self.rig_panel.set_override_rigs(rigs)

    def get_selected_rig(self) -> str | None:
        index = self.rig_panel.currentIndex()
        if not index.isValid():
            return None
        return index.data(QtCore.Qt.UserRole)

    def get_rig_type(self) -> str:
        return self.name

    def get_all_rig_names(self) -> list[str]:
        return self.rig_panel.get_all_rig_names()
