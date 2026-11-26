"""The Versions tab"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.cache import RENDER_DIRNAME
from pipe.core.render.protocol import shot_folder
from pipe.core.render.sizes import human
from pipe.core.render.versions import (
    DeleteFailure,
    DeleteSkipped,
    RenderLayer,
    RenderVersion,
    ShotRenders,
    delete_version,
    deletion_order,
    folder_size,
    shot_renders,
)
from pipe.core.ui import MessageDialog, MessageDialogCustomButtons, progress_scope
from pipe.core.util.paths import get_repo_root

_DATA_ROLE = QtCore.Qt.UserRole
_VERSION_COLUMN, _FRAMES_COLUMN, _MODIFIED_COLUMN = range(3)
_DELETE_STEP = "Deleting renders"
_MAX_LISTED_FAILURES = 8
_IN_COMP_ICON = get_repo_root() / "resources/icon/nuke.png"
_ICON_SIZE = 16
_MAX_LISTED_SHOTS = 6


class VersionsTab(QtWidgets.QWidget):
    deleted = QtCore.Signal()

    _layer_list: QtWidgets.QListWidget
    _version_tree: QtWidgets.QTreeWidget
    _remove_button: QtWidgets.QPushButton
    _clean_button: QtWidgets.QPushButton
    _clean_mine_button: QtWidgets.QPushButton
    _status: QtWidgets.QLabel
    _my_shots: list[str]
    _shot: str | None
    # Why the artist may not delete the shot's renders, or "".
    _refusal: str
    _renders: ShotRenders | None

    def __init__(
        self, my_shots: Sequence[str], parent: QtWidgets.QWidget | None = None
    ) -> None:
        """`my_shots` are the codes of the shots the artist lights."""
        super().__init__(parent)
        self._my_shots, self._shot, self._refusal = list(my_shots), None, ""
        self._renders = None

        self._layer_list = QtWidgets.QListWidget()
        self._version_tree = QtWidgets.QTreeWidget()
        self._version_tree.setHeaderLabels(["Version", "Frames", "Modified"])
        self._version_tree.setRootIsDecorated(False)
        self._version_tree.setIconSize(QtCore.QSize(_ICON_SIZE, _ICON_SIZE))

        self._remove_button = QtWidgets.QPushButton("Remove All Selected")
        self._clean_button = QtWidgets.QPushButton("Delete All Unused (This Shot)")
        self._clean_mine_button = QtWidgets.QPushButton(
            "Delete All Unused (All My Shots)"
        )
        self._clean_mine_button.setToolTip(
            "Delete All Unused for every shot you light."
        )
        self._clean_mine_button.setEnabled(bool(self._my_shots))
        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)

        splitter = QtWidgets.QSplitter()
        for title, widget in (
            ("Render Layers", self._layer_list),
            ("Versions", self._version_tree),
        ):
            pane = QtWidgets.QWidget()
            pane_layout = QtWidgets.QVBoxLayout(pane)
            pane_layout.setContentsMargins(0, 0, 0, 0)
            pane_layout.addWidget(QtWidgets.QLabel(f"<b>{title}</b>"))
            pane_layout.addWidget(widget)
            splitter.addWidget(pane)
        splitter.setSizes([300, 500])
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self._status, stretch=1)
        buttons.addWidget(self._clean_mine_button)
        buttons.addWidget(self._clean_button)
        buttons.addWidget(self._remove_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(splitter, stretch=1)
        layout.addLayout(buttons)

        self._layer_list.currentItemChanged.connect(self._show_versions)
        self._version_tree.itemChanged.connect(self._refresh_buttons)
        self._remove_button.clicked.connect(self._remove_checked)
        self._clean_button.clicked.connect(self._clean_shot)
        self._clean_mine_button.clicked.connect(
            lambda: self._delete_all_unused(self._my_shots)
        )
        self._refresh_buttons()

    def set_shot(self, shot_code: str, refusal: str) -> None:
        """`refusal` says why the artist may not delete its renders, or is ""."""
        self._shot, self._refusal, self._renders = shot_code, refusal, None
        if self.isVisible():
            self._show_layers()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 (Qt's name)
        super().showEvent(event)
        if self._renders is None:
            self._show_layers()

    # ---- panes -------------------------------------------------------------

    def _show_layers(self, keep_layer: str | None = None) -> None:
        """Reads the disk. Selects `keep_layer` again if it still has versions."""
        self._layer_list.clear()
        self._version_tree.clear()
        if self._shot is None:
            return
        self._renders = shot_renders(shot_folder(self._shot))
        self._status.setText(
            ""
            if self._renders.comp_read
            else f"No comp script at {self._renders.comp_script}, so no version "
            "shows as used; only the latest, newest complete, and newest with "
            "frames are kept."
        )
        layers = self._renders.layers
        if not layers:
            root = shot_folder(self._shot) / RENDER_DIRNAME
            _add_placeholder(self._layer_list, f"No renders in {root}")
            self._refresh_buttons()
            return
        for layer in layers:
            self._layer_list.addItem(_layer_item(layer))
        # Protected rows cannot be selected, so the first row may not be selectable.
        selectable = [layer.name for layer in layers if not layer.protection]
        if selectable:
            name = keep_layer if keep_layer in selectable else selectable[0]
            self._layer_list.setCurrentRow([layer.name for layer in layers].index(name))
        self._refresh_buttons()

    def _show_versions(self, *_: object) -> None:
        # Filling the tree emits itemChanged once per checkbox.
        self._version_tree.blockSignals(True)
        self._version_tree.clear()
        layer = self._current_layer()
        script = self._renders.comp_script.name if self._renders else ""
        unused = layer.unused_versions if layer else []
        for version in layer.versions if layer else []:
            item = _version_item(version, version in unused, script)
            self._version_tree.addTopLevelItem(item)
        for column in (_VERSION_COLUMN, _FRAMES_COLUMN, _MODIFIED_COLUMN):
            self._version_tree.resizeColumnToContents(column)
        self._version_tree.blockSignals(False)
        self._refresh_buttons()

    def _current_layer(self) -> RenderLayer | None:
        item = self._layer_list.currentItem()
        return item.data(_DATA_ROLE) if item else None

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

    def _refresh_buttons(self) -> None:
        allowed = self._shot is not None and not self._refusal
        self._remove_button.setEnabled(allowed and bool(self._checked_versions()))
        self._clean_button.setEnabled(allowed)
        self._remove_button.setToolTip(
            self._refusal or "Delete the checked versions of this render layer."
        )
        self._clean_button.setToolTip(
            self._refusal
            or "Delete the versions this shot's comp script does not read. Each "
            "layer's latest version, and FINAL and fx2d layers, are kept."
        )

    # ---- actions -----------------------------------------------------------

    def _remove_checked(self) -> None:
        checked = self._checked_versions()
        layer = self._current_layer()
        if not checked or layer is None:
            return
        names = ", ".join(version.name for version in checked)
        message = (
            f"Delete {_plural(len(checked), 'version')} of {layer.name}, "
            f"freeing {self._size_of(checked)}?\n\n{names}"
        )
        if kept := [
            f"{version.name} ({', '.join(version.kept_reasons)})"
            for version in checked
            if version.kept_reasons
        ]:
            message += (
                f"\n\nThis includes {', '.join(kept)}, which Delete All Unused "
                "always keeps: what to read while the latest renders, or after it "
                "fails."
            )
        if in_comp := [version.name for version in checked if version.in_comp]:
            script = self._renders.comp_script.name if self._renders else "the comp"
            message += (
                f"\n\n{script} reads {', '.join(in_comp)}. Those Read nodes will "
                "error until they are pointed at another version."
            )
        if self._confirm(message):
            self._delete(checked)

    def _clean_shot(self) -> None:
        if self._shot is not None:
            self._delete_all_unused([self._shot])

    def _delete_all_unused(self, shot_codes: list[str]) -> None:
        layers: list[RenderLayer] = []
        no_comp: list[str] = []
        for code in shot_codes:
            renders = shot_renders(shot_folder(code))
            layers.extend(renders.layers)
            if not renders.comp_read:
                no_comp.append(code)
        doomed = [version for layer in layers for version in layer.unused_versions]
        listed = _listed(shot_codes)
        kept = (
            "Versions the comp script reads, each layer's latest, newest complete, "
            "and newest with frames, and FINAL and fx2d layers are kept."
        )
        if no_comp:
            kept += (
                f"\n\n{_listed(no_comp)} {'has' if len(no_comp) == 1 else 'have'} "
                "no comp script, so only each layer's latest, newest complete, and "
                "newest with frames are kept there."
            )
        if not doomed:
            MessageDialog(
                self, f"Nothing to delete in {listed}.\n\n{kept}", "Delete Renders"
            ).exec_()
            return
        touched = sum(1 for layer in layers if layer.unused_versions)
        if self._confirm(
            f"Delete {_plural(len(doomed), 'unused version')} across "
            f"{_plural(touched, 'render layer')} in {listed}, freeing "
            f"{self._size_of(doomed)}?\n\n{kept}"
        ):
            self._delete(doomed)

    def _size_of(self, doomed: list[RenderVersion]) -> str:
        """How much the versions' files hold, such as "48.2 GB". Reading every
        frame's size takes a moment over the network, so the cursor says so."""
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            return human(sum(folder_size(version.path) for version in doomed))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

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
        skipped: list[DeleteSkipped] = []
        ordered = deletion_order(doomed)
        with progress_scope(
            parent=self, title="Delete Renders", steps=[_DELETE_STEP]
        ) as progress:
            progress.begin_step(_DELETE_STEP)
            for index, version in enumerate(ordered):
                progress.update_substep(
                    index, len(ordered), f"{version.path.parent.name}/{version.name}"
                )
                outcome = delete_version(version)
                if isinstance(outcome, DeleteFailure):
                    failures.append(outcome)
                elif isinstance(outcome, DeleteSkipped):
                    skipped.append(outcome)

        layer = self._current_layer()
        self._show_layers(keep_layer=layer.name if layer else None)
        self.deleted.emit()

        done = len(ordered) - len(failures) - len(skipped)
        status = f"Deleted {_plural(done, 'version')}."
        if skipped:
            status += f" Kept {len(skipped)} that changed since you confirmed."
        self._status.setText(status)
        if failures or skipped:
            self._report(failures, skipped)

    def _report(
        self, failures: list[DeleteFailure], skipped: list[DeleteSkipped]
    ) -> None:
        sections = []
        if skipped:
            sections.append(
                f"Kept {_plural(len(skipped), 'version')} that changed after you "
                f"confirmed:\n\n{_listed_outcomes(skipped)}"
            )
        if failures:
            sections.append(
                f"Could not delete {_plural(len(failures), 'version')}:\n\n"
                f"{_listed_outcomes(failures)}\n\nIf the folder belongs to someone "
                "else, ask them or a TD to delete it."
            )
        MessageDialog(self, "\n\n".join(sections), "Delete Renders").exec_()


