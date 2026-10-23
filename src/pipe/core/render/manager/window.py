"""Three panes: the lighter's shots, a shot's render layers, a layer's versions."""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.manager.disk import (
    DeleteFailure,
    RenderLayer,
    RenderVersion,
    ShotRenders,
    delete_version,
    find_renders,
    render_root,
)
from pipe.core.shotgrid import Shot
from pipe.core.ui import MessageDialog, MessageDialogCustomButtons, progress_scope
from pipe.core.util.paths import get_repo_root

_DATA_ROLE = QtCore.Qt.UserRole
_VERSION_COLUMN, _FRAMES_COLUMN, _MODIFIED_COLUMN = range(3)
_DELETE_STEP = "Deleting renders"
# A failure dialog that lists hundreds of paths is not read; the log has them all.
_MAX_LISTED_FAILURES = 8
_IN_COMP_ICON = get_repo_root() / "resources/icon/nuke.png"
_ICON_SIZE = 16
# Every shot's code in a confirmation is a wall of text nobody reads.
_MAX_LISTED_SHOTS = 6


class RenderManagerWindow(QtWidgets.QWidget):
    _shot_list: QtWidgets.QListWidget
    _layer_list: QtWidgets.QListWidget
    _version_tree: QtWidgets.QTreeWidget
    _remove_button: QtWidgets.QPushButton
    _clean_button: QtWidgets.QPushButton
    _clean_all_button: QtWidgets.QPushButton
    _status: QtWidgets.QLabel
    _renders: ShotRenders | None

    def __init__(self, shots: Sequence[Shot], user_name: str) -> None:
        super().__init__()
        self._renders = None
        self.setWindowTitle(f"Render Manager — {user_name}")
        self.resize(1000, 560)

        self._shot_list = QtWidgets.QListWidget()
        # Several shots at once, for Delete All Unused.
        self._shot_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        for shot in shots:
            item = QtWidgets.QListWidgetItem(shot.code or "")
            item.setData(_DATA_ROLE, shot)
            self._shot_list.addItem(item)

        self._layer_list = QtWidgets.QListWidget()

        self._version_tree = QtWidgets.QTreeWidget()
        self._version_tree.setHeaderLabels(["Version", "Frames", "Modified"])
        self._version_tree.setRootIsDecorated(False)
        self._version_tree.setIconSize(QtCore.QSize(_ICON_SIZE, _ICON_SIZE))

        self._remove_button = QtWidgets.QPushButton("Remove All Selected")
        self._remove_button.setToolTip(
            "Delete the checked versions of this render layer."
        )
        self._clean_button = QtWidgets.QPushButton("Delete All Unused")
        self._clean_button.setToolTip(
            "In every selected shot, delete the versions its comp script does not "
            "read. Each layer's latest version, and FINAL layers, are kept."
        )
        self._clean_all_button = QtWidgets.QPushButton("Delete All Unused (All Shots)")
        self._clean_all_button.setToolTip(
            "Delete All Unused for every shot in the list, selected or not."
        )
        self._clean_all_button.setEnabled(bool(shots))
        self._status = QtWidgets.QLabel()

        self._build_layout()

        self._shot_list.currentItemChanged.connect(self._show_layers)
        self._shot_list.itemSelectionChanged.connect(self._refresh_buttons)
        self._layer_list.currentItemChanged.connect(self._show_versions)
        self._version_tree.itemChanged.connect(self._refresh_buttons)
        self._remove_button.clicked.connect(self._remove_checked)
        self._clean_button.clicked.connect(
            lambda: self._delete_all_unused(self._selected_shots())
        )
        self._clean_all_button.clicked.connect(
            lambda: self._delete_all_unused(self._all_shots())
        )

        if self._shot_list.count():
            self._shot_list.setCurrentRow(0)
        self._refresh_buttons()

    def _build_layout(self) -> None:
        splitter = QtWidgets.QSplitter()
        for title, widget in (
            ("Shots", self._shot_list),
            ("Render Layers", self._layer_list),
            ("Versions", self._version_tree),
        ):
            pane = QtWidgets.QWidget()
            pane_layout = QtWidgets.QVBoxLayout(pane)
            pane_layout.setContentsMargins(0, 0, 0, 0)
            pane_layout.addWidget(QtWidgets.QLabel(f"<b>{title}</b>"))
            pane_layout.addWidget(widget)
            splitter.addWidget(pane)
        splitter.setSizes([200, 300, 500])

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self._status, stretch=1)
        buttons.addWidget(self._clean_all_button)
        buttons.addWidget(self._clean_button)
        buttons.addWidget(self._remove_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(splitter, stretch=1)
        layout.addLayout(buttons)

    # ---- panes -------------------------------------------------------------

    def _current_shot(self) -> Shot | None:
        item = self._shot_list.currentItem()
        return item.data(_DATA_ROLE) if item else None

    def _show_layers(self, *_: object, keep_layer: str | None = None) -> None:
        """Reads the disk. Selects `keep_layer` again if it still has versions."""
        self._layer_list.clear()
        shot = self._current_shot()
        self._renders = find_renders(shot) if shot else None
        if shot is None or self._renders is None:
            return
        self._status.setText(
            ""
            if self._renders.comp_read
            else f"No comp script at {self._renders.comp_script}, so no version "
            "shows as used and only the latest are kept."
        )
        layers = self._renders.layers
        if not layers:
            _add_placeholder(self._layer_list, f"No renders in {render_root(shot)}")
            return
        for layer in layers:
            self._layer_list.addItem(_layer_item(layer))
        # FINAL rows cannot be selected, so the first row may not be selectable.
        selectable = [layer.name for layer in layers if not layer.is_final]
        if selectable:
            name = keep_layer if keep_layer in selectable else selectable[0]
            self._layer_list.setCurrentRow([layer.name for layer in layers].index(name))

    def _show_versions(self, *_: object) -> None:
        # Filling the tree emits itemChanged once per checkbox.
        self._version_tree.blockSignals(True)
        self._version_tree.clear()
        item = self._layer_list.currentItem()
        layer: RenderLayer | None = item.data(_DATA_ROLE) if item else None
        script = self._renders.comp_script.name if self._renders else ""
        for version in layer.versions if layer else []:
            self._version_tree.addTopLevelItem(_version_item(version, script))
        for column in (_VERSION_COLUMN, _FRAMES_COLUMN, _MODIFIED_COLUMN):
            self._version_tree.resizeColumnToContents(column)
        self._version_tree.blockSignals(False)
        self._refresh_buttons()

    def _checked_versions(self) -> list[RenderVersion]:
        items = (
            self._version_tree.topLevelItem(row)
            for row in range(self._version_tree.topLevelItemCount())
        )
        return [
            item.data(_VERSION_COLUMN, _DATA_ROLE)
            for item in items
            if item.checkState(_VERSION_COLUMN) == QtCore.Qt.Checked
        ]

    def _all_shots(self) -> list[Shot]:
        return [
            self._shot_list.item(row).data(_DATA_ROLE)
            for row in range(self._shot_list.count())
        ]

    def _selected_shots(self) -> list[Shot]:
        return [item.data(_DATA_ROLE) for item in self._shot_list.selectedItems()]

    def _refresh_buttons(self) -> None:
        self._remove_button.setEnabled(bool(self._checked_versions()))
        self._clean_button.setEnabled(bool(self._selected_shots()))

    # ---- actions -----------------------------------------------------------

    def _remove_checked(self) -> None:
        checked = self._checked_versions()
        if not checked:
            return
        layer_item = self._layer_list.currentItem()
        layer: RenderLayer = layer_item.data(_DATA_ROLE)
        names = ", ".join(version.name for version in checked)
        message = (
            f"Delete {_plural(len(checked), 'version')} of {layer.name}?\n\n{names}"
        )
        if any(version.is_latest for version in checked):
            message += "\n\nThis includes the latest version."
        if in_comp := [version.name for version in checked if version.in_comp]:
            script = self._renders.comp_script.name if self._renders else "the comp"
            message += (
                f"\n\n{script} reads {', '.join(in_comp)}. Those Read nodes will "
                "error until they are pointed at another version."
            )
        if self._confirm(message):
            self._delete(checked)

    def _delete_all_unused(self, shots: list[Shot]) -> None:
        layers: list[RenderLayer] = []
        no_comp: list[str] = []
        for shot in shots:
            renders = find_renders(shot)
            layers.extend(renders.layers)
            if not renders.comp_read:
                no_comp.append(shot.code or "")
        doomed = [version for layer in layers for version in layer.unused_versions]
        shot_codes = _listed([shot.code or "" for shot in shots])
        kept = (
            "Versions the comp script reads, the latest version of every layer, "
            "and FINAL layers are kept."
        )
        if no_comp:
            kept += (
                f"\n\n{_listed(no_comp)} {'has' if len(no_comp) == 1 else 'have'} "
                "no comp script, so only the latest versions are kept there."
            )
        if not doomed:
            MessageDialog(
                self,
                f"Nothing to delete in {shot_codes}.\n\n{kept}",
                "Render Manager",
            ).exec_()
            return
        touched = sum(1 for layer in layers if layer.unused_versions)
        if self._confirm(
            f"Delete {_plural(len(doomed), 'unused version')} across "
            f"{_plural(touched, 'render layer')} in {shot_codes}?\n\n{kept}"
        ):
            self._delete(doomed)

    def _confirm(self, message: str) -> bool:
        return bool(
            MessageDialogCustomButtons(
                self,
                f"{message}\n\nDeleted renders cannot be recovered.",
                "Delete Renders",
                has_cancel_button=True,
                ok_name="Delete",
                cancel_name="Cancel",
            ).exec_()
        )

    def _delete(self, doomed: list[RenderVersion]) -> None:
        """Deletes folders on disk, then reads it again to show what is left."""
        failures: list[DeleteFailure] = []
        with progress_scope(
            parent=self, title="Delete Renders", steps=[_DELETE_STEP]
        ) as progress:
            progress.begin_step(_DELETE_STEP)
            for index, version in enumerate(doomed):
                progress.update_substep(
                    index, len(doomed), f"{version.path.parent.name}/{version.name}"
                )
                if failure := delete_version(version):
                    failures.append(failure)

        layer_item = self._layer_list.currentItem()
        layer: RenderLayer | None = layer_item.data(_DATA_ROLE) if layer_item else None
        self._show_layers(keep_layer=layer.name if layer else None)

        deleted = len(doomed) - len(failures)
        self._status.setText(f"Deleted {_plural(deleted, 'version')}.")
        if failures:
            self._report(failures)

    def _report(self, failures: list[DeleteFailure]) -> None:
        listed = "\n".join(
            f"{failure.path}: {failure.reason}"
            for failure in failures[:_MAX_LISTED_FAILURES]
        )
        more = len(failures) - _MAX_LISTED_FAILURES
        if more > 0:
            listed += f"\n…and {more} more."
        MessageDialog(
            self,
            f"Could not delete {_plural(len(failures), 'version')}:\n\n{listed}\n\n"
            "If the folder belongs to someone else, ask them or a TD to delete it.",
            "Delete Renders",
        ).exec_()


def _layer_item(layer: RenderLayer) -> QtWidgets.QListWidgetItem:
    count = _plural(len(layer.versions), "version")
    if not layer.is_final:
        item = QtWidgets.QListWidgetItem(f"{layer.name}  ({count})")
        item.setData(_DATA_ROLE, layer)
        return item
    item = QtWidgets.QListWidgetItem(f"{layer.name}  ({count}, protected)")
    # Grayed out and unselectable, so none of its versions can be checked.
    item.setFlags(QtCore.Qt.NoItemFlags)
    item.setToolTip("FINAL layers are delivered frames, and are never deleted.")
    return item


def _version_item(version: RenderVersion, script: str) -> QtWidgets.QTreeWidgetItem:
    label = f"{version.name}  (latest)" if version.is_latest else version.name
    item = QtWidgets.QTreeWidgetItem(
        [
            label,
            "Yes" if version.has_frames else "None yet",
            version.modified.strftime("%Y-%m-%d %H:%M"),
        ]
    )
    item.setData(_VERSION_COLUMN, _DATA_ROLE, version)
    item.setIcon(_VERSION_COLUMN, _in_comp_icon() if version.in_comp else _blank_icon())
    if version.in_comp:
        item.setToolTip(_VERSION_COLUMN, f"Read in {script}")
    item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
    # Everything but the newest starts checked, ready to clean up.
    item.setCheckState(
        _VERSION_COLUMN,
        QtCore.Qt.Unchecked if version.is_latest else QtCore.Qt.Checked,
    )
    return item


@cache
def _in_comp_icon() -> QtGui.QIcon:
    # Cached and built on first use: a QIcon needs the QApplication to exist.
    return QtGui.QIcon(str(_IN_COMP_ICON))


@cache
def _blank_icon() -> QtGui.QIcon:
    """Keeps version names lined up whether or not they show the comp icon."""
    pixmap = QtGui.QPixmap(_ICON_SIZE, _ICON_SIZE)
    pixmap.fill(QtCore.Qt.transparent)
    return QtGui.QIcon(pixmap)


def _add_placeholder(widget: QtWidgets.QListWidget, text: str) -> None:
    item = QtWidgets.QListWidgetItem(text)
    item.setFlags(QtCore.Qt.NoItemFlags)
    widget.addItem(item)


def _listed(codes: list[str]) -> str:
    shown = ", ".join(codes[:_MAX_LISTED_SHOTS])
    more = len(codes) - _MAX_LISTED_SHOTS
    return f"{shown}, and {more} more" if more > 0 else shown


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"
