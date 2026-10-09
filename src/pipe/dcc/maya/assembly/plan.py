"""The Split Pieces plan for the open scene: the shared plan plus Maya's own checks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

import mayaUsd.ufe

from pipe.core.assembly.plan import Plan, Refusal
from pipe.core.assembly.plan import plan_split as plan_pieces
from pipe.core.shotgrid import Asset
from pipe.dcc.maya.assembly.scan import scan_pieces, unsplit_nodes
from pipe.dcc.maya.assembly.split import scene_units_problem
from pipe.dcc.maya.assembly.stage import stage_shape
from pipe.dcc.maya.util.materials import material_problems


def plan_split(assembly: Asset, assets: Iterable[Asset]) -> Plan:
    """What Split Pieces would do to the open scene. Reads the scene and disk only."""
    pieces = scan_pieces()
    plan = plan_pieces(pieces, assembly, assets, placed=placed_prim_names())

    refusals = list(plan.refusals)
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


def placed_prim_names() -> set[str]:
    """The pieces already in the assembly's stage, by prim name; empty before a split."""
    shape = stage_shape()
    if shape is None:
        return set()
    stage = mayaUsd.ufe.getStage(shape)
    root = stage.GetDefaultPrim() if stage is not None else None
    if not root:
        return set()
    return {prim.GetName() for prim in root.GetChildren()}
