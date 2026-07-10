from __future__ import annotations

from typing import TypeVar

from pipe.dcc.maya.command import maya_command
from pipe.dcc.maya.runtime import get_main_qt_window
from pipe.dcc.maya.playblast.turnaround import (
    AnimTurnaroundDialog,
    AssetTurnaroundDialog,
)

_D = TypeVar("_D", bound=AssetTurnaroundDialog)

_dialog: AssetTurnaroundDialog | None = None
_anim_dialog: AnimTurnaroundDialog | None = None


def _replace_dialog(
    existing: AssetTurnaroundDialog | None, dialog_class: type[_D]
) -> _D:
    if existing is not None:
        try:
            existing.close()
            existing.deleteLater()
        except Exception:
            pass

    dialog = dialog_class(get_main_qt_window())
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


@maya_command(
    name="turnaround",
    label="Turnaround",
    category="modeling",
    icon="turnaround.svg",
)
def show_turnaround_dialog() -> AssetTurnaroundDialog:
    """Open the Maya asset turnaround dialog and keep a module-level reference."""

    global _dialog
    _dialog = _replace_dialog(_dialog, AssetTurnaroundDialog)
    return _dialog


@maya_command(
    name="anim_turnaround",
    label="Turnaround",
    category="animation",
    icon="turnaround.svg",
)
def show_anim_turnaround_dialog() -> AnimTurnaroundDialog:
    """Open the animation (scratch scene) turnaround dialog and keep a
    module-level reference."""

    global _anim_dialog
    _anim_dialog = _replace_dialog(_anim_dialog, AnimTurnaroundDialog)
    return _anim_dialog


class Turnaround:
    """Backward-compatible shelf wrapper for the asset turnaround dialog."""

    def __init__(self) -> None:
        self.dialog = show_turnaround_dialog()


__all__ = ["Turnaround", "show_anim_turnaround_dialog", "show_turnaround_dialog"]
