"""The shot's stage as a tree, as Houdini's Scene Graph Tree pane shows it.

It is the stage the Render Network receives: every department's publish, before
any layer prunes or mattes anything. The window shows it beside the parms, and
the prim pickers use it to choose prims.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.protocol import Prim

# Houdini's bookkeeping prim, which no render layer targets.
_HIDDEN_ROOTS = ("/HoudiniLayerInfo",)
_PATH_ROLE = QtCore.Qt.UserRole
_NAME, _TYPE, _KIND, _PURPOSE = range(4)

TypeFilter = Callable[[str], bool]


def is_camera(type_name: str) -> bool:
    return type_name == "Camera"


def is_light(type_name: str) -> bool:
    # UsdLux and RenderMan lights all end so: RectLight, PxrDomeLight, ...
    return type_name.endswith("Light") or type_name == "LightFilter"


class SceneGraphTree(QtWidgets.QTreeWidget):
    """Prims by path. Dragging selected prims carries their paths as text, so
    they drop into any parm field."""

    _items: dict[str, QtWidgets.QTreeWidgetItem]

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._items = {}
        self.setHeaderLabels(["Scene Graph", "Type", "Kind", "Purpose"])
        self.setUniformRowHeights(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def populate(self, prims: Sequence[Prim], only: TypeFilter | None = None) -> None:
        """Show `prims`, or with `only`, the matching prims and those above them."""
        self.clear()
        self._items = {}
        wanted = None
        if only is not None:
            wanted = set()
            for prim in prims:
                if only(prim.type):
                    wanted.update(_ancestors(prim.path))
        for prim in prims:
            if prim.path.startswith(_HIDDEN_ROOTS):
                continue
            if wanted is not None and prim.path not in wanted:
                continue
            parent_path, _, name = prim.path.rpartition("/")
            item = QtWidgets.QTreeWidgetItem([name, prim.type, prim.kind, prim.purpose])
            item.setData(_NAME, _PATH_ROLE, prim.path)
            item.setToolTip(_NAME, prim.path)
            if only is not None and not only(prim.type):
                # Shown for where a match is, but not itself a choice.
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsSelectable)
            if (parent := self._items.get(parent_path)) is None:
                self.addTopLevelItem(item)
            else:
                parent.addChild(item)
            self._items[prim.path] = item
        if only is not None:
            self.expandAll()
        else:
            # Down to each top-level prim's children, as Houdini opens it.
            for index in range(self.topLevelItemCount()):
                self.topLevelItem(index).setExpanded(True)
        self.resizeColumnToContents(_NAME)

    def paths(self) -> list[str]:
        return [item.data(_NAME, _PATH_ROLE) for item in self.selectedItems()]

    def select(self, paths: Sequence[str]) -> None:
        for path in paths:
            if (item := self._items.get(path)) is None:
                continue
            item.setSelected(True)
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.scrollToItem(item)

    def filter(self, text: str) -> int:
        """Show only prims whose path holds `text`, and those above them, and
        return how many match."""
        needle = text.strip().lower()
        shown: set[str] = set()
        matches = 0
        if needle:
            for path in self._items:
                if needle in path.lower():
                    matches += 1
                    shown.update(_ancestors(path))
        for path, item in self._items.items():
            item.setHidden(bool(needle) and path not in shown)
            if needle and path in shown:
                item.setExpanded(True)
        return matches if needle else len(self._items)

    def mimeData(self, items: Iterable[QtWidgets.QTreeWidgetItem]) -> QtCore.QMimeData:  # noqa: N802 (Qt's name)
        data = QtCore.QMimeData()
        data.setText(" ".join(item.data(_NAME, _PATH_ROLE) for item in items))
        return data

    def mimeTypes(self) -> list[str]:  # noqa: N802 (Qt's name)
        return ["text/plain"]

    def _menu(self, position: QtCore.QPoint) -> None:
        paths = self.paths()
        if not paths:
            return
        menu = QtWidgets.QMenu(self)
        label = "Copy Path" if len(paths) == 1 else f"Copy {len(paths)} Paths"
        copy = menu.addAction(label)
        expand = menu.addAction("Expand All Below")
        chosen = menu.exec_(self.viewport().mapToGlobal(position))
        if chosen == copy:
            QtGui.QGuiApplication.clipboard().setText(" ".join(paths))
        elif chosen == expand:
            for item in self.selectedItems():
                _expand_below(item)


class SceneGraphPanel(QtWidgets.QWidget):
    """The tree with a filter, for the window's side pane."""

    _tree: SceneGraphTree
    _filter: QtWidgets.QLineEdit
    _count: QtWidgets.QLabel

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        title = QtWidgets.QLabel("<b>Scene Graph</b>")
        title.setToolTip(
            "The stage the Render Network receives: every department's publish, "
            "before any layer prunes or mattes anything."
        )
        self._count = QtWidgets.QLabel()
        self._count.setEnabled(False)
        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText("Filter by name or path")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        self._tree = SceneGraphTree()
        hint = QtWidgets.QLabel("Drag prims onto a parm, or right-click to copy paths.")
        hint.setWordWrap(True)
        hint.setEnabled(False)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._count)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self._filter)
        layout.addWidget(self._tree, 1)
        layout.addWidget(hint)

    def show_prims(self, prims: Sequence[Prim]) -> None:
        self._tree.populate(prims)
        self._apply_filter(self._filter.text())

    def _apply_filter(self, text: str) -> None:
        count = self._tree.filter(text)
        self._count.setText(f"{count:,} {'matching' if text.strip() else 'prims'}")


class PrimPicker(QtWidgets.QDialog):
    """Choose prims for a parm; `chosen()` is what was selected."""

    _tree: SceneGraphTree

    def __init__(
        self,
        parent: QtWidgets.QWidget | None,
        prims: Sequence[Prim],
        selected: Sequence[str],
        title: str,
        only: TypeFilter | None = None,
        single: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 680)

        search = QtWidgets.QLineEdit()
        search.setPlaceholderText("Filter by name or path")
        search.setClearButtonEnabled(True)
        self._tree = SceneGraphTree()
        self._tree.setDragEnabled(False)
        if single:
            self._tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._tree.populate(prims, only)
        self._tree.select(selected)
        self._tree.itemDoubleClicked.connect(lambda *_: self.accept())
        search.textChanged.connect(self._tree.filter)

        hint = QtWidgets.QLabel(
            "Double-click to choose."
            if single
            else "Ctrl- or Shift-click to choose several."
        )
        hint.setEnabled(False)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(search)
        layout.addWidget(self._tree)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        search.setFocus()

    def chosen(self) -> list[str]:
        return self._tree.paths()


def _ancestors(path: str) -> list[str]:
    """`path` and every prim above it, e.g. /a/b → [/a/b, /a]."""
    found = []
    while path and path != "/":
        found.append(path)
        path = path.rpartition("/")[0]
    return found


def _expand_below(item: QtWidgets.QTreeWidgetItem) -> None:
    item.setExpanded(True)
    for index in range(item.childCount()):
        _expand_below(item.child(index))
