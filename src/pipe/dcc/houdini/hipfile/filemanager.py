from __future__ import annotations

from pathlib import Path

import hou
from env_sg import DB_Config

from pipe.core.cache import RENDER_DIRNAME, SIM_DIRNAME, link_to_cache
from pipe.dcc.houdini import runtime as houdini_runtime
from pipe.core.ui import MessageDialog
from pipe.core.shotgrid import SGEntity, ShotGrid
from pipe.core.util import FileManager

from .upgrade import upgrade


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

    def _link_cache_dirs(self, entity: SGEntity, entity_path: Path) -> None:
        super()._link_cache_dirs(entity, entity_path)
        link_to_cache(entity_path / SIM_DIRNAME)
        # Tractor Configure renders a lighting hip into the shot's `render/`, which
        # the base class links, and every other hip into `$HIP/render`.
        if entity_path.name != "lighting":
            link_to_cache(entity_path / RENDER_DIRNAME)

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
        warning = None
        try:
            hou.hipFile.load(str(path), suppress_save_prompt=True)
        except hou.LoadWarning as exc:
            warning = self._describe_exception(
                exc,
                fallback="Houdini reported load warnings while opening the HIP file",
            )
        # For hips saved before publish versions. Delete with the module (ADR-0028).
        upgrade(self._main_window)
        return warning

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
