"""The Render Manager tab: a lighter deletes old render versions of the shots
they light, to free room on the render drive.

Three panes: the shots, a shot's render layers, a layer's versions. Which
versions the comp reads, and which every cleanup keeps, is for
`pipe.core.render.manager.disk` to say. Nothing is read from the disk until the
tab is first shown.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

from Qt import QtCore, QtGui, QtWidgets

from pipe.core.render.lighters import LIGHTING_STEP
from pipe.core.render.manager.disk import (
    DeleteFailure,
    DeleteSkipped,
    RenderLayer,
    RenderVersion,
    ShotRenders,
    delete_version,
    deletion_order,
    find_renders,
    render_root,
)
from pipe.core.render.network import theme, widgets
from pipe.core.shotgrid import Shot
from pipe.core.ui import MessageDialog, MessageDialogCustomButtons, progress_scope
from pipe.core.util.paths import get_repo_root

TITLE = "Render Manager"
_DATA_ROLE = QtCore.Qt.UserRole
_VERSION_COLUMN, _FRAMES_COLUMN, _MODIFIED_COLUMN = range(3)
_DELETE_STEP = "Deleting renders"
# A failure dialog that lists hundreds of paths is not read; the log has them all.
_MAX_LISTED_FAILURES = 8
_IN_COMP_ICON = get_repo_root() / "resources/icon/nuke.png"
_ICON_SIZE = 16
# Every shot's code in a confirmation is a wall of text nobody reads.
_MAX_LISTED_SHOTS = 6


class ManagerPage(QtWidgets.QWidget):
    _shot_list: QtWidgets.QTreeWidget
    _layer_list: QtWidgets.QTreeWidget
    _version_tree: QtWidgets.QTreeWidget
    _notice: widgets.Banner
    _remove_button: QtWidgets.QPushButton
    _clean_button: QtWidgets.QPushButton
    _clean_all_button: QtWidgets.QPushButton
    _status: QtWidgets.QLabel
    _renders: ShotRenders | None
    # The first shot is read when the tab is first shown, not at startup.
    _shown_once: bool

    def __init__(
        self,
        shots: Sequence[Shot] | None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        """`shots` are those ShotGrid assigns the artist to light, or None when
        it couldn't say."""
        super().__init__(parent)
        self._renders = None
        self._shown_once = False

        title = widgets.PageHeading(
            "hard-drive", TITLE, "Delete old render versions of the shots you light"
        )
        refresh = widgets.button(
            "Refresh",
            "refresh",
            "Read the chosen shot's renders from the disk again",
            clicked=self._refresh,
        )
        self._notice = widgets.Banner()
        self._notice.hide()

        self._shot_list = _pane(["Shot"])
        # Several shots at once, for Delete All Unused.
        self._shot_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        for shot in shots or []:
            item = QtWidgets.QTreeWidgetItem([shot.code or ""])
            item.setData(0, _DATA_ROLE, shot)
            self._shot_list.addTopLevelItem(item)

        self._layer_list = _pane(["Render Layer", "Versions"])
        self._layer_list.setColumnWidth(0, 160)
        self._version_tree = _pane(["Version", "Frames", "Modified"])
        self._version_tree.setIconSize(QtCore.QSize(_ICON_SIZE, _ICON_SIZE))

        self._remove_button = widgets.button(
            "Remove All Selected",
            "trash",
            "Delete the checked versions of this render layer.",
            theme.DANGER,
            self._remove_checked,
        )
        self._clean_button = widgets.button(
            "Delete All Unused",
            "trash",
            "In every selected shot, delete the versions its comp script does not "
            "read. Each layer's latest version, and FINAL layers, are kept.",
            theme.DANGER,
            lambda: self._delete_all_unused(self._selected_shots()),
        )
        self._clean_all_button = widgets.button(
            "Delete All Unused (All Shots)",
            "trash",
            "Delete All Unused for every shot in the list, selected or not.",
            theme.DANGER,
            lambda: self._delete_all_unused(self._all_shots()),
        )
        self._clean_all_button.setEnabled(bool(shots))
        self._status = QtWidgets.QLabel()
        self._status.setStyleSheet(f"QLabel {{ color: {theme.DIM_TEXT}; }}")

        header = QtWidgets.QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh)
        splitter = QtWidgets.QSplitter()
        for pane in (self._shot_list, self._layer_list, self._version_tree):
            splitter.addWidget(pane)
        splitter.setSizes([180, 320, 560])
        splitter.setStretchFactor(2, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self._status, stretch=1)
        buttons.addWidget(self._clean_all_button)
        buttons.addWidget(self._clean_button)
        buttons.addWidget(self._remove_button)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 10)
        layout.setSpacing(10)
        layout.addLayout(header)
        layout.addWidget(self._notice)
        layout.addWidget(splitter, 1)
        layout.addLayout(buttons)

        self._shot_list.currentItemChanged.connect(self._show_layers)
        self._shot_list.itemSelectionChanged.connect(self._refresh_buttons)
        self._layer_list.currentItemChanged.connect(self._show_versions)
        self._version_tree.itemChanged.connect(self._refresh_buttons)

        if shots is None:
            self._set_notice(
                "ShotGrid couldn't say which shots you light, so none are listed. "
                "Reopen Ranch to try again.",
                widgets.WARNING,
            )
        elif not shots:
            self._set_notice(
                f"ShotGrid has no {LIGHTING_STEP} tasks assigned to you, so there "
                "are no shots to manage renders for."
            )
        self._refresh_buttons()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 (Qt's name)
        super().showEvent(event)
        if not self._shown_once:
            self._shown_once = True
            # Once shown, so the tab draws before the disk is read.
            QtCore.QTimer.singleShot(0, self._choose_first_shot)

    def _choose_first_shot(self) -> None:
        first = self._shot_list.topLevelItem(0)
        if first is not None and self._shot_list.currentItem() is None:
            self._shot_list.setCurrentItem(first)

    # ---- panes -------------------------------------------------------------

    def _current_shot(self) -> Shot | None:
        item = self._shot_list.currentItem()
        return item.data(0, _DATA_ROLE) if item else None

    def _current_layer(self) -> RenderLayer | None:
        item = self._layer_list.currentItem()
        return item.data(0, _DATA_ROLE) if item else None

    def _refresh(self) -> None:
        layer = self._current_layer()
        self._show_layers(keep_layer=layer.name if layer else None)

    def _show_layers(self, *_: object, keep_layer: str | None = None) -> None:
        """Reads the disk. Selects `keep_layer` again if it still has versions."""
        self._layer_list.clear()
        self._status.setText("")
        shot = self._current_shot()
        self._renders = find_renders(shot) if shot else None
        if shot is None or self._renders is None:
            return
        if self._renders.comp_read:
            self._notice.hide()
        else:
            self._set_notice(
                f"No comp script at {self._renders.comp_script}, so no version "
                "shows as used; only the latest, newest complete, and newest with "
                "frames are kept.",
                widgets.WARNING,
            )
        layers = self._renders.layers
        if not layers:
            _add_placeholder(self._layer_list, f"No renders in {render_root(shot)}")
            return
        for layer in layers:
            self._layer_list.addTopLevelItem(_layer_item(layer))
        # FINAL rows cannot be selected, so the first row may not be selectable.
        selectable = [layer.name for layer in layers if not layer.is_final]
        if selectable:
            name = keep_layer if keep_layer in selectable else selectable[0]
            index = [layer.name for layer in layers].index(name)
            self._layer_list.setCurrentItem(self._layer_list.topLevelItem(index))

    def _show_versions(self, *_: object) -> None:
        # Filling the tree emits itemChanged once per checkbox.
        self._version_tree.blockSignals(True)
        self._version_tree.clear()
        layer = self._current_layer()
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
            self._shot_list.topLevelItem(row).data(0, _DATA_ROLE)
            for row in range(self._shot_list.topLevelItemCount())
        ]

    def _selected_shots(self) -> list[Shot]:
        return [item.data(0, _DATA_ROLE) for item in self._shot_list.selectedItems()]

    def _refresh_buttons(self) -> None:
        self._remove_button.setEnabled(bool(self._checked_versions()))
        self._clean_button.setEnabled(bool(self._selected_shots()))

    def _set_notice(self, text: str, kind: str = widgets.INFO) -> None:
        self._notice.set_kind(kind)
        self._notice.setText(text)
        self._notice.show()

    # ---- actions -----------------------------------------------------------

    def _remove_checked(self) -> None:
        checked = self._checked_versions()
        layer = self._current_layer()
        if not checked or layer is None:
            return
        names = ", ".join(version.name for version in checked)
        message = (
            f"Delete {_plural(len(checked), 'version')} of {layer.name}?\n\n{names}"
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
            "Versions the comp script reads, each layer's latest, newest complete, "
            "and newest with frames, and FINAL layers are kept."
        )
        if no_comp:
            kept += (
                f"\n\n{_listed(no_comp)} {'has' if len(no_comp) == 1 else 'have'} "
                "no comp script, so only each layer's latest, newest complete, and "
                "newest with frames are kept there."
            )
        if not doomed:
            MessageDialog(
                self, f"Nothing to delete in {shot_codes}.\n\n{kept}", TITLE
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

        self._refresh()

        deleted = len(ordered) - len(failures) - len(skipped)
        status = f"Deleted {_plural(deleted, 'version')}."
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


def _pane(headers: list[str]) -> QtWidgets.QTreeWidget:
    """A list with column headers, which name the pane."""
    table = QtWidgets.QTreeWidget()
    table.setHeaderLabels(headers)
    table.setRootIsDecorated(False)
    table.setAlternatingRowColors(True)
    # Rows added before the list is styled are sized without the theme's
    # padding, as the shots are, when the tab is built.
    table.ensurePolished()
    return table


def _listed_outcomes(outcomes: Sequence[DeleteFailure | DeleteSkipped]) -> str:
    listed = "\n".join(
        f"{outcome.path}: {outcome.reason}"
        for outcome in outcomes[:_MAX_LISTED_FAILURES]
    )
    more = len(outcomes) - _MAX_LISTED_FAILURES
    return f"{listed}\n…and {more} more." if more > 0 else listed


def _layer_item(layer: RenderLayer) -> QtWidgets.QTreeWidgetItem:
    count = _plural(len(layer.versions), "version")
    if not layer.is_final:
        item = QtWidgets.QTreeWidgetItem([layer.name, count])
        item.setData(0, _DATA_ROLE, layer)
        return item
    item = QtWidgets.QTreeWidgetItem([layer.name, f"{count}, protected"])
    # Grayed out and unselectable, so none of its versions can be checked.
    item.setFlags(QtCore.Qt.NoItemFlags)
    for column in range(2):
        item.setToolTip(
            column, "FINAL layers are delivered frames, and are never deleted."
        )
    return item


def _version_item(version: RenderVersion, script: str) -> QtWidgets.QTreeWidgetItem:
    tags = version.kept_reasons
    label = f"{version.name}  ({', '.join(tags)})" if tags else version.name
    item = QtWidgets.QTreeWidgetItem(
        [
            label,
            "Yes"
            if version.is_complete
            else "Incomplete"
            if version.has_frames
            else "None yet",
            version.modified.strftime("%Y-%m-%d %H:%M"),
        ]
    )
    item.setData(_VERSION_COLUMN, _DATA_ROLE, version)
    item.setIcon(_VERSION_COLUMN, _in_comp_icon() if version.in_comp else _blank_icon())
    if version.in_comp:
        item.setToolTip(_VERSION_COLUMN, f"Read in {script}")
    item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
    # What every cleanup keeps starts unchecked; the rest, ready to clean up.
    item.setCheckState(
        _VERSION_COLUMN,
        QtCore.Qt.Unchecked if version.kept_reasons else QtCore.Qt.Checked,
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


def _add_placeholder(widget: QtWidgets.QTreeWidget, text: str) -> None:
    item = QtWidgets.QTreeWidgetItem([text])
    item.setFlags(QtCore.Qt.NoItemFlags)
    widget.addTopLevelItem(item)
    item.setFirstColumnSpanned(True)


def _listed(codes: list[str]) -> str:
    shown = ", ".join(codes[:_MAX_LISTED_SHOTS])
    more = len(codes) - _MAX_LISTED_SHOTS
    return f"{shown}, and {more} more" if more > 0 else shown


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"
