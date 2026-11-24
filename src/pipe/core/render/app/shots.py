"""The window's shot list: shots under collapsible sequences, with a filter.

Each shot shows its Render task's ShotGrid status as an icon; hovering the
icon names it, and right-clicking the shot changes it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.app import status_icons
from pipe.core.render.render_status import RenderBoard, RenderStatus
from pipe.core.shotgrid import Shot, Status

_CODE_ROLE = QtCore.Qt.UserRole
# The Render task's status as ShotGrid names it, for the tooltip and the filter.
_STATUS_ROLE = QtCore.Qt.UserRole + 1
_NO_TASK = "No Render task in ShotGrid"
# Which sequences the artist left open, kept between sessions.
_SETTINGS = ("SandwichKwonDo", "RenderNetwork")
_EXPANDED_KEY = "shots/expanded_sequences"


def sequence_of(shot: Shot) -> str:
    """The shot's sequence in ShotGrid, else the letters before its number."""
    if shot.sequence is not None and shot.sequence.code:
        return shot.sequence.code
    return (shot.code or "").split("_")[0]


class ShotList(QtWidgets.QWidget):
    """`chosen` carries a shot's code when the artist picks it;
    `render_status_chosen` a shot's code and the Status picked for its Render
    task, for the window to save to ShotGrid and then `set_render_status`."""

    chosen = QtCore.Signal(str)
    render_status_chosen = QtCore.Signal(str, object)

    _filter: QtWidgets.QLineEdit
    _tree: _ShotTree
    _sequences: dict[str, QtWidgets.QTreeWidgetItem]
    _shots: dict[str, QtWidgets.QTreeWidgetItem]
    _settings: QtCore.QSettings

    def __init__(
        self,
        shots: Sequence[Shot],
        mine: set[str] | None = None,
        render: RenderBoard | None = None,
        may_change: Callable[[str], str] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """`mine` are the codes of the shots the artist lights, shown in bold;
        `render` is each shot's Render task status and the statuses it may be
        given. `may_change` says why the artist can't change a shot's, or ""
        when they can."""
        super().__init__(parent)
        self._mine = mine or set()
        board = render or RenderBoard()
        self._render = dict(board.shots)
        self._choices = board.choices
        self._may_change = may_change or (lambda _code: "")
        self._settings = QtCore.QSettings(*_SETTINGS)
        self._sequences, self._shots = {}, {}

        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText("Filter shots or statuses")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)

        self._tree = _ShotTree()
        self._tree.setHeaderHidden(True)
        self._tree.setIconSize(QtCore.QSize(status_icons.SIZE, status_icons.SIZE))
        self._tree.setUniformRowHeights(True)
        self._tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._menu)
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
            self._show_status(item, self._render.get(shot.code or ""))
            if shot.code in self._mine:
                item.setFont(0, bold)
                item.setToolTip(0, "You light this shot")
            group.addChild(item)
            self._shots[shot.code or ""] = item
        for sequence, group in self._sequences.items():
            group.setText(0, f"{sequence}  ({group.childCount()})")
            group.setExpanded(sequence in expanded)

    def render_status(self, code: str) -> RenderStatus | None:
        return self._render.get(code)

    def set_render_status(self, code: str, render: RenderStatus) -> None:
        """Show `render` as shot `code`'s Render status, as ShotGrid now has it."""
        self._render[code] = render
        if (item := self._shots.get(code)) is not None:
            self._show_status(item, render)
        self._apply_filter(self._filter.text())

    def _show_status(
        self, item: QtWidgets.QTreeWidgetItem, render: RenderStatus | None
    ) -> None:
        item.setIcon(0, status_icons.icon(render.status if render else None))
        item.setData(
            0, _STATUS_ROLE, f"{render.task}: {render.name}" if render else _NO_TASK
        )

    def _menu(self, position: QtCore.QPoint) -> None:
        item = self._tree.itemAt(position)
        code = item.data(0, _CODE_ROLE) if item is not None else None
        if not code:
            return
        menu = QtWidgets.QMenu(self)
        render = self._render.get(code)
        title = menu.addAction(
            f"{code} · {render.task}" if render else f"{code} · {_NO_TASK}"
        )
        title.setEnabled(False)
        refusal = (
            "This shot has no Render task to set." if render is None else ""
        ) or self._may_change(code)
        if refusal:
            menu.addAction(refusal).setEnabled(False)
        menu.addSeparator()
        for status in self._choices:
            action = menu.addAction(status_icons.icon(status), status.name)
            action.setCheckable(True)
            action.setChecked(render is not None and status.code == render.code)
            action.setEnabled(not refusal)
            action.triggered.connect(lambda _=False, s=status: self._chose(code, s))
        menu.exec_(self._tree.viewport().mapToGlobal(position))

    def _chose(self, code: str, status: Status) -> None:
        render = self._render.get(code)
        if render is not None and render.code != status.code:
            self.render_status_chosen.emit(code, status)

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
                status = str(item.data(0, _STATUS_ROLE) or "").lower()
                hidden = (
                    bool(needle)
                    and needle not in item.text(0).lower()
                    and needle not in status
                )
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


class _ShotTree(QtWidgets.QTreeWidget):
    """Over a shot's status icon, the tooltip names its Render status; elsewhere on
    the row it shows the row's own tooltip. Right-clicking a shot leaves the
    open shot as it is."""

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802 (Qt's name)
        # Right-click only opens the status menu: choosing the shot would open
        # it in Houdini, which changing its status doesn't need.
        if event.button() == QtCore.Qt.RightButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def viewportEvent(self, event: QtCore.QEvent) -> bool:  # noqa: N802 (Qt's name)
        if event.type() != QtCore.QEvent.ToolTip:
            return super().viewportEvent(event)
        assert isinstance(event, QtGui.QHelpEvent)
        item = self.itemAt(event.pos())
        status = item.data(0, _STATUS_ROLE) if item is not None else None
        if item is not None and status and self._over_icon(item, event.pos().x()):
            QtWidgets.QToolTip.showText(
                event.globalPos(),
                f"Render status — {status}\nRight-click to change it",
                self.viewport(),
            )
            return True
        return super().viewportEvent(event)

    def _over_icon(self, item: QtWidgets.QTreeWidgetItem, x: int) -> bool:
        # The icon leads the cell; allow the style's few pixels of padding.
        left = self.visualItemRect(item).left()
        return left <= x <= left + self.iconSize().width() + 6


def _as_list(value: object) -> list[str]:
    """QSettings reads a one-item list back as a bare string, and none as None."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]  # ty: ignore[not-iterable]
