"""Substance Painter asset opener and project metadata helpers.

This module centralizes how Painter projects map to pipeline asset roots and
stores the chosen asset in project metadata for reuse during export.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Optional

import substance_painter as sp
from env_sg import DB_Config
from Qt import QtCore, QtWidgets
from shared.util import resolve_mapped_path

from pipe.asset.paths import DCC_SUBSTANCE, AssetPaths, paths_for_asset
from pipe.db import DB
from pipe.glui.dialogs import (
    DialogFilteredList,
    FilteredListDialog,
    MessageDialog,
    MessageDialogCustomButtons,
)
from pipe.sp.local import get_main_qt_window
from pipe.struct.db import Asset

log = logging.getLogger(__name__)

# Metadata context + keys (easy to grep)
PIPE_SP_METADATA_CONTEXT = "bobo_asset_pipeline"
PIPE_SP_METADATA_KEY = "asset_selection"
PIPE_SP_METADATA_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _metadata() -> sp.project.Metadata:
    return sp.project.Metadata(PIPE_SP_METADATA_CONTEXT)


def _safe_get_metadata() -> dict[str, Any]:
    if not sp.project.is_open():
        return {}
    try:
        payload = _metadata().get(PIPE_SP_METADATA_KEY)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def get_asset_selection_metadata() -> dict[str, Any]:
    """Return the stored asset selection metadata, if any."""
    return _safe_get_metadata()


def _build_asset_selection_payload(
    asset_map: dict[str, str], last_asset: Optional[str] = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": PIPE_SP_METADATA_SCHEMA_VERSION,
        "dcc": DCC_SUBSTANCE,
        "asset_map": asset_map,
        "updated_at": _utc_now_iso(),
    }
    if last_asset:
        payload["last_asset"] = last_asset
    return payload


def store_asset_selection_metadata(asset_map: dict[str, str]) -> None:
    """Persist texture-set to asset mapping in the project metadata."""
    if not sp.project.is_open():
        return
    if sp.project.is_busy():
        sp.project.execute_when_not_busy(
            lambda: store_asset_selection_metadata(asset_map)
        )
        return

    last_asset = None
    if asset_map:
        unique = set(asset_map.values())
        if len(unique) == 1:
            last_asset = next(iter(unique))

    payload = _build_asset_selection_payload(asset_map, last_asset=last_asset)
    _metadata().set(PIPE_SP_METADATA_KEY, payload)


def store_asset_metadata_for_project(asset: Asset) -> None:
    """Store a single asset selection for all current texture sets."""
    if not sp.project.is_open():
        return

    asset_name = asset.name or asset.disp_name
    if not asset_name:
        return

    asset_map = {
        texset.name(): asset_name for texset in sp.textureset.all_texture_sets()
    }
    store_asset_selection_metadata(asset_map)


class SubstanceAssetDialog(FilteredListDialog):
    """Select an asset and preview the canonical textures path."""

    _conn: DB
    _info_label: QtWidgets.QLabel

    def __init__(
        self, parent: QtWidgets.QWidget | None, items: list[str], conn: DB
    ) -> None:
        super().__init__(
            parent,
            items,
            "Open Asset Textures",
            "Select the asset to open its textures project.",
            accept_button_name="Open",
        )
        self._conn = conn

        info_widget = QtWidgets.QWidget(self)
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)

        self._info_label = QtWidgets.QLabel("Select an asset to see details.")
        self._info_label.setWordWrap(True)
        self._info_label.setTextFormat(QtCore.Qt.PlainText)
        info_layout.addWidget(self._info_label)

        self._layout.insertWidget(1, info_widget)

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
        status = "exists" if paths.textures_path.exists() else "missing"
        self._info_label.setText(f"Textures project: {paths.textures_path} ({status})")


class SubstanceAssetActionDialog(QtWidgets.QDialog, DialogFilteredList):
    """Select an asset and choose how to open or create its textures project."""

    ACTION_OPEN_EXISTING = "open_existing"
    ACTION_SAVE_CURRENT = "save_current_as"
    ACTION_CREATE_DEFAULT = "create_default"

    _conn: DB
    _info_label: QtWidgets.QLabel
    _mesh_status_label: QtWidgets.QLabel
    _action: str | None
    _asset: Asset | None
    _paths: AssetPaths | None
    _resolved_mesh_path: Path | None

    def __init__(
        self, parent: QtWidgets.QWidget | None, items: list[str], conn: DB
    ) -> None:
        super().__init__(parent)
        self._conn = conn
        self._action = None
        self._asset = None
        self._paths = None
        self._resolved_mesh_path = None

        self._init_filtered_list(
            items,
            list_label="Select an asset to open or create its textures project.",
            include_filter_field=True,
        )

        self.setParent(parent)
        self.setWindowTitle("Open Asset Textures")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(600, 720)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(self.filtered_list)

        info_widget = QtWidgets.QWidget(self)
        info_layout = QtWidgets.QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)

        self._info_label = QtWidgets.QLabel("Select an asset to see details.")
        self._info_label.setWordWrap(True)
        self._info_label.setTextFormat(QtCore.Qt.PlainText)
        info_layout.addWidget(self._info_label)

        layout.addWidget(info_widget)

        create_widget = QtWidgets.QWidget(self)
        create_layout = QtWidgets.QVBoxLayout(create_widget)
        create_layout.setContentsMargins(0, 0, 0, 0)
        create_layout.setSpacing(6)

        create_title = QtWidgets.QLabel("Create Default Options")
        create_layout.addWidget(create_title)

        variant_row = QtWidgets.QHBoxLayout()
        self._geo_variant_radio = QtWidgets.QRadioButton("Geometry Variant")
        self._geo_variant_radio.setChecked(True)
        self._geo_variant_dropdown = QtWidgets.QComboBox()
        variant_row.addWidget(self._geo_variant_radio, 30)
        variant_row.addWidget(self._geo_variant_dropdown, 70)
        create_layout.addLayout(variant_row)

        custom_row = QtWidgets.QHBoxLayout()
        self._custom_mesh_radio = QtWidgets.QRadioButton("Custom Mesh")
        self._custom_mesh_field = QtWidgets.QLineEdit()
        self._custom_mesh_field.setPlaceholderText("Select a custom mesh...")
        self._custom_mesh_browse = QtWidgets.QPushButton("Browse...")
        custom_row.addWidget(self._custom_mesh_radio, 30)
        custom_row.addWidget(self._custom_mesh_field, 55)
        custom_row.addWidget(self._custom_mesh_browse, 15)
        create_layout.addLayout(custom_row)

        self._mesh_status_label = QtWidgets.QLabel("Mesh source: --")
        self._mesh_status_label.setWordWrap(True)
        self._mesh_status_label.setTextFormat(QtCore.Qt.PlainText)
        create_layout.addWidget(self._mesh_status_label)

        layout.addWidget(create_widget)

        buttons_layout = QtWidgets.QHBoxLayout()
        self._open_existing_btn = QtWidgets.QPushButton("Open Existing")
        self._save_current_btn = QtWidgets.QPushButton("Save Current As")
        self._create_default_btn = QtWidgets.QPushButton("Create Default")
        self._cancel_btn = QtWidgets.QPushButton("Cancel")

        buttons_layout.addWidget(self._open_existing_btn)
        buttons_layout.addWidget(self._save_current_btn)
        buttons_layout.addWidget(self._create_default_btn)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self._cancel_btn)
        layout.addLayout(buttons_layout)

        self._list_widget.itemSelectionChanged.connect(self._on_item_selected)
        self._geo_variant_dropdown.currentTextChanged.connect(self._update_mesh_status)
        self._custom_mesh_field.textChanged.connect(self._update_mesh_status)
        self._geo_variant_radio.toggled.connect(self._update_create_mode)
        self._custom_mesh_radio.toggled.connect(self._update_create_mode)
        self._custom_mesh_browse.clicked.connect(self._browse_custom_mesh)

        self._open_existing_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_OPEN_EXISTING)
        )
        self._save_current_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_SAVE_CURRENT)
        )
        self._create_default_btn.clicked.connect(
            lambda: self._set_action_and_accept(self.ACTION_CREATE_DEFAULT)
        )
        self._cancel_btn.clicked.connect(self.reject)

        self._update_create_mode()

    def _set_action_and_accept(self, action: str) -> None:
        self._action = action
        self.accept()

    def get_selected_action(self) -> str | None:
        return self._action

    def get_selected_asset(self) -> Asset | None:
        return self._asset

    def use_custom_mesh(self) -> bool:
        return self._custom_mesh_radio.isChecked()

    def get_selected_variant(self) -> str:
        return self._geo_variant_dropdown.currentText().strip()

    def get_custom_mesh_path(self) -> Path | None:
        text = self._custom_mesh_field.text().strip()
        if not text:
            return None
        return Path(text).expanduser()

    def get_resolved_mesh_path(self) -> Path | None:
        return self._resolved_mesh_path

    def _on_item_selected(self) -> None:
        selected = self.get_selected_item()
        if not selected:
            self._asset = None
            self._paths = None
            self._info_label.setText("Select an asset to see details.")
            self._populate_geo_variants(None)
            self._update_create_mode()
            return

        asset = self._conn.get_asset_by_name(selected)
        if not asset or not asset.path:
            self._asset = None
            self._paths = None
            self._info_label.setText("Asset path not set in ShotGrid.")
            self._populate_geo_variants(None)
            self._update_create_mode()
            return

        self._asset = asset
        self._paths = paths_for_asset(asset)
        project_exists = self._paths.textures_path.exists()
        status = "exists" if project_exists else "missing"
        self._info_label.setText(
            f"Textures project: {self._paths.textures_path} ({status})"
        )

        self._populate_geo_variants(asset)
        self._update_create_mode()

    def _populate_geo_variants(self, asset: Asset | None) -> None:
        variants = set()
        if asset and hasattr(asset, "geometry_variants"):
            variants.update(asset.geometry_variants)
        variants.add("main")
        ordered = sorted(v for v in variants if v)
        self._geo_variant_dropdown.clear()
        self._geo_variant_dropdown.addItems(ordered)
        if "main" in ordered:
            self._geo_variant_dropdown.setCurrentText("main")

    def _update_create_mode(self) -> None:
        has_asset = bool(self._asset)
        use_variant = self._geo_variant_radio.isChecked()
        self._geo_variant_radio.setEnabled(has_asset)
        self._custom_mesh_radio.setEnabled(has_asset)
        self._geo_variant_dropdown.setEnabled(has_asset and use_variant)
        self._custom_mesh_field.setEnabled(has_asset and not use_variant)
        self._custom_mesh_browse.setEnabled(has_asset and not use_variant)
        self._update_mesh_status()

    def _browse_custom_mesh(self) -> None:
        base_dir = ""
        if self._paths:
            base_dir = str(self._paths.publish_source_dir)
        selection, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Mesh File",
            base_dir,
            "Mesh Files (*.usd *.usda *.usdc *.obj *.fbx *.abc);;All Files (*)",
        )
        if selection:
            resolved = resolve_mapped_path(Path(selection))
            self._custom_mesh_field.setText(str(resolved))

    def _update_mesh_status(self) -> None:
        self._resolved_mesh_path = None

        if not self._paths or not self._asset:
            self._mesh_status_label.setText("Mesh source: --")
            self._update_state()
            return

        if self._geo_variant_radio.isChecked():
            variant = self.get_selected_variant()
            if variant:
                self._resolved_mesh_path = self._paths.publish_source_variant_usd(
                    variant
                )
        else:
            self._resolved_mesh_path = self.get_custom_mesh_path()

        if self._resolved_mesh_path:
            status = "exists" if self._resolved_mesh_path.exists() else "missing"
            self._mesh_status_label.setText(
                f"Mesh source: {self._resolved_mesh_path} ({status})"
            )
        else:
            self._mesh_status_label.setText("Mesh source: --")

        self._update_state()

    def _update_state(self) -> None:
        project_open = sp.project.is_open()
        project_exists = False
        if self._paths:
            project_exists = self._paths.textures_path.exists()

        mesh_ready = (
            self._resolved_mesh_path is not None and self._resolved_mesh_path.exists()
        )

        self._open_existing_btn.setEnabled(project_exists)
        self._save_current_btn.setEnabled(bool(self._asset) and project_open)
        self._create_default_btn.setEnabled(bool(self._asset) and mesh_ready)


def _confirm_discard_unsaved(parent: QtWidgets.QWidget | None) -> bool:
    dialog = MessageDialogCustomButtons(
        parent,
        "The current project has unsaved changes. Continue and discard them?",
        "Unsaved Changes",
        has_cancel_button=True,
        ok_name="Continue",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _confirm_save_as(parent: QtWidgets.QWidget | None, path: Path) -> bool:
    dialog = MessageDialogCustomButtons(
        parent,
        f"No project exists at {path}. Save the current project there?",
        "Create Textures Project",
        has_cancel_button=True,
        ok_name="Save As",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _confirm_overwrite_project(parent: QtWidgets.QWidget | None, path: Path) -> bool:
    dialog = MessageDialogCustomButtons(
        parent,
        f"A textures project already exists at {path}. Overwrite it?",
        "Overwrite Textures Project",
        has_cancel_button=True,
        ok_name="Overwrite",
        cancel_name="Cancel",
    )
    return bool(dialog.exec_())


def _current_project_path() -> Path | None:
    try:
        current_path = sp.project.file_path()
    except Exception:
        return None
    if not current_path:
        return None
    return Path(current_path)


def _store_asset_metadata_when_ready(asset: Asset) -> None:
    if not sp.project.is_open():
        return
    sp.project.execute_when_not_busy(lambda: store_asset_metadata_for_project(asset))


def _open_existing_project(path: Path) -> None:
    sp.project.open(str(path))


def _save_current_project_as(path: Path) -> None:
    sp.project.save_as(str(path))


def _open_existing_project_for_asset(asset: Asset, project_path: Path) -> None:
    parent = get_main_qt_window()
    if not project_path.exists():
        MessageDialog(
            parent,
            "No textures project exists yet. Use Save Current As or Create Default.",
            "Missing Textures Project",
        ).exec_()
        return

    current_path = _current_project_path()
    if current_path and current_path.resolve() == project_path.resolve():
        if sp.project.needs_saving():
            _save_current_project_as(project_path)
        _store_asset_metadata_when_ready(asset)
        return

    if sp.project.is_open():
        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        sp.project.close()

    _open_existing_project(project_path)
    _store_asset_metadata_when_ready(asset)


def _save_current_project_as_asset(asset: Asset, project_path: Path) -> None:
    parent = get_main_qt_window()
    if not sp.project.is_open():
        MessageDialog(
            parent,
            "No project is currently open. Open or create a project before saving.",
            "No Project Open",
        ).exec_()
        return

    current_path = _current_project_path()
    if current_path and current_path.resolve() == project_path.resolve():
        _store_asset_metadata_when_ready(asset)
        return

    if project_path.exists() and not _confirm_overwrite_project(parent, project_path):
        return

    project_path.parent.mkdir(parents=True, exist_ok=True)
    _save_current_project_as(project_path)
    _store_asset_metadata_when_ready(asset)


def _create_default_project_for_asset(
    asset: Asset, project_path: Path, mesh_path: Path | None
) -> None:
    parent = get_main_qt_window()
    if not mesh_path or not mesh_path.exists():
        MessageDialog(
            parent,
            "The selected mesh source is missing. Choose a valid mesh to proceed.",
            "Missing Mesh Source",
        ).exec_()
        return

    if sp.project.is_open():
        if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
            return
        sp.project.close()

    if project_path.exists() and not _confirm_overwrite_project(parent, project_path):
        return

    MessageDialog(
        parent,
        "Default project creation is not configured yet. "
        "Use Save Current As for now.",
        "Create Default",
    ).exec_()


def _dispatch_textures_action(
    asset: Asset, action: str, mesh_path: Path | None = None
) -> None:
    paths = paths_for_asset(asset)
    project_path = paths.textures_path

    if action == SubstanceAssetActionDialog.ACTION_OPEN_EXISTING:
        _open_existing_project_for_asset(asset, project_path)
        return
    if action == SubstanceAssetActionDialog.ACTION_SAVE_CURRENT:
        _save_current_project_as_asset(asset, project_path)
        return
    if action == SubstanceAssetActionDialog.ACTION_CREATE_DEFAULT:
        _create_default_project_for_asset(asset, project_path, mesh_path)
        return

    log.warning("Unknown textures action requested: %s", action)


def launch_open_asset_textures() -> None:
    """Open or create the textures project for a selected asset."""

    if sp.project.is_busy():
        sp.project.execute_when_not_busy(launch_open_asset_textures)
        return

    conn = DB.Get(DB_Config)
    asset_names = conn.get_asset_name_list(sorted=True)
    parent = get_main_qt_window()
    dialog = SubstanceAssetActionDialog(parent, asset_names, conn)
    if not dialog.exec_():
        return

    action = dialog.get_selected_action()
    asset = dialog.get_selected_asset()
    if not action or not asset:
        return
    if not asset or not asset.path:
        MessageDialog(
            parent,
            "The selected asset does not have a valid path in ShotGrid.",
            "Missing Asset Path",
        ).exec_()
        return

    _dispatch_textures_action(asset, action, mesh_path=dialog.get_resolved_mesh_path())


__all__ = [
    "PIPE_SP_METADATA_CONTEXT",
    "PIPE_SP_METADATA_KEY",
    "PIPE_SP_METADATA_SCHEMA_VERSION",
    "get_asset_selection_metadata",
    "store_asset_metadata_for_project",
    "store_asset_selection_metadata",
    "launch_open_asset_textures",
]
