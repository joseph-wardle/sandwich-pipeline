from __future__ import annotations

import logging
from pathlib import Path

import hou
from env_sg import DB_Config

from pipe.dcc.houdini import runtime as houdini_runtime
from pipe.dcc.houdini.hipfile.paths import current_hip_path
from pipe.core.ui import (
    RESTORE_CANCEL,
    RESTORE_SAVE_FIRST,
    MessageDialog,
    prompt_restore_conflict,
)
from pipe.core.ui.save_version_dialog import SaveVersionDialog
from pipe.core.ui.version_browser import VersionBrowserWidget
from pipe.core.shotgrid import SGEntity, ShotGrid
from pipe.core.util import FileManager
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

log = logging.getLogger(__name__)


class HFileManager(FileManager):
    def __init__(
        self,
        entity_type: type[SGEntity],
        versioning: bool = False,
        version_glob: str = "",
    ) -> None:
        conn = ShotGrid.connect(DB_Config)
        window = houdini_runtime.get_main_qt_window()
        super().__init__(
            conn, entity_type, window, versioning=versioning, version_glob=version_glob
        )

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    def _resolve_current_stream(
        self, hip_path: Path
    ) -> tuple[VersionStreamSpec, str, SGEntity] | None:
        """Return (stream, owner_label, entity) for the current HIP, or None.

        Subclasses must override this to resolve the versioning stream that
        corresponds to the open HIP file.  ``owner_label`` is displayed in the
        version browser header.  ``entity`` is passed to ``_post_open_file``
        after opening a backup version.
        """
        raise NotImplementedError

    def _entity_label(self) -> str:
        """Human-readable noun for the entity kind managed by this class.

        Used in dialog messages, e.g. ``"asset"``, ``"set"``, ``"shot"``.
        """
        return "file"

    # ------------------------------------------------------------------
    # Shared HIP helpers
    # ------------------------------------------------------------------

    def _check_unsaved_changes(self) -> bool:
        if hou.hipFile.hasUnsavedChanges():
            warning_response = hou.ui.displayMessage(
                "The current file has not been saved. Continue anyways?",
                buttons=("Continue", "Cancel"),
                severity=hou.severityType.ImportantMessage,
                default_choice=1,
            )
            if warning_response == 1:
                return False
        return True

    @staticmethod
    def _describe_exception(exc: BaseException, *, fallback: str) -> str:
        message = str(exc).strip()
        if message:
            return message
        return f"{fallback} ({type(exc).__name__})"

    def _load_hip_file(self, path: Path) -> str | None:
        try:
            hou.hipFile.load(str(path), suppress_save_prompt=True)
        except hou.LoadWarning as exc:
            return self._describe_exception(
                exc,
                fallback="Houdini reported load warnings while opening the HIP file",
            )
        return None

    def _show_hip_load_warning(
        self,
        *,
        path: Path,
        warning: str,
        title: str = "Open Warning",
    ) -> None:
        MessageDialog(
            self._main_window,
            f"Opened HIP with warnings:\n{path}\n\n{warning}",
            title,
        ).exec_()

    def _open_file(self, path: Path) -> None:
        warning = self._load_hip_file(path)
        if warning:
            self._show_hip_load_warning(path=path, warning=warning)

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        hou.hipFile.clear(suppress_save_prompt=True)
        hou.hipFile.save(str(path))

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

    # ------------------------------------------------------------------
    # Shared version browser and save
    # ------------------------------------------------------------------

    def open_version_browser(self) -> None:
        kind = self._entity_label()
        hip_path = current_hip_path()
        if hip_path is None:
            MessageDialog(
                self._main_window,
                f"No valid {kind} HIP is open. Use Open {kind[0].upper() + kind[1:]} first.",
                "Version History",
            ).exec_()
            return

        resolved = self._resolve_current_stream(hip_path)
        if resolved is None:
            MessageDialog(
                self._main_window,
                f"Could not resolve the current HIP to a valid {kind}. "
                f"Use Open {kind[0].upper() + kind[1:]} first.",
                "Version History",
            ).exec_()
            return

        stream, owner_label, entity = resolved
        records = list_version_records(stream)
        if not records:
            MessageDialog(
                self._main_window,
                f"No version history was found for this {kind}.",
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
        kind = self._entity_label()
        if self._has_unversioned_work(stream):
            choice = prompt_restore_conflict(self._main_window)
            if choice == RESTORE_CANCEL:
                return
            if choice == RESTORE_SAVE_FIRST and not self._save_named_version(stream):
                return

        try:
            working_path = restore_version(record, stream)
        except Exception as exc:
            log.exception("Failed to restore %s version.", kind)
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
            log.exception("Restored %s version but could not open it.", kind)
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
