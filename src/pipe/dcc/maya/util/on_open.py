"""Re-runs a scene's file manager `run_on_open` after every open.

The scene names its manager in `fileInfo`, and `userSetup` registers the
callback. A Python script node in the scene would do the same job, but Maya's
Safe Mode prompts for every module such a node imports.
"""

from __future__ import annotations

import logging
from typing import Protocol

import maya.api.OpenMaya as om
from maya import cmds as mc

from pipe.dcc.maya.util.legacy_on_open import adopt_legacy_on_open_node

log = logging.getLogger(__name__)

MANAGER_FILE_INFO_KEY = "skdOnOpenManager"


class _OnOpenManager(Protocol):
    @classmethod
    def run_on_open(cls) -> None: ...


def install_on_open_callback() -> None:
    om.MSceneMessage.addCallback(om.MSceneMessage.kAfterOpen, _run_scene_manager)


def tag_on_open_manager(manager: _OnOpenManager) -> None:
    """Name `manager` as the scene's open hook, unless the scene already has one."""
    if _scene_manager_name():
        return
    mc.fileInfo(MANAGER_FILE_INFO_KEY, type(manager).__name__)
    manager.run_on_open()  # nothing named it on open, so nothing ran


def remove_on_open_manager() -> None:
    """Drop the scene's open hook, so the next manager to open the file names
    its own."""
    mc.fileInfo(remove=MANAGER_FILE_INFO_KEY)


def _scene_manager_name() -> str | None:
    values = mc.fileInfo(MANAGER_FILE_INFO_KEY, query=True)
    return values[0] if values else None


def _run_scene_manager(*_: object) -> None:
    name = _scene_manager_name()
    if name is None:
        name = adopt_legacy_on_open_node()
        if name is None:
            return
        mc.fileInfo(MANAGER_FILE_INFO_KEY, name)
    manager = _managers().get(name)
    if manager is None:
        log.warning("Scene names an unknown open hook %r; nothing ran.", name)
        return
    manager.run_on_open()


def _managers() -> dict[str, type[_OnOpenManager]]:
    # Imported here: the managers import this module to tag their scenes.
    from pipe.dcc.maya.previs.file_manager import MPrevisFileManager
    from pipe.dcc.maya.shotfile.anim import MAnimShotFileManager
    from pipe.dcc.maya.shotfile.rlo import MRLOShotFileManager

    return {
        cls.__name__: cls
        for cls in (MAnimShotFileManager, MRLOShotFileManager, MPrevisFileManager)
    }
