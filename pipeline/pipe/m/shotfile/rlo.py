import logging
from pathlib import Path

from pipe.glui.dialogs import MessageDialogCustomButtons
from pipe.shot.version_adapter import (
    maya_rlo_stream,
    path_matches_stream,
    shot_owner_for,
)
from pipe.struct.db import SGEntity, Shot
from pipe.versioning import VersionStreamSpec

from .shotfile_manager import MShotFileManager

log = logging.getLogger(__name__)


class MRLOShotFileManager(MShotFileManager):
    def __init__(self):
        super().__init__(version_glob="{}*.{}", version_msg="Open alt version")

    @staticmethod
    def _check_unsaved_changes() -> bool:
        return True

    def _get_subpath(self) -> str:
        return "rlo"

    def _setup_scene(self) -> None:
        self._import_env()

    def _setup_file(self, path: Path, entity: SGEntity) -> None:
        if not path.exists():
            prompt_create = MessageDialogCustomButtons(
                self._main_window,
                f"The RLO file for shot {entity.code} does not exist. Continue "
                "to save a copy of the current file as the RLO file?",
                has_cancel_button=True,
                ok_name="Continue",
                cancel_name="Cancel",
            )
            if not bool(prompt_create.exec_()):
                return
        super()._setup_file(path, entity)

    def _resolve_current_rlo_stream(
        self,
        scene_path: Path,
    ) -> tuple[Shot, VersionStreamSpec] | None:
        shot = self._resolve_shot_for_scene(scene_path)
        if shot is None:
            return None

        stream = maya_rlo_stream(shot, owner=shot_owner_for(shot))
        if not path_matches_stream(scene_path, stream):
            return None
        return shot, stream

    def _entity_label(self) -> str:
        return "RLO"

    def _resolve_current_stream(
        self, scene_path: Path
    ) -> tuple[VersionStreamSpec, str, Shot] | None:
        result = self._resolve_current_rlo_stream(scene_path)
        if result is None:
            return None
        shot, stream = result
        return stream, shot.code, shot
