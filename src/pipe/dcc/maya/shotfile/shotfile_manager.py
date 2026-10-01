from __future__ import annotations

import logging
from abc import abstractmethod
from pathlib import Path
from typing import cast

import maya.cmds as mc
from env_sg import DB_Config
from timeline_marker.ui import TimelineMarker  # type: ignore[import-not-found]

from pipe.dcc.maya.runtime import get_main_qt_window
from pipe.dcc.maya.util.on_open import install_on_open_node
from pipe.core.shotgrid import (
    SGEntity,
    Shot,
    ShotGrid,
    validate_shot_code_token,
)
from pipe.core.util import FileManager, log_errors

from .sets import sync_shot_sets
from .stage import build_shot_stage
from .timeline import shot_timeline_generator

log = logging.getLogger(__name__)


class MShotFileManager(FileManager):
    shot: Shot

    def __init__(self, **kwargs) -> None:
        conn = ShotGrid.connect(DB_Config)
        window = get_main_qt_window()
        super().__init__(conn, Shot, window, versioning=True, **kwargs)

    @classmethod
    def _shot_code_from_file_info(cls) -> str | None:
        info = mc.fileInfo("code", query=True)
        if isinstance(info, (list, tuple)):
            if not info:
                return None
            raw_value = info[0]
        elif isinstance(info, str):
            raw_value = info
        else:
            return None

        try:
            return validate_shot_code_token(raw_value)
        except ValueError:
            log.warning("Invalid shot code in scene metadata: %s", raw_value)
            return None

    @classmethod
    def _shot_code_from_scene_path(cls, scene_path: str | None) -> str | None:
        """Resolve shot code from a scene path using canonical shot folder semantics.

        Preferred source is the directory token immediately after `shot/`.
        Falls back to the scene filename stem for legacy/non-canonical paths.
        """
        if not scene_path:
            return None
        path = Path(scene_path)
        try:
            shot_index = path.parts.index("shot")
            if shot_index + 1 < len(path.parts):
                try:
                    return validate_shot_code_token(path.parts[shot_index + 1])
                except ValueError:
                    log.warning("Invalid shot token in scene path: %s", scene_path)
        except ValueError:
            pass
        stem = path.stem
        if not stem:
            return None
        try:
            return validate_shot_code_token(stem.split(".")[0])
        except ValueError:
            return None

    @classmethod
    @log_errors
    def run_on_open(cls) -> None:
        """Function to run on file open via script node"""
        # change default render resolution
        mc.setAttr("defaultResolution.width", 1920)  # type: ignore
        mc.setAttr("defaultResolution.height", 1080)  # type: ignore
        mc.setAttr("defaultResolution.pixelAspect", 1.0)  # type: ignore
        mc.setAttr("defaultResolution.deviceAspectRatio", 1920 / 1080)  # type: ignore

        try:
            shot_code = cls._shot_code_from_file_info()
            if not shot_code:
                scene_path = mc.file(query=True, sceneName=True)
                scene_path_str = scene_path if isinstance(scene_path, str) else ""
                shot_code = cls._shot_code_from_scene_path(scene_path_str)
                if shot_code:
                    mc.fileInfo("code", shot_code)
                else:
                    mc.warning(
                        "Could not determine shot code; sets and timeline not set"
                    )
                    return

            conn = ShotGrid.connect(DB_Config)
            shot = conn.get_shot(code=shot_code)
            if not mc.about(batch=True):
                sync_shot_sets(shot)

            # Import Timeline
            frames, colors, comments = shot_timeline_generator(
                shot.cut_duration or 0, shot.cut_in or 1001
            )
            TimelineMarker.clear()
            TimelineMarker.set(frames, colors, comments)
            mc.playbackOptions(
                animationStartTime=frames[0],
                animationEndTime=frames[-1],
                minTime=frames[0],
                maxTime=frames[-1],
            )
        except Exception:
            # Workflow boundary: many things can fail during file-open setup
            # (ShotGrid lookup, set sync, timeline marker). Log + warn
            # rather than crash the open.
            log.exception("run_on_open failed")
            mc.error(
                "Could not finish file-open setup. Check the script editor for details."
            )

    def _check_unsaved_changes(self) -> bool:
        if mc.file(query=True, modified=True):
            warning_response = mc.confirmDialog(
                title="Do you want to save?",
                message="The current file has not been saved. Continue anyways?",
                button=["Continue", "Cancel"],
                defaultButton="Cancel",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if warning_response == "Cancel":
                return False
        return True

    def _generate_filename_ext(self, entity) -> tuple[str, str]:
        shot = cast(Shot, entity)
        return shot.code or "", "mb"

    def _open_file(self, path: Path) -> None:
        mc.file(str(path), open=True, force=True)

    def _post_open_file(self, entity: SGEntity) -> None:
        install_on_open_node(self)

    @abstractmethod
    def _setup_scene(self) -> None:
        """Fill the new scene's stage."""
        ...

    def _setup_file(self, path: Path, entity) -> None:
        mc.file(rename=str(path))
        self.shot = cast(Shot, entity)
        build_shot_stage(self.shot)
        self._setup_scene()
        mc.file(save=True, force=True)
