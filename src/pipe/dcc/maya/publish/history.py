"""A shot's anim publish versions: open the scene that made one, or make one current.

The Animation shelf's Version History calls `show`.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import maya.cmds as mc
from env_sg import DB_Config
from Qt import QtWidgets

from pipe.core.publish.history import history, move_current, replace_scene
from pipe.core.publish.target import Refused, shot_target
from pipe.core.shotgrid import ShotGrid
from pipe.core.ui import HistoryAction, MessageDialog, prompt_history
from pipe.core.ui.history_dialog import TITLE
from pipe.core.util.paths import get_production_path
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.maya.runtime import get_main_qt_window
from pipe.dcc.maya.shotfile.anim import MAnimShotFileManager

from .anim_index import DEPARTMENT, AnimStream

SHOTS_DIRNAME = "shot"


def show() -> None:
    """List this scene's anim publish versions and do what the artist picks with one."""
    window = get_main_qt_window()
    try:
        _show(window)
    except Refused as refusal:
        MessageDialog(window, str(refusal), TITLE).exec_()


def _show(window: QtWidgets.QWidget | None) -> None:
    scene, shot_code = _anim_scene()
    conn = ShotGrid.connect(DB_Config)
    shot = conn.get_shot(code=shot_code)
    # Both streams, which number their versions together.
    entries = history(
        [shot_target(shot, DEPARTMENT, stream.layer_name) for stream in AnimStream]
    )
    if not entries:
        raise Refused(f"Nothing has been published for {shot_code} {DEPARTMENT} yet.")

    choice = prompt_history(window, entries, scene.name)
    if choice is None:
        return
    action, entry = choice
    if action is HistoryAction.OPEN:
        replace_scene(entry, scene)
        MAnimShotFileManager().open_path(scene, shot)
    elif action is HistoryAction.MAKE_CURRENT:
        lines = move_current(conn, entry, author=resolve_artist_display_name())
        MessageDialog(window, "\n".join(lines), TITLE).exec_()


def _anim_scene() -> tuple[Path, str]:
    """The open scene, and the code of the shot whose anim folder holds it."""
    name = cast(str, mc.file(query=True, sceneName=True))
    scene = Path(name).resolve()
    try:
        parts = scene.relative_to(get_production_path().resolve()).parts
    except ValueError:
        parts = ()
    in_anim_folder = (
        len(parts) == 4 and parts[0] == SHOTS_DIRNAME and parts[2] == DEPARTMENT
    )
    if not name or not in_anim_folder:
        raise Refused(
            "This scene isn't in a shot's anim folder, so it has no publish "
            "versions. Open it with Open Anim File."
        )
    return scene, parts[1]
