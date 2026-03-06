from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import hou
from shared.util import get_production_path

from pipe.asset.paths import BACKUP_DIRNAME, DCC_HOUDINI, paths_for_asset
from pipe.asset.version_service import (
    list_version_records,
    promote_version,
)
from pipe.asset.version_service import (
    save_version as save_version_record,
)
from pipe.db import DBInterface
from pipe.glui.dialogs import FilteredListDialog, MessageDialog
from pipe.glui.save_version_dialog import PromoteVersionDialog, SaveVersionDialog
from pipe.glui.version_browser import VersionBrowserWidget
from pipe.struct.db import Asset, SGEntity

from .. import nodelayouts
from .filemanager import HFileManager

log = logging.getLogger(__name__)


def _version_label(version: int | None) -> str:
    if version is None:
        return "unknown version"
    return f"v{version:03d}"


class HAssetFileManager(HFileManager):
    def __init__(self) -> None:
        super().__init__(Asset)

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

    @staticmethod
    def _current_hip_path() -> Path | None:
        hip_raw = (hou.hipFile.path() or "").strip()
        if not hip_raw:
            return None

        hip_path = Path(hou.expandString(hip_raw)).expanduser()
        if not hip_path.is_absolute():
            hip_path = (Path(hou.hscriptStringExpression("$HIP")) / hip_path).resolve()
        else:
            hip_path = hip_path.resolve()
        return hip_path

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

    def _ensure_hip_saved(self) -> Path | None:
        hip_path = self._current_hip_path()
        if hip_path is None:
            MessageDialog(
                self._main_window,
                "Current HIP has no file path. Save the project before creating a version.",
                "Save Required",
            ).exec_()
            return None

        if hou.hipFile.hasUnsavedChanges():
            response = hou.ui.displayMessage(
                "The current HIP has unsaved changes. Save before creating a version?",
                buttons=("Save", "Cancel"),
                severity=hou.severityType.ImportantMessage,
                default_choice=0,
                close_choice=1,
            )
            if response != 0:
                return None
            try:
                hou.hipFile.save()
            except Exception:
                log.exception("Failed to save HIP before creating version.")
                MessageDialog(
                    self._main_window,
                    "Failed to save the current HIP. Resolve file issues and try again.",
                    "Save Failed",
                ).exec_()
                return None
            hip_path = self._current_hip_path()
            if hip_path is None:
                MessageDialog(
                    self._main_window,
                    "Could not resolve HIP path after save.",
                    "Save Failed",
                ).exec_()
                return None

        if not hip_path.exists() or not hip_path.is_file():
            MessageDialog(
                self._main_window,
                f"HIP file does not exist on disk:\n{hip_path}",
                "Invalid HIP Path",
            ).exec_()
            return None
        return hip_path

    def open_version_browser(self) -> None:
        if not self._check_unsaved_changes():
            return

        hip_path = self._current_hip_path()
        if hip_path is None:
            MessageDialog(
                self._main_window,
                "No valid asset HIP is open. Use Open Asset first.",
                "Version History",
            ).exec_()
            return

        asset = self._resolve_asset_for_hip(hip_path)
        if not asset:
            MessageDialog(
                self._main_window,
                "Could not resolve the current HIP to a valid asset. Use Open Asset first.",
                "Version History",
            ).exec_()
            return

        asset_paths = paths_for_asset(asset)
        records = list_version_records(
            asset_paths=asset_paths,
            dcc=DCC_HOUDINI,
            stem="asset_builder",
            ext="hipnc",
        )
        if not records:
            MessageDialog(
                self._main_window,
                "No version history was found for this asset.",
                "No Versions",
            ).exec_()
            return

        browser = VersionBrowserWidget(
            self._main_window,
            records,
            asset_label=asset.display_name or asset.name or "Asset",
        )
        if not browser.exec_():
            return

        selected_record = browser.get_selected_record()
        selected_action = browser.get_selected_action()
        if selected_record is None:
            return

        if selected_action == VersionBrowserWidget.ACTION_OPEN:
            backup_path = selected_record.backup_path
            if backup_path is None:
                MessageDialog(
                    self._main_window,
                    "The selected version has no backup file path.",
                    "Open Version Failed",
                ).exec_()
                return

            try:
                hou.hipFile.load(str(backup_path), suppress_save_prompt=True)
                self._post_open_file(asset)
            except Exception as exc:
                log.exception("Failed to open Houdini backup version: %s", backup_path)
                MessageDialog(
                    self._main_window,
                    f"Failed to open selected version:\n{exc}",
                    "Open Version Failed",
                ).exec_()
            return

        if selected_action == VersionBrowserWidget.ACTION_PROMOTE:
            promote_dialog = PromoteVersionDialog(self._main_window, selected_record)
            if not promote_dialog.exec_():
                return
            try:
                promoted_record = promote_version(
                    selected_record,
                    asset_paths=asset_paths,
                    dcc=DCC_HOUDINI,
                    stem="asset_builder",
                    ext="hipnc",
                    title=promote_dialog.get_title(),
                    note=promote_dialog.get_note(),
                )
            except Exception as exc:
                log.exception("Failed to promote Houdini version.")
                MessageDialog(
                    self._main_window,
                    f"Failed to promote version:\n{exc}",
                    "Promote Version Failed",
                ).exec_()
                return

            MessageDialog(
                self._main_window,
                (
                    f'Promoted version to {_version_label(promoted_record.version)} '
                    f'"{promoted_record.title or "(untitled)"}".'
                ),
                "Version Promoted",
            ).exec_()

    def save_version(self) -> None:
        hip_path = self._ensure_hip_saved()
        if hip_path is None:
            return

        asset = self._resolve_asset_for_hip(hip_path)
        if asset is None:
            asset = self._prompt_asset_selection()
        if not asset:
            return

        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return

        try:
            version_record = save_version_record(
                source_path=hip_path,
                asset_paths=paths_for_asset(asset),
                dcc=DCC_HOUDINI,
                stem="asset_builder",
                ext="hipnc",
                title=dialog.get_title(),
                note=dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to save Houdini version.")
            MessageDialog(
                self._main_window,
                f"Failed to save version:\n{exc}",
                "Save Version Failed",
            ).exec_()
            return

        MessageDialog(
            self._main_window,
            (
                f'Saved {_version_label(version_record.version)} '
                f'"{version_record.title or "(untitled)"}".'
            ),
            "Version Saved",
        ).exec_()
