from __future__ import annotations

from dcc.maya.command import maya_command
from dcc.maya.runtime import get_main_qt_window
from dcc.maya.playblast.turnaround import AssetTurnaroundDialog

_dialog: AssetTurnaroundDialog | None = None


@maya_command(
    name="turnaround",
    label="Turnaround",
    category="modeling",
    icon="turnaround.svg",
)
def show_turnaround_dialog() -> AssetTurnaroundDialog:
    """Open the Maya asset turnaround dialog and keep a module-level reference."""

    global _dialog
    parent = get_main_qt_window()

    if _dialog is not None:
        try:
            _dialog.close()
            _dialog.deleteLater()
        except Exception:
            pass

    _dialog = AssetTurnaroundDialog(parent)
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
    return _dialog


class Turnaround:
    """Backward-compatible shelf wrapper for the asset turnaround dialog."""

    def __init__(self) -> None:
        self.dialog = show_turnaround_dialog()


__all__ = ["Turnaround", "show_turnaround_dialog"]
