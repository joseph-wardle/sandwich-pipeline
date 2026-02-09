"""Maya asset file manager and scene asset metadata helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import maya.cmds as mc
import maya.mel as mel
from env_sg import DB_Config
from Qt import QtCore, QtWidgets
from shared.util import get_production_path

from pipe.asset.paths import BACKUP_DIRNAME, DCC_MAYA, AssetPaths, paths_for_asset
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

FILEINFO_PREFIX = "pipe_asset"
FILEINFO_ASSET_ID = f"{FILEINFO_PREFIX}_id"
FILEINFO_ASSET_NAME = f"{FILEINFO_PREFIX}_name"
FILEINFO_ASSET_DISPLAY_NAME = f"{FILEINFO_PREFIX}_display_name"
FILEINFO_ASSET_PATH = f"{FILEINFO_PREFIX}_path"


@dataclass(frozen=True)
class AssetMetadata:
    id: Optional[int]
    name: Optional[str]
    display_name: Optional[str]
    path: Optional[str]
    asset: Optional[Asset]


def _normalize_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _get_file_info_value(key: str) -> Optional[str]:
    try:
        value = mc.fileInfo(key, query=True)
    except Exception:
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return _normalize_value(value)


def _set_file_info_value(key: str, value: Optional[str]) -> None:
    mc.fileInfo(key, value or "")


def write_asset_metadata(asset: Asset) -> None:
    """Write asset metadata to the current Maya scene fileInfo."""
    _set_file_info_value(FILEINFO_ASSET_ID, str(asset.id) if asset.id else "")
    _set_file_info_value(FILEINFO_ASSET_NAME, _normalize_value(asset.name))
    _set_file_info_value(
        FILEINFO_ASSET_DISPLAY_NAME, _normalize_value(asset.display_name)
    )
    _set_file_info_value(FILEINFO_ASSET_PATH, _normalize_value(asset.path))


def read_asset_metadata(conn: DB | None = None) -> AssetMetadata:
    """Read asset metadata from fileInfo and resolve to an Asset when possible."""
    asset_id_raw = _get_file_info_value(FILEINFO_ASSET_ID)
    asset_name = _get_file_info_value(FILEINFO_ASSET_NAME)
    asset_display_name = _get_file_info_value(FILEINFO_ASSET_DISPLAY_NAME)
    asset_path = _get_file_info_value(FILEINFO_ASSET_PATH)

    asset_id: Optional[int]
    if asset_id_raw:
        try:
            asset_id = int(asset_id_raw)
        except Exception:
            log.warning("Invalid asset id in fileInfo: %s", asset_id_raw)
            asset_id = None
    else:
        asset_id = None

    resolved: Asset | None = None
    conn = conn or DB.Get(DB_Config)
    if conn:
        if asset_id is not None:
            try:
                resolved = conn.get_asset_by_id(asset_id)
            except Exception as exc:
                log.warning("Failed to resolve asset by id %s: %s", asset_id, exc)
        if resolved is None and asset_path:
            try:
                resolved = conn.get_asset_by_attr("path", asset_path)
            except Exception as exc:
                log.warning("Failed to resolve asset by path %s: %s", asset_path, exc)

    return AssetMetadata(
        id=asset_id,
        name=asset_name,
        display_name=asset_display_name,
        path=asset_path,
        asset=resolved,
    )


def _asset_root_from_scene_path(scene_path: Path) -> Optional[Path]:
    if not scene_path:
        return None
    parent = scene_path.parent
    if parent.name == BACKUP_DIRNAME:
        return parent.parent
    return parent


def _asset_path_from_root(asset_root: Path) -> Optional[str]:
    if not asset_root:
        return None
    prod_root = get_production_path()
    try:
        rel_path = asset_root.relative_to(prod_root)
    except ValueError:
        rel_path = asset_root
    return rel_path.as_posix()


def _resolve_asset_from_scene_path(conn: DB, scene_path: Path) -> Optional[Asset]:
    asset_root = _asset_root_from_scene_path(scene_path)
    if not asset_root:
        return None
    asset_path = _asset_path_from_root(asset_root)
    if not asset_path:
        return None
    try:
        return conn.get_asset_by_attr("path", asset_path)
    except Exception as exc:
        log.debug("No asset found for scene path %s: %s", scene_path, exc)
        return None


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

        backup_versions = list_versions(paths.backup_dir, "model", "mb")
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
        return "model", "mb"

    def _open_file(self, path: Path) -> None:
        mc.file(str(path), open=True, force=True)

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        mc.file(new=True, force=True)
        mc.file(rename=str(path))
        mc.file(save=True, type="mayaBinary")
        asset = entity if isinstance(entity, Asset) else None
        if asset:
            write_asset_metadata(asset)

    def _ensure_scene_asset_metadata(self, scene_path: Optional[Path] = None) -> None:
        meta = read_asset_metadata(self._conn)
        if meta.asset:
            if (
                meta.id is None
                or not meta.name
                or not meta.display_name
                or not meta.path
            ):
                write_asset_metadata(meta.asset)
            return

        if scene_path is None:
            raw_path = mc.file(query=True, sn=True) or ""
            if not raw_path:
                return
            scene_path = Path(raw_path)

        asset = _resolve_asset_from_scene_path(self._conn, scene_path)
        if asset:
            write_asset_metadata(asset)

    def _prompt_backup_version(self, paths: AssetPaths) -> Optional[Path]:
        versions = list_versions(paths.backup_dir, "model", "mb")
        if not versions:
            MessageDialog(
                self._main_window,
                "No backup versions were found for this asset.",
                "No Backups",
            ).exec_()
            return None

        version_files = [
            versioned_filename("model", "mb", version)
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
                self._ensure_scene_asset_metadata(backup_path)
            return

        if not self._prompt_create_if_not_exist(paths.root):
            return

        model_path = paths.model_path
        if model_path.is_file():
            self._open_file(model_path)
            self._ensure_scene_asset_metadata(model_path)
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


__all__ = [
    "FILEINFO_PREFIX",
    "FILEINFO_ASSET_ID",
    "FILEINFO_ASSET_NAME",
    "FILEINFO_ASSET_DISPLAY_NAME",
    "FILEINFO_ASSET_PATH",
    "AssetMetadata",
    "write_asset_metadata",
    "read_asset_metadata",
    "AssetOpenDialog",
    "MAssetFileManager",
    "install_asset_menu",
]
