"""The Split Pieces plan for the open scene: the shared plan plus Maya's own checks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from pipe.core.assembly.plan import Plan, Refusal
from pipe.core.assembly.plan import plan_split as plan_pieces
from pipe.core.shotgrid import Asset
from pipe.dcc.maya.assembly.editing import open_piece
from pipe.dcc.maya.assembly.scan import scan_pieces, unsplit_nodes
from pipe.dcc.maya.assembly.split import scene_units_problem
from pipe.dcc.maya.assembly.stage import find_assembly_stage
from pipe.dcc.maya.util.materials import material_problems


def plan_split(assembly: Asset, assets: Iterable[Asset]) -> Plan:
    """What Split Pieces would do to the open scene. Reads the scene and disk only."""
    pieces = scan_pieces()
    plan = plan_pieces(pieces, assembly, assets, placed=placed_prim_names())

    refusals = list(plan.refusals)
    editing = _open_piece_problem()
    if editing is not None:
        refusals.append(Refusal(None, editing))
    units = scene_units_problem()
    if units is not None:
        refusals.append(Refusal(None, units))

    grouped = {piece.node for piece in pieces}
    for node in unsplit_nodes():
        if node in grouped:
            continue
        name = node.rsplit("|", 1)[-1]
        refusals.append(
            Refusal(
                name,
                f"'{name}' is geometry outside any group, and a piece is a group. "
                "Group it, or put it inside the piece it belongs to.",
            )
        )

    for piece in pieces:
        for problem in material_problems([piece.node]):
            refusals.append(Refusal(piece.name, f"'{piece.name}': {problem}"))

    return replace(plan, refusals=tuple(refusals))


def _open_piece_problem() -> str | None:
    """A split saves the scene and empties the undo queue, which an open piece
    (whose Maya copy is the only one of its edits) must not be caught in."""
    stage = find_assembly_stage()
    piece = open_piece(stage) if stage is not None else None
    if piece is None:
        return None
    return (
        f"'{piece.GetName()}' is open for editing. Save or discard its edits, "
        "then split again."
    )


def placed_prim_names() -> set[str]:
    """The pieces already in the assembly's stage, by prim name; empty before a split."""
    stage = find_assembly_stage()
    root = stage.GetDefaultPrim() if stage is not None else None
    if not root:
        return set()
    return {prim.GetName() for prim in root.GetChildren()}