def _listed_outcomes(outcomes: Sequence[DeleteFailure | DeleteSkipped]) -> str:
    listed = "\n".join(
        f"{outcome.path}: {outcome.reason}"
        for outcome in outcomes[:_MAX_LISTED_FAILURES]
    )
    more = len(outcomes) - _MAX_LISTED_FAILURES
    return f"{listed}\n…and {more} more." if more > 0 else listed


def _layer_item(layer: RenderLayer) -> QtWidgets.QListWidgetItem:
    count = _plural(len(layer.versions), "version")
    if not layer.protection:
        item = QtWidgets.QListWidgetItem(f"{layer.name}  ({count})")
        item.setData(_DATA_ROLE, layer)
        return item
    item = QtWidgets.QListWidgetItem(f"{layer.name}  ({count}, protected)")
    # Grayed out and unselectable, so none of its versions can be checked.
    item.setFlags(QtCore.Qt.NoItemFlags)
    item.setToolTip(layer.protection)
    return item


def _version_item(
    version: RenderVersion, unused: bool, script: str
) -> QtWidgets.QTreeWidgetItem:
    tags = version.kept_reasons
    label = f"{version.name}  ({', '.join(tags)})" if tags else version.name
    if version.is_complete:
        frames = "Yes"
    else:
        frames = "Incomplete" if version.has_frames else "None yet"
    item = QtWidgets.QTreeWidgetItem(
        [label, frames, version.modified.strftime("%Y-%m-%d %H:%M")]
    )
    item.setData(_VERSION_COLUMN, _DATA_ROLE, version)
    item.setIcon(_VERSION_COLUMN, _in_comp_icon() if version.in_comp else _blank_icon())
    if version.in_comp:
        item.setToolTip(_VERSION_COLUMN, f"Read in {script}")
    item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
    # What Delete All Unused would delete starts checked.
    item.setCheckState(
        _VERSION_COLUMN, QtCore.Qt.Checked if unused else QtCore.Qt.Unchecked
    )
    return item


@cache
def _in_comp_icon() -> QtGui.QIcon:
    # Built on first use: a QIcon needs the QApplication to exist.
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
