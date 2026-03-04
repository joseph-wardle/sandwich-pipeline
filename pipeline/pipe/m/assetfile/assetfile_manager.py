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
FILEINFO_ASSET_SUBDIRECTORY = f"{FILEINFO_PREFIX}_subdirectory"


@dataclass(frozen=True)
class AssetMetadata:
    id: Optional[int]
    name: Optional[str]
    display_name: Optional[str]
    path: Optional[str]
    subdirectory: Optional[str]
    asset: Optional[Asset]


def _normalize_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _get_file_info_value(key: str) -> Optional[str]:
    try:
        raw_value = mc.fileInfo(key, query=True)
    except Exception:
        return None
    if isinstance(raw_value, (list, tuple)):
        first_value = raw_value[0] if raw_value else None
        return _normalize_value(str(first_value) if first_value is not None else None)
    if isinstance(raw_value, str):
        return _normalize_value(raw_value)
    return None


def _set_file_info_value(key: str, value: Optional[str]) -> None:
    mc.fileInfo(key, value or "")


def _set_dialog_button_tooltips(
    dialog: QtWidgets.QDialog, *, ok_text: str, cancel_text: str
) -> None:
    buttons = getattr(dialog, "buttons", None)
    if not buttons:
        return
    ok_btn = buttons.button(QtWidgets.QDialogButtonBox.Ok)
    if ok_btn:
        ok_btn.setToolTip(ok_text)
    cancel_btn = buttons.button(QtWidgets.QDialogButtonBox.Cancel)
    if cancel_btn:
        cancel_btn.setToolTip(cancel_text)


def write_asset_metadata(asset: Asset) -> None:
    """Write asset metadata to the current Maya scene fileInfo."""
    _set_file_info_value(FILEINFO_ASSET_ID, str(asset.id) if asset.id else "")
    _set_file_info_value(FILEINFO_ASSET_NAME, _normalize_value(asset.name))
    _set_file_info_value(
        FILEINFO_ASSET_DISPLAY_NAME, _normalize_value(asset.display_name)
    )
    _set_file_info_value(FILEINFO_ASSET_PATH, _normalize_value(asset.asset_path))
    _set_file_info_value(
        FILEINFO_ASSET_SUBDIRECTORY, _normalize_value(asset.subdirectory)
    )


def read_asset_metadata(conn: DBInterface | None = None) -> AssetMetadata:
    """Read asset metadata from fileInfo and resolve to an Asset when possible."""
    asset_id_raw = _get_file_info_value(FILEINFO_ASSET_ID)
    asset_name = _get_file_info_value(FILEINFO_ASSET_NAME)
    asset_display_name = _get_file_info_value(FILEINFO_ASSET_DISPLAY_NAME)
    asset_path = _get_file_info_value(FILEINFO_ASSET_PATH)
    asset_subdirectory = _get_file_info_value(FILEINFO_ASSET_SUBDIRECTORY)

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
        subdirectory=asset_subdirectory,
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


