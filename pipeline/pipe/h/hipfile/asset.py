from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import hou
from shared.util import get_production_path

from pipe.asset.paths import BACKUP_DIRNAME, paths_for_asset
from pipe.asset.version_adapter import (
    asset_owner_for,
    houdini_asset_builder_stream,
)
from pipe.db import DBInterface
from pipe.glui.dialogs import FilteredListDialog, MessageDialog
from pipe.struct.db import Asset, SGEntity
from pipe.versioning import VersionStreamSpec

from .. import nodelayouts
from .filemanager import HFileManager

log = logging.getLogger(__name__)


class HAssetFileManager(HFileManager):
    def __init__(self) -> None:
        super().__init__(Asset)

    def _entity_label(self) -> str:
        return "asset"

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        return "asset_builder", "hipnc"

    def _post_open_file(self, entity: SGEntity) -> None:
        asset = cast(Asset, entity)
        asset_name = (
            (asset.name or "").strip()
            or (asset.display_name or "").strip()
            or (Path(asset.asset_path).name if asset.asset_path else "")
        )

        if asset_name:
            hou.setContextOption("ASSET", asset_name)
        else:
            log.warning("Unable to set ASSET context option; asset name missing")

        try:
            nodelayouts.ensure_managed_skd_component_builder()
        except Exception:
            log.exception("Failed to ensure SKD Component Builder for %s", asset_name)

    def _prompt_asset_selection(self) -> Asset | None:
        asset_codes = self._conn.get_entity_code_list(
            Asset,
            sorted=True,
            child_mode=DBInterface.ChildQueryMode.ROOTS,
        )
        dialog = FilteredListDialog(
            self._main_window,
            asset_codes,
            "Select Asset",
            "Select the asset to browse versions.",
            accept_button_name="Select",
        )
        if not dialog.exec_():
            return None

        selection = dialog.get_selected_item()
        if not selection:
            return None

        try:
            entity = self._conn.get_entity_by_code(Asset, selection)
        except Exception:
            log.exception("Failed to resolve selected asset: %s", selection)
            entity = None

        if isinstance(entity, Asset):
            return entity

        MessageDialog(
            self._main_window,
            "Could not resolve the selected asset in ShotGrid.",
            "Asset Not Found",
        ).exec_()
        return None

    def _resolve_asset_for_hip(self, hip_path: Path) -> Asset | None:
        try:
            context_asset = str(hou.contextOption("ASSET")).strip()
        except Exception:
            context_asset = ""

        if context_asset:
            for resolver in (
                lambda: self._conn.get_entity_by_code(Asset, context_asset),
                lambda: self._conn.get_asset_by_display_name(context_asset),
                lambda: self._conn.get_asset_by_name(context_asset),
            ):
                try:
                    resolved = resolver()
                except Exception:
                    continue
                if isinstance(resolved, Asset):
                    return resolved

        asset_root = hip_path.parent
        if asset_root.name == BACKUP_DIRNAME:
            asset_root = asset_root.parent

        try:
            rel_asset_path = asset_root.resolve().relative_to(get_production_path())
        except Exception:
            return None

        try:
            return self._conn.get_asset_by_attr("path", rel_asset_path.as_posix())
        except Exception:
            return None

    def _resolve_current_stream(
        self, hip_path: Path
    ) -> tuple[VersionStreamSpec, str, SGEntity] | None:
        asset = self._resolve_asset_for_hip(hip_path)
        if asset is None:
            return None
        stream = houdini_asset_builder_stream(
            paths_for_asset(asset), owner=asset_owner_for(asset)
        )
        return stream, asset.display_name or asset.name or "Asset", asset

    def save_version(self) -> None:
        hip_path = self._ensure_hip_saved()
        if hip_path is None:
            return

        resolved = self._resolve_current_stream(hip_path)
        if resolved is None:
            # The HIP isn't linked to a known asset context; let the artist pick.
            asset = self._prompt_asset_selection()
            if not asset:
                return
            stream: VersionStreamSpec = houdini_asset_builder_stream(
                paths_for_asset(asset), owner=asset_owner_for(asset)
            )
        else:
            stream, _, _ = resolved

        self._do_save_version(hip_path, stream)
