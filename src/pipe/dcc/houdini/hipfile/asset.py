from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import hou
from pipe.core.util.paths import get_production_path

from pipe.core.asset import (
    asset_owner_for,
    houdini_asset_builder_stream,
    paths_for_asset,
)
from pipe.core.asset.create import new_asset
from pipe.core.asset.paths import BACKUP_DIRNAME
from pipe.core.ui import (
    RESTORE_CANCEL,
    RESTORE_SAVE_FIRST,
    FilteredListDialog,
    MessageDialog,
    prompt_restore_conflict,
)
from pipe.core.ui.save_version_dialog import SaveVersionDialog
from pipe.core.ui.version_browser import VersionBrowserWidget
from pipe.core.shotgrid import Asset, SGEntity, group_assets_by_subdirectory
from pipe.core.versioning import (
    VersionRecord,
    VersionStreamSpec,
    list_version_records,
    resolve_working_file_version,
    restore_version,
    restored_message,
    save_version as _save_version,
    saved_message,
)

from ..publish import nodelayouts
from .filemanager import HFileManager
from .paths import current_hip_path

log = logging.getLogger(__name__)

TURNAROUND_FRAMES = (1, 120)


class HAssetFileManager(HFileManager):
    _can_create = True

    def __init__(self) -> None:
        super().__init__(Asset)

    def _new_entity(self) -> Asset | None:
        return new_asset(self._conn, self._main_window)

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        return "asset_builder", "hipnc"

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        super()._setup_file(path, entity)
        hou.playbar.setFrameRange(*TURNAROUND_FRAMES)
        hou.playbar.setPlaybackRange(*TURNAROUND_FRAMES)
        hou.hipFile.save()

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
        assets = group_assets_by_subdirectory(self._conn.find_assets(roots_only=True))
        dialog = FilteredListDialog(
            self._main_window,
            assets,
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
            return self._conn.get_asset(display_name=selection)
        except Exception:
            log.exception("Failed to resolve selected asset: %s", selection)

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
                lambda: self._conn.get_asset(display_name=context_asset),
                lambda: self._conn.get_asset(name=context_asset),
            ):
                try:
                    return resolver()
                except Exception:
                    continue

        asset_root = hip_path.parent
        if asset_root.name == BACKUP_DIRNAME:
            asset_root = asset_root.parent

        try:
            rel_asset_path = asset_root.resolve().relative_to(get_production_path())
        except Exception:
            return None

        try:
            return self._conn.get_asset(path=rel_asset_path.as_posix())
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

        self._write_named_version(hip_path, stream)

    def _ensure_hip_saved(self) -> Path | None:
        """Prompt the artist to save unsaved changes, then return the HIP path.

        Returns None if the HIP has no path, the artist cancels, or the save
        fails.  Also validates that the file exists on disk before returning.
        """
        hip_path = current_hip_path()
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
            hip_path = current_hip_path()
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
        hip_path = current_hip_path()
        if hip_path is None:
            MessageDialog(
                self._main_window,
                "No asset HIP is open. Use Open Asset first.",
                "Version History",
            ).exec_()
            return

        resolved = self._resolve_current_stream(hip_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                "Could not resolve the current HIP to an asset. Use Open Asset first.",
                "Version History",
            ).exec_()
            return

        stream, owner_label, entity = resolved
        records = list_version_records(stream)
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
            owner_label=owner_label,
            stream_label=stream.label,
        )
        if not browser.exec_():
            return

        selected_record = browser.get_selected_record()
        selected_action = browser.get_selected_action()
        if selected_record is None:
            return

        if selected_action == VersionBrowserWidget.ACTION_RESTORE:
            self._restore_version(selected_record, stream, entity)

    def _restore_version(
        self, record: VersionRecord, stream: VersionStreamSpec, entity: SGEntity
    ) -> None:
        if self._has_unversioned_work(stream):
            choice = prompt_restore_conflict(self._main_window)
            if choice == RESTORE_CANCEL:
                return
            if choice == RESTORE_SAVE_FIRST and not self._save_named_version(stream):
                return

        try:
            working_path = restore_version(record, stream)
        except Exception as exc:
            log.exception("Failed to restore asset version.")
            MessageDialog(
                self._main_window,
                f"Failed to restore version:\n{exc}",
                "Restore Version Failed",
            ).exec_()
            return

        try:
            load_warning = self._load_hip_file(working_path)
            self._post_open_file(entity)
        except Exception as exc:
            log.exception("Restored asset version but could not open it.")
            MessageDialog(
                self._main_window,
                (
                    "Restored the version but could not open it:\n"
                    f"{self._describe_exception(exc, fallback='Could not load the HIP file')}"
                ),
                "Restore Version Failed",
            ).exec_()
            return

        if load_warning:
            self._show_hip_load_warning(
                path=working_path,
                warning=load_warning,
                title="Version Restored With Warnings",
            )
            return

        MessageDialog(
            self._main_window,
            restored_message(record),
            "Version Restored",
        ).exec_()

    def _has_unversioned_work(self, stream: VersionStreamSpec) -> bool:
        if hou.hipFile.hasUnsavedChanges():
            return True
        return resolve_working_file_version(stream) is None

    def _write_named_version(self, hip_path: Path, stream: VersionStreamSpec) -> bool:
        """Prompt for a version title and write a backup of *hip_path*."""
        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return False
        try:
            record = _save_version(
                hip_path,
                stream,
                title=dialog.get_title(),
                note=dialog.get_note(),
            )
        except Exception as exc:
            log.exception("Failed to save version.")
            MessageDialog(
                self._main_window,
                f"Failed to save version:\n{exc}",
                "Save Version Failed",
            ).exec_()
            return False

        MessageDialog(
            self._main_window,
            saved_message(record),
            "Version Saved",
        ).exec_()
        return True

    def _save_named_version(self, stream: VersionStreamSpec) -> bool:
        hip_path = self._ensure_hip_saved()
        if hip_path is None:
            return False
        return self._write_named_version(hip_path, stream)