def resolve_asset_from_scene_path(
    conn: DBInterface, scene_path: Path
) -> Optional[Asset]:
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

    _conn: DBInterface
    _open_backup_cb: QtWidgets.QCheckBox
    _info_label: QtWidgets.QLabel

    def __init__(
        self, parent: QtWidgets.QWidget | None, items: list[str], conn: DBInterface
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
        self._open_backup_cb.setToolTip(
            "Open a versioned backup instead of the live asset model file."
        )
        info_layout.addWidget(self._open_backup_cb)

        self._info_label = QtWidgets.QLabel("Select an asset to see details.")
        self._info_label.setWordWrap(True)
        self._info_label.setTextFormat(QtCore.Qt.PlainText)
        self._info_label.setToolTip(
            "Shows recent publish info and available backups for the selected asset."
        )
        info_layout.addWidget(self._info_label)

        self._layout.insertWidget(1, info_widget)
        if hasattr(self, "_filter_field"):
            self._filter_field.setToolTip("Type to filter the asset list.")
        self._list_widget.setToolTip("Select the asset model you want to open.")
        _set_dialog_button_tooltips(
            self,
            ok_text="Open the selected asset model file.",
            cancel_text="Close without opening a file.",
        )

    @property
    def open_backup(self) -> bool:
        return self._open_backup_cb.isChecked()

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item()
        if not selected:
            self._info_label.setText("Select an asset to see details.")
            return

        asset = self._conn.get_asset_by_name(selected)
        if not asset:
            self._info_label.setText("Could not resolve the selected asset.")
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

        info_lines = [
            f"Path: {paths.root}",
            f"Last publish: {publish_summary}",
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
        mc.file(newFile=True, force=True)
        mc.file(rename=str(path))
        mc.file(save=True, type="mayaBinary")
        asset = entity if isinstance(entity, Asset) else None
        if asset:
            write_asset_metadata(asset)

    def _ensure_scene_asset_metadata(self, scene_path: Optional[Path] = None) -> None:
        meta = read_asset_metadata(self._conn)
        if meta.asset:
            expected_path = _normalize_value(meta.asset.asset_path)
            expected_subdirectory = _normalize_value(meta.asset.subdirectory)
            if (
                meta.id is None
                or not meta.name
                or not meta.display_name
                or meta.path != expected_path
                or meta.subdirectory != expected_subdirectory
            ):
                log.info("Backfilling incomplete asset metadata in fileInfo.")
                write_asset_metadata(meta.asset)
            return

        if scene_path is None:
            raw_path = mc.file(query=True, sceneName=True)
            if not isinstance(raw_path, str) or not raw_path:
                log.debug("Scene has no file path; cannot infer asset metadata.")
                return
            scene_path = Path(raw_path)

        asset = resolve_asset_from_scene_path(self._conn, scene_path)
        if asset:
            log.info("Inferred asset metadata from scene path: %s", asset.asset_path)
            write_asset_metadata(asset)
        else:
            log.debug("Unable to infer asset metadata from scene path: %s", scene_path)

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
        if hasattr(dialog, "_filter_field"):
            dialog._filter_field.setToolTip("Type to filter backup versions.")
        dialog._list_widget.setToolTip("Select a backup version to open.")
        _set_dialog_button_tooltips(
            dialog,
            ok_text="Open the selected backup version.",
            cancel_text="Close without opening a backup.",
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
        if not asset:
            MessageDialog(
                self._main_window,
                "The selected asset could not be resolved from ShotGrid.",
                "Missing Asset",
            ).exec_()
            return

        file_open_event, file_create_event = self._telemetry_file_events()
        action_id = self._new_file_action_id()
        paths = paths_for_asset(asset)
        if dialog.open_backup:
            backup_path = self._prompt_backup_version(paths)
            if backup_path:
                try:
                    self._open_file(backup_path)
                    self._ensure_scene_asset_metadata(backup_path)
                except Exception as exc:
                    if file_open_event:
                        self._emit_file_event(
                            event_type=file_open_event,
                            status="error",
                            entity=asset,
                            path=backup_path,
                            action_id=action_id,
                            opened_backup=True,
                            error_message=str(exc),
                            exception_type=type(exc).__name__,
                        )
                    raise
                if file_open_event:
                    self._emit_file_event(
                        event_type=file_open_event,
                        status="success",
                        entity=asset,
                        path=backup_path,
                        action_id=action_id,
                        opened_backup=True,
                    )
            return

        if not self._prompt_create_if_not_exist(paths.root):
            return

        model_path = paths.model_path
        if model_path.is_file():
            try:
                self._open_file(model_path)
                self._ensure_scene_asset_metadata(model_path)
            except Exception as exc:
                if file_open_event:
                    self._emit_file_event(
                        event_type=file_open_event,
                        status="error",
                        entity=asset,
                        path=model_path,
                        action_id=action_id,
                        opened_backup=False,
                        error_message=str(exc),
                        exception_type=type(exc).__name__,
                    )
                raise
            if file_open_event:
                self._emit_file_event(
                    event_type=file_open_event,
                    status="success",
                    entity=asset,
                    path=model_path,
                    action_id=action_id,
                    opened_backup=False,
                )
        else:
            try:
                self._setup_file(model_path, asset)
            except Exception as exc:
                if file_create_event:
                    self._emit_file_event(
                        event_type=file_create_event,
                        status="error",
                        entity=asset,
                        path=model_path,
                        action_id=action_id,
                        error_message=str(exc),
                        exception_type=type(exc).__name__,
                    )
                raise
            if file_create_event:
                self._emit_file_event(
                    event_type=file_create_event,
                    status="success",
                    entity=asset,
                    path=model_path,
                    action_id=action_id,
                )


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

    menu: str
    if mc.menu(menu_name, exists=True):
        menu = menu_name
    elif create_menu:
        created_menu = mc.menu(
            menu_name,
            label=menu_name,
            parent=main_window,
            tearOff=True,
        )
        if not isinstance(created_menu, str):
            log.warning("Failed to create menu %s; skipping menu install", menu_name)
            return
        menu = created_menu
    else:
        log.debug("Menu %s not found; skipping menu install", menu_name)
        return

    if mc.menuItem(menu_item_name, exists=True, parent=menu):
        mc.deleteUI(menu_item_name)

    def _open_asset_model(*_args) -> None:
        MAssetFileManager().open_file()

    mc.menuItem(
        menu_item_name,
        parent=menu,
        label="Open Asset Model",
        annotation="Open or create the asset model file",
        command=_open_asset_model,
    )


__all__ = [
    "FILEINFO_PREFIX",
    "FILEINFO_ASSET_ID",
    "FILEINFO_ASSET_NAME",
    "FILEINFO_ASSET_DISPLAY_NAME",
    "FILEINFO_ASSET_PATH",
    "FILEINFO_ASSET_SUBDIRECTORY",
    "AssetMetadata",
    "write_asset_metadata",
    "read_asset_metadata",
    "resolve_asset_from_scene_path",
    "AssetOpenDialog",
    "MAssetFileManager",
    "install_asset_menu",
]
