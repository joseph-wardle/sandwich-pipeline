"""The Assets shelf's assembly buttons: Split Pieces..., Edit Piece, Save Piece."""

from __future__ import annotations

import platform

import mayaUsd.ufe
import ufe
from env_sg import DB_Config
from pxr import Usd

from pipe.core.assembly.model import AssemblyError, EditError
from pipe.core.shotgrid import ShotGrid
from pipe.core.ui import MessageDialog
from pipe.dcc.maya.assembly.editing import edit_piece, save_piece
from pipe.dcc.maya.assembly.split_dialog import SplitDialog
from pipe.dcc.maya.assembly.stage import find_assembly_stage
from pipe.dcc.maya.assetfile import scene_asset
from pipe.dcc.maya.command import maya_command
from pipe.dcc.maya.runtime import get_main_qt_window

_dialog: SplitDialog | None = None


@maya_command(name="split_pieces", label="Split Pieces...", category="assembly")
def split_pieces() -> None:
    """Open the Split Pieces plan for the assembly in the open scene (Linux only)."""
    global _dialog
    window = get_main_qt_window()
    if platform.system() == "Windows":
        MessageDialog(
            window,
            "Splitting runs on Linux: a piece's texture links cannot be made from "
            "Windows. Open the assembly on a Linux machine.",
            "Split Pieces",
        ).exec_()
        return
    conn = ShotGrid.connect(DB_Config)
    assembly = scene_asset(conn)
    if assembly is None:
        MessageDialog(
            window,
            "This scene is not an asset's model file. Open the assembly with "
            "Open Asset, then try again.",
            "Split Pieces",
        ).exec_()
        return
    if _dialog is not None:
        _dialog.close()
        _dialog.deleteLater()
    _dialog = SplitDialog(window, conn, assembly)
    _dialog.show()


@maya_command(name="edit_piece", label="Edit Piece", category="assembly")
def edit_selected_piece() -> None:
    """Pull the selected piece into Maya to be modelled."""
    try:
        edit_piece(_selected_prim())
    except AssemblyError as error:
        MessageDialog(get_main_qt_window(), str(error), "Edit Piece").exec_()


@maya_command(name="save_piece", label="Save Piece", category="assembly")
def save_open_piece() -> None:
    """Write the open piece's edits into its child asset's layer."""
    try:
        stage = find_assembly_stage()
        if stage is None:
            raise EditError(
                "This scene has no assembly stage, so no piece is open for editing."
            )
        save_piece(stage)
    except AssemblyError as error:
        MessageDialog(get_main_qt_window(), str(error), "Save Piece").exec_()


def _selected_prim() -> Usd.Prim:
    items = list(ufe.GlobalSelection.get())
    prim = (
        mayaUsd.ufe.ufePathToPrim(ufe.PathString.string(items[0].path()))
        if len(items) == 1
        else None
    )
    if not prim:
        raise EditError(
            "Select one piece in the assembly's stage, in the Outliner or the "
            "viewport, then press Edit Piece."
        )
    return prim
