from __future__ import annotations

import logging
from pathlib import Path

import hou
from env_sg import DB_Config

from dcc.houdini import runtime as houdini_runtime
from core.glui.dialogs import MessageDialog
from core.glui.save_version_dialog import PromoteVersionDialog, SaveVersionDialog
from core.glui.version_browser import VersionBrowserWidget
from core.shotgrid import SGEntity, ShotGrid
from core.util import FileManager
from core.versioning import (
    VersionStreamSpec,
    list_version_records,
    promote_version as _promote_version,
    save_version as _save_version,
    version_label,
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

    @staticmethod
    def _current_hip_path() -> Path | None:
        """Return the resolved, absolute path of the current HIP file, or None."""
        hip_raw = (hou.hipFile.path() or "").strip()
        if not hip_raw:
            return None
        hip_path = Path(hou.expandString(hip_raw)).expanduser()
        if not hip_path.is_absolute():
            hip_path = (Path(hou.hscriptStringExpression("$HIP")) / hip_path).resolve()
        else:
            hip_path = hip_path.resolve()
        return hip_path

    def _ensure_hip_saved(self) -> Path | None:
        """Prompt the artist to save unsaved changes, then return the HIP path.

        Returns None if the HIP has no path, the artist cancels, or the save
        fails.  Also validates that the file exists on disk before returning.
        """
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

    # ------------------------------------------------------------------
    # Shared version browser and save
    # ------------------------------------------------------------------

    def open_version_browser(self) -> None:
        kind = self._entity_label()
        hip_path = self._current_hip_path()
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

        if selected_action == VersionBrowserWidget.ACTION_OPEN:
            backup_path = selected_record.backup_path
            if backup_path is None:
                MessageDialog(
                    self._main_window,
                    "The selected version has no backup file path.",
                    "Open Version Failed",
                ).exec_()
                return
            if not backup_path.exists() or not backup_path.is_file():
                MessageDialog(
                    self._main_window,
                    f"Backup file is missing on disk:\n{backup_path}",
                    "Open Version Failed",
                ).exec_()
                return
            if not self._check_unsaved_changes():
                return

            load_warning: str | None = None
            try:
                load_warning = self._load_hip_file(backup_path)
            except Exception as exc:
                log.exception("Failed to open HIP version: %s", backup_path)
                MessageDialog(
                    self._main_window,
                    (
                        "Failed to open selected version:\n"
                        f"{self._describe_exception(exc, fallback='Could not load the HIP file')}"
                    ),
                    "Open Version Failed",
                ).exec_()
                return

            try:
                self._post_open_file(entity)
            except Exception as exc:
                log.exception(
                    "Loaded HIP version but post-open setup failed: %s", backup_path
                )
                MessageDialog(
                    self._main_window,
                    (
                        f"The selected version loaded, but {kind} setup could not finish:\n"
                        f"{self._describe_exception(exc, fallback=f'{kind[0].upper() + kind[1:]} post-open setup failed')}"
                    ),
                    "Open Version Failed",
                ).exec_()
                return

            if load_warning:
                self._show_hip_load_warning(
                    path=backup_path,
                    warning=load_warning,
                    title="Version Opened With Warnings",
                )
            return

        if selected_action == VersionBrowserWidget.ACTION_PROMOTE:
            source_backup = selected_record.backup_path
            if source_backup is None or not source_backup.exists():
                MessageDialog(
                    self._main_window,
                    "Cannot create a new version from this entry because the backup file is missing.",
                    "Create Version Failed",
                ).exec_()
                return

            promote_dialog = PromoteVersionDialog(self._main_window, selected_record)
            if not promote_dialog.exec_():
                return
            try:
                promoted_record = _promote_version(
                    selected_record,
                    stream,
                    title=promote_dialog.get_title(),
                    note=promote_dialog.get_note(),
                )
            except Exception as exc:
                log.exception("Failed to promote version.")
                MessageDialog(
                    self._main_window,
                    f"Failed to create new version:\n{exc}",
                    "Create Version Failed",
                ).exec_()
                return

            MessageDialog(
                self._main_window,
                (
                    f"Created new version {version_label(promoted_record.version)} "
                    f'"{promoted_record.title or "(untitled)"}" from the selected backup.\n'
                    "Open it from Version History to continue working from it."
                ),
                "Version Created",
            ).exec_()

    def _do_save_version(self, hip_path: Path, stream: VersionStreamSpec) -> None:
        """Prompt for a version title and write a backup of *hip_path*."""
        dialog = SaveVersionDialog(self._main_window)
        if not dialog.exec_():
            return
        try:
            version_record = _save_version(
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
            return

        MessageDialog(
            self._main_window,
            (
                f"Saved {version_label(version_record.version)} "
                f'"{version_record.title or "(untitled)"}".'
            ),
            "Version Saved",
        ).exec_()
