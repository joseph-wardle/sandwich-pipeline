"""
What an assembly publishes from Maya. This includes an assembled USD
reference chain, and a flat mesh for Painter.
"""

from __future__ import annotations

from pathlib import Path

from pxr import Usd, UsdGeom

from pipe.core.assembly.model import AssemblyError
from pipe.core.assembly.pieces import pieces_layer_for
from pipe.dcc.maya.assembly.editing import open_piece
from pipe.dcc.maya.assembly.scan import unsplit_nodes
from pipe.dcc.maya.assembly.stage import find_assembly_stage
from pipe.dcc.maya.util.usd_export import export_layer


def publishable_stage() -> Usd.Stage | None:
    """The working stage of an assembly ready to publish, or None for a component."""
    stage = find_assembly_stage()
    if stage is None:
        return None
    _refuse_open_piece(stage)
    _refuse_unsplit_nodes()
    _refuse_hidden_pieces(stage)
    _refuse_empty_pieces(stage)
    return stage


def _refuse_open_piece(stage: Usd.Stage) -> None:
    editing = open_piece(stage)
    if editing is not None:
        raise AssemblyError(
            f"'{editing.GetName()}' is open for editing. Save or discard its edits, "
            "then publish again."
        )


def _refuse_unsplit_nodes() -> None:
    unsplit = [node.rsplit("|", 1)[-1] for node in unsplit_nodes()]
    if unsplit:
        raise AssemblyError(
            f"{len(unsplit)} pieces are not split yet ({', '.join(unsplit)}), and an "
            "assembly publishes only once every piece is split. Split them, or group "
            "leftover geometry into a piece and split that."
        )


def _refuse_hidden_pieces(stage: Usd.Stage) -> None:
    """A deactivated or unloaded piece composes nothing, and both exports skip it."""
    pieces = stage.GetDefaultPrim().GetAllChildren()
    inactive = [piece.GetName() for piece in pieces if not piece.IsActive()]
    if inactive:
        raise AssemblyError(
            "These pieces are deactivated, so the publish would leave them out: "
            f"{', '.join(inactive)}. Right-click each in the Outliner and choose "
            "Activate Prim, then publish again."
        )
    unloaded = [piece.GetName() for piece in pieces if not piece.IsLoaded()]
    if unloaded:
        raise AssemblyError(
            "These pieces are unloaded, so the publish would leave them out: "
            f"{', '.join(unloaded)}. Right-click each in the Outliner and choose "
            "Load, then publish again."
        )


def _refuse_empty_pieces(stage: Usd.Stage) -> None:
    empty = [
        piece.GetName()
        for piece in stage.GetDefaultPrim().GetChildren()
        if not any(UsdGeom.Mesh(prim) for prim in Usd.PrimRange(piece))
    ]
    if empty:
        raise AssemblyError(
            "These pieces have no geometry, so the publish would place nothing for "
            f"them: {', '.join(empty)}. Edit each piece and give it geometry, or "
            "delete it from the stage, then publish again."
        )


def export_assembly(stage: Usd.Stage, *, pieces: Path, mesh: Path) -> None:
    """Write the whole assembly, flat, for Painter, then the pieces layer for the builder."""
    flat = stage.Flatten()
    pieces_layer = pieces_layer_for(stage)
    export_layer(flat, mesh)
    export_layer(pieces_layer, pieces)
