"""What an assembly publishes from Maya: its pieces, and a flat mesh for Painter."""

from __future__ import annotations

from pathlib import Path

from pxr import Usd

from pipe.core.assembly.model import AssemblyError
from pipe.core.assembly.pieces import pieces_layer_for
from pipe.dcc.maya.assembly.editing import open_piece
from pipe.dcc.maya.assembly.scan import unsplit_nodes
from pipe.dcc.maya.assembly.stage import find_assembly_stage
from pipe.dcc.maya.util.usd_export import export_layer


def publishable_stage() -> Usd.Stage | None:
    """The working stage of an assembly ready to publish, or None for a component.

    An assembly publishes only once every piece is split and none is open for
    editing (ADR-0032); the refusal says which pieces stand in the way.
    """
    stage = find_assembly_stage()
    if stage is None:
        return None

    editing = open_piece(stage)
    if editing is not None:
        raise AssemblyError(
            f"'{editing.GetName()}' is open for editing. Save or discard its edits, "
            "then publish again."
        )

    unsplit = [node.rsplit("|", 1)[-1] for node in unsplit_nodes()]
    if unsplit:
        raise AssemblyError(
            f"{len(unsplit)} pieces are not split yet ({', '.join(unsplit)}), and an "
            "assembly publishes only once every piece is split. Split them, or group "
            "leftover geometry into a piece and split that."
        )
    return stage


def export_assembly(stage: Usd.Stage, *, pieces: Path, mesh: Path) -> None:
    """Write the pieces layer for the builder and the whole assembly, flat, for Painter."""
    export_layer(pieces_layer_for(stage), pieces)
    export_layer(stage.Flatten(), mesh)
