"""A shot or set hip's publish versions: open the hip that made one, or make one current.

The shelf's Version History calls `show`. Whose versions it lists comes from where
the hip is, as it does for a publish.
"""

from __future__ import annotations

from env_sg import DB_Config
from Qt import QtWidgets

from pipe.core.publish import Refused, Target, history, move_current, replace_scene
from pipe.core.shotgrid import Set, ShotGrid
from pipe.core.ui import HistoryAction, MessageDialog, prompt_history
from pipe.core.ui.history_dialog import TITLE
from pipe.core.util.users import resolve_artist_display_name
from pipe.dcc.houdini import runtime
from pipe.dcc.houdini.hipfile import HSetFileManager, HShotFileManager
from pipe.dcc.houdini.hipfile.filemanager import HFileManager
from pipe.dcc.houdini.hipfile.paths import current_hip_path

from .version import NOT_A_PUBLISHING_HIP, hip_target


def show() -> None:
    """List this hip's publish versions and do what the artist picks with one."""
    window = runtime.get_main_qt_window()
    try:
        _show(window)
    except Refused as refusal:
        MessageDialog(window, str(refusal), TITLE).exec_()


def _show(window: QtWidgets.QWidget | None) -> None:
    hip_path = current_hip_path()
    if hip_path is None:
        raise Refused(NOT_A_PUBLISHING_HIP)
    conn = ShotGrid.connect(DB_Config)
    target = hip_target(conn, hip_path)
    entries = history([target])
    if not entries:
        raise Refused(f"Nothing has been published for {target.name} yet.")

    choice = prompt_history(window, entries, hip_path.name)
    if choice is None:
        return
    action, entry = choice
    if action is HistoryAction.OPEN:
        replace_scene(entry, hip_path)
        _manager(target).open_path(hip_path, target.entity)
    elif action is HistoryAction.MAKE_CURRENT:
        lines = move_current(conn, entry, author=resolve_artist_display_name())
        MessageDialog(window, "\n".join(lines), TITLE).exec_()


def _manager(target: Target) -> HFileManager:
    """The file manager that opens this target's hips, for what it sets up in one."""
    if isinstance(target.entity, Set):
        return HSetFileManager()
    return HShotFileManager(target.department, prompt_for_department=False)
