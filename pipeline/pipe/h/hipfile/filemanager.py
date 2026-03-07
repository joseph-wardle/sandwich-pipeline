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
