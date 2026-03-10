from __future__ import annotations

import logging
from pathlib import Path

import hou
from env_sg import DB_Config

import pipe.h
from pipe.db import DB
from pipe.glui.dialogs import MessageDialog
from pipe.struct.db import SGEntity
from pipe.util import FileManager

log = logging.getLogger(__name__)


class HFileManager(FileManager):
    def __init__(
        self,
        entity_type: type[SGEntity],
        versioning: bool = False,
        version_glob: str = "",
    ) -> None:
        conn = DB.Get(DB_Config)
        window = pipe.h.local.get_main_qt_window()
        super().__init__(
            conn, entity_type, window, versioning=versioning, version_glob=version_glob
        )

    @staticmethod
    def _check_unsaved_changes() -> bool:
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
