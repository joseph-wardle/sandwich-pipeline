"""Maya asset file manager and UI for opening model files with version info."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import maya.cmds as mc
import maya.mel as mel
from env_sg import DB_Config
from Qt import QtCore, QtWidgets

from pipe.asset.paths import DCC_MAYA, AssetPaths, paths_for_asset
from pipe.asset.versioning import (
    get_manifest_path,
    list_versions,
    load_manifest,
    versioned_filename,
)
from pipe.db import DB, DBInterface
from pipe.glui.dialogs import FilteredListDialog, MessageDialog
from pipe.m.local import get_main_qt_window
from pipe.struct.db import Asset, SGEntity
from pipe.util import FileManager

log = logging.getLogger(__name__)


class AssetOpenDialog(FilteredListDialog):
    """Dialog for selecting an asset and previewing manifest metadata."""

    _conn: DB
    _open_backup_cb: QtWidgets.QCheckBox
    _info_label: QtWidgets.QLabel

    def __init__(
        self, parent: QtWidgets.QWidget | None, items: list[str], conn: DB
    ) -> None:
        super().__init__(
            parent,
            items,
            "Open Asset Model",
            "Select the asset model file to open.",
            accept_button_name="Open",
        )
        self._conn = conn

        info_widget = QtWidgets.QWidget(self)
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)

        self._open_backup_cb = QtWidgets.QCheckBox("Open backup version")
        info_layout.addWidget(self._open_backup_cb)

        self._info_label = QtWidgets.QLabel("Select an asset to see details.")
        self._info_label.setWordWrap(True)
        self._info_label.setTextFormat(QtCore.Qt.PlainText)
        info_layout.addWidget(self._info_label)

        self._layout.insertWidget(1, info_widget)

    @property
    def open_backup(self) -> bool:
        return self._open_backup_cb.isChecked()

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item()
        if not selected:
            self._info_label.setText("Select an asset to see details.")
            return

        asset = self._conn.get_asset_by_name(selected)
        if not asset or not asset.path:
            self._info_label.setText("Asset path not set in ShotGrid.")
            return

        paths = paths_for_asset(asset)
        manifest_path = get_manifest_path(paths.root)
        manifest = load_manifest(manifest_path)

        dcc_block = manifest.get("dcc", {}).get(DCC_MAYA, {})
        current = dcc_block.get("current") or {}

        version = current.get("version")
        user = current.get("user")
        timestamp = current.get("timestamp")

        publish_summary = "No publish recorded"
        if version is not None:
            version_label = f"v{int(version):03d}"
            parts = [version_label]
            if user:
                parts.append(f"by {user}")
            if timestamp:
                parts.append(f"at {timestamp}")
            publish_summary = " ".join(parts)

        backup_versions = list_versions(paths.backup_dir, "model", "ma")
        if backup_versions:
            backup_label = ", ".join(f"v{v:03d}" for v in backup_versions)
        else:
            backup_label = "none"

        manifest_state = "present" if manifest_path.exists() else "missing"
        info_lines = [
            f"Root: {paths.root}",
            f"Model: {paths.model_path}",
            f"Manifest: {manifest_path} ({manifest_state})",
            f"Last publish (manifest): {publish_summary}",
            f"Available backups: {backup_label}",
        ]
        self._info_label.setText("\n".join(info_lines))


class MAssetFileManager(FileManager):
    """Open or create Maya asset model files with manifest awareness."""

    def __init__(self) -> None:
        conn = DB.Get(DB_Config)
        window = get_main_qt_window()
        super().__init__(conn, Asset, window)

    def _check_unsaved_changes(self) -> bool:
        if mc.file(query=True, modified=True):
            response = mc.confirmDialog(
                title="Do you want to save?",
                message="The current file has not been saved. Continue anyways?",
                button=["Continue", "Cancel"],
                defaultButton="Cancel",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if response == "Cancel":
                return False
        return True

    def _generate_filename_ext(self, entity: SGEntity) -> tuple[str, str]:
        return "model", "ma"

    def _open_file(self, path: Path) -> None:
        mc.file(str(path), open=True, force=True)

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        mc.file(new=True, force=True)
        mc.file(rename=str(path))
        mc.file(save=True, type="mayaAscii")

    def _prompt_backup_version(self, paths: AssetPaths) -> Optional[Path]:
        versions = list_versions(paths.backup_dir, "model", "ma")
        if not versions:
            MessageDialog(
                self._main_window,
                "No backup versions were found for this asset.",
                "No Backups",
            ).exec_()
            return None

        version_files = [
            versioned_filename("model", "ma", version)
            for version in sorted(versions, reverse=True)
        ]
        dialog = FilteredListDialog(
            self._main_window,
            version_files,
            "Open Backup Version",
            "Select the backup version to open.",
            accept_button_name="Open",
        )
        if not dialog.exec_():
            return None
        selected = dialog.get_selected_item()
        if not selected:
            return None
        return paths.backup_dir / selected

    def open_file(self) -> None:
        if not self._check_unsaved_changes():
            return

        asset_names = self._conn.get_entity_code_list(
            Asset,
            sorted=True,
            child_mode=DBInterface.ChildQueryMode.ROOTS,
        )
        dialog = AssetOpenDialog(self._main_window, asset_names, self._conn)
        if not dialog.exec_():
            return

        selection = dialog.get_selected_item()
        if not selection:
            return

        asset = self._conn.get_asset_by_name(selection)
        if not asset or not asset.path:
            MessageDialog(
                self._main_window,
                "The selected asset does not have a valid path in ShotGrid.",
                "Missing Asset Path",
            ).exec_()
            return

        paths = paths_for_asset(asset)
        if dialog.open_backup:
            backup_path = self._prompt_backup_version(paths)
            if backup_path:
                self._open_file(backup_path)
            return

        if not self._prompt_create_if_not_exist(paths.root):
            return

        model_path = paths.model_path
        if model_path.is_file():
            self._open_file(model_path)
        else:
            self._setup_file(model_path, asset)


def install_asset_menu(
    *,
    menu_name: str = "Bobo",
    create_menu: bool = False,
    menu_item_name: str = "BoboOpenAssetModel",
) -> None:
    """Install the optional Open Asset menu item in Maya's main menu bar."""

    main_window = mel.eval("$tempVar=$gMainWindow")
    if not main_window:
        return

    if mc.menu(menu_name, exists=True):
        menu = menu_name
    elif create_menu:
        menu = mc.menu(menu_name, label=menu_name, parent=main_window, tearOff=True)
    else:
        log.debug("Menu %s not found; skipping menu install", menu_name)
        return

    if mc.menuItem(menu_item_name, exists=True, parent=menu):
        mc.deleteUI(menu_item_name)

    mc.menuItem(
        menu_item_name,
        parent=menu,
        label="Open Asset Model",
        annotation="Open or create the asset model file",
        command="from pipe.m.assetfile import MAssetFileManager; "
        "MAssetFileManager().open_file()",
    )


__all__ = ["MAssetFileManager", "install_asset_menu"]
