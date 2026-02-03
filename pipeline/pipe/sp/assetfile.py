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

from pipe.asset.paths import DCC_SUBSTANCE, AssetPaths, paths_for_asset
from pipe.db import DB
from pipe.glui.dialogs import (
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


def _open_existing_project(path: Path) -> None:
    sp.project.open(str(path))


def _save_current_project_as(path: Path) -> None:
    sp.project.save_as(str(path))


def _open_or_create_textures_project(asset: Asset) -> None:
    paths = paths_for_asset(asset)
    project_path = paths.textures_path
    project_exists = project_path.exists()

    parent = get_main_qt_window()

    if sp.project.is_open():
        current_path = None
        try:
            current_path = sp.project.file_path()
        except Exception:
            current_path = None

        if current_path and Path(current_path).resolve() == project_path.resolve():
            sp.project.execute_when_not_busy(
                lambda: store_asset_metadata_for_project(asset)
            )
            return

        if project_exists:
            if sp.project.needs_saving() and not _confirm_discard_unsaved(parent):
                return
            sp.project.close()
            _open_existing_project(project_path)
            sp.project.execute_when_not_busy(
                lambda: store_asset_metadata_for_project(asset)
            )
            return

        if _confirm_save_as(parent, project_path):
            project_path.parent.mkdir(parents=True, exist_ok=True)
            _save_current_project_as(project_path)
            sp.project.execute_when_not_busy(
                lambda: store_asset_metadata_for_project(asset)
            )
        return

    if project_exists:
        _open_existing_project(project_path)
        sp.project.execute_when_not_busy(
            lambda: store_asset_metadata_for_project(asset)
        )
        return

    MessageDialog(
        parent,
        "No textures project exists yet. Create a new project (File > New), "
        "then run this action again to save it into the pipeline.",
        "No Project Open",
    ).exec_()


def launch_open_asset_textures() -> None:
    """Open or create the textures project for a selected asset."""

    if sp.project.is_busy():
        sp.project.execute_when_not_busy(launch_open_asset_textures)
        return

    conn = DB.Get(DB_Config)
    asset_names = conn.get_asset_name_list(sorted=True)
    dialog = SubstanceAssetDialog(get_main_qt_window(), asset_names, conn)
    if not dialog.exec_():
        return

    selection = dialog.get_selected_item()
    if not selection:
        return

    asset = conn.get_asset_by_name(selection)
    if not asset or not asset.path:
        MessageDialog(
            get_main_qt_window(),
            "The selected asset does not have a valid path in ShotGrid.",
            "Missing Asset Path",
        ).exec_()
        return

    _open_or_create_textures_project(asset)


__all__ = [
    "PIPE_SP_METADATA_CONTEXT",
    "PIPE_SP_METADATA_KEY",
    "PIPE_SP_METADATA_SCHEMA_VERSION",
    "get_asset_selection_metadata",
    "store_asset_metadata_for_project",
    "store_asset_selection_metadata",
    "launch_open_asset_textures",
]
