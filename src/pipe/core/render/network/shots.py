"""The window's shot list: shots under collapsible sequences, with a filter."""

from __future__ import annotations

from collections.abc import Sequence

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.shotgrid import Shot

_CODE_ROLE = QtCore.Qt.UserRole
# Which sequences the artist left open, kept between sessions.
_SETTINGS = ("SandwichKwonDo", "RenderNetwork")
_EXPANDED_KEY = "shots/expanded_sequences"


def sequence_of(shot: Shot) -> str:
    """The shot's sequence in ShotGrid, else the letters before its number."""
    if shot.sequence is not None and shot.sequence.code:
        return shot.sequence.code
    return (shot.code or "").split("_")[0]


class ShotList(QtWidgets.QWidget):
    """`chosen` carries a shot's code when the artist picks it."""

    chosen = QtCore.Signal(str)

    _filter: QtWidgets.QLineEdit
    _tree: QtWidgets.QTreeWidget
    _sequences: dict[str, QtWidgets.QTreeWidgetItem]
    _shots: dict[str, QtWidgets.QTreeWidgetItem]
    _settings: QtCore.QSettings

    def __init__(
        self,
        shots: Sequence[Shot],
        mine: set[str] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """`mine` are the codes of the shots the artist lights, shown in bold."""
        super().__init__(parent)
        self._mine = mine or set()
        self._settings = QtCore.QSettings(*_SETTINGS)
        self._sequences, self._shots = {}, {}

        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText("Filter shots")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._fill(shots)
        self._tree.currentItemChanged.connect(self._current_changed)
        self._tree.itemExpanded.connect(self._remember_expanded)
        self._tree.itemCollapsed.connect(self._remember_expanded)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._filter)
        layout.addWidget(self._tree)

    def select(self, code: str | None) -> None:
        """Show `code` as the current shot, without choosing it again."""
        item = self._shots.get(code or "")
        # Qt.py has no QSignalBlocker.
        self._tree.blockSignals(True)
        try:
            if item is None:
                self._tree.clearSelection()
            else:
                self._tree.setCurrentItem(item)
                item.parent().setExpanded(True)
                self._tree.scrollToItem(item)
        finally:
            self._tree.blockSignals(False)

    def _fill(self, shots: Sequence[Shot]) -> None:
        expanded = set(_as_list(self._settings.value(_EXPANDED_KEY, [])))
        bold = QtGui.QFont(self._tree.font())
        bold.setBold(True)
        for shot in shots:
            sequence = sequence_of(shot)
            if (group := self._sequences.get(sequence)) is None:
                group = QtWidgets.QTreeWidgetItem([sequence])
                group.setFont(0, bold)
                # Opens and closes; only shots are chosen.
                group.setFlags(QtCore.Qt.ItemIsEnabled)
                self._tree.addTopLevelItem(group)
                self._sequences[sequence] = group
            item = QtWidgets.QTreeWidgetItem([shot.code or ""])
            item.setData(0, _CODE_ROLE, shot.code)
            if shot.code in self._mine:
                item.setFont(0, bold)
                item.setToolTip(0, "You light this shot")
            group.addChild(item)
            self._shots[shot.code or ""] = item
        for sequence, group in self._sequences.items():
            group.setText(0, f"{sequence}  ({group.childCount()})")
            group.setExpanded(sequence in expanded)

    def _current_changed(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        _previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        code = current.data(0, _CODE_ROLE) if current is not None else None
        if code:
            self.chosen.emit(code)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for group in self._sequences.values():
            shown = 0
            for index in range(group.childCount()):
                item = group.child(index)
                hidden = bool(needle) and needle not in item.text(0).lower()
                item.setHidden(hidden)
                shown += not hidden
            group.setHidden(shown == 0)
        if needle:
            self._tree.expandAll()
        else:
            # Back to what the artist had open.
            expanded = set(_as_list(self._settings.value(_EXPANDED_KEY, [])))
            for sequence, group in self._sequences.items():
                group.setExpanded(sequence in expanded)
            if (current := self._tree.currentItem()) is not None and current.parent():
                current.parent().setExpanded(True)

    def _remember_expanded(self, _item: QtWidgets.QTreeWidgetItem) -> None:
        # Opening every match while filtering isn't the artist's choice.
        if self._filter.text().strip():
            return
        expanded = [s for s, group in self._sequences.items() if group.isExpanded()]
        self._settings.setValue(_EXPANDED_KEY, expanded)


def _as_list(value: object) -> list[str]:
    """QSettings reads a one-item list back as a bare string, and none as None."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]  # ty: ignore[not-iterable]
