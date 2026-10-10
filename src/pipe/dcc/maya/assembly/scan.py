"""Reading the assembly scene: its pieces, where they stand, and what stops a split."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

import maya.api.OpenMaya as om
from maya import cmds as mc
from pxr import Gf

from pipe.core.assembly.model import Piece
from pipe.core.assembly.normalize import SOURCE_LAYER_LINEAR_UNIT, SOURCE_LAYER_UP_AXIS
from pipe.core.assembly.plan import Plan, Refusal, plan_children
from pipe.core.shotgrid import Asset
from pipe.dcc.maya.assembly.editing import open_piece
from pipe.dcc.maya.assembly.stage import find_assembly_stage, placed_prim_names
from pipe.dcc.maya.util.materials import material_problems

# Maya's startup cameras are assemblies too, and are never pieces.
_DEFAULT_CAMERAS = ("persp", "top", "front", "side")
# The group mayaUsd parks a piece under while it is open for editing.
_PULL_ROOT = "__mayaUsd__"


def plan_split(assembly: Asset, assets: Iterable[Asset]) -> Plan:
    """What Split Pieces would do to the open scene. Reads the scene and disk only."""
    pieces = scan_pieces()
    plan = plan_children(pieces, assembly, assets, placed=placed_prim_names())

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


def scene_units_problem() -> str | None:
    """Why the scene's unit settings stop a split, or None if they do not."""
    unit = mc.currentUnit(query=True, linear=True)
    if unit != SOURCE_LAYER_LINEAR_UNIT:
        return (
            f"This scene's working unit is {unit}, and a split writes centimetres. "
            "Set the working unit to centimetres (Preferences > Settings), which "
            "moves nothing; if the assembly then looks 100x too large, scale it "
            "by 0.01 and freeze its transformations."
        )

    up_axis = str(mc.upAxis(query=True, axis=True)).upper()
    if up_axis != SOURCE_LAYER_UP_AXIS:
        return (
            f"This scene is {up_axis}-up, and a split writes {SOURCE_LAYER_UP_AXIS}-up. "
            f"Set the up axis to {SOURCE_LAYER_UP_AXIS} first (Preferences > "
            "Settings), then rotate the assembly upright and freeze its "
            "transformations."
        )
    return None


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


def scan_pieces() -> list[Piece]:
    """Return every top-level group in the scene that could become a child asset."""
    return [piece_for_node(node) for node in _candidate_groups()]


def unsplit_nodes() -> list[str]:
    """Every top-level node still holding geometry: what an assembly publish needs split."""
    return [node for node in _top_level_nodes() if renderable_meshes(node)]


def piece_for_node(node: str) -> Piece:
    """Read one group's identity and placement, without modifying it."""
    return Piece(node=node, world_matrix=world_matrix(node))


def world_point_bounds(node: str) -> Gf.Range3d:
    """World-space bound of every point under `node`."""
    bounds = Gf.Range3d()
    selection = om.MSelectionList()
    for mesh in renderable_meshes(node):
        selection.clear()
        selection.add(mesh)
        mesh_fn = om.MFnMesh(selection.getDagPath(0))
        for point in mesh_fn.getPoints(om.MSpace.kWorld):
            bounds.UnionWith(Gf.Vec3d(point.x, point.y, point.z))
    return bounds


def renderable_meshes(node: str) -> list[str]:
    """Return the mesh shapes under `node` that a USD export would write."""
    meshes = mc.listRelatives(node, allDescendents=True, type="mesh", fullPath=True)
    if not meshes:
        # `mc.ls` with nothing to filter would list the whole scene instead.
        return []
    return cast(list[str], mc.ls(*meshes, noIntermediate=True, long=True) or [])


def world_matrix(node: str) -> Gf.Matrix4d:
    """Return `node`'s world matrix in USD's row-vector convention, which Maya shares."""
    # `mc.xform` is typed as the union of every shape its flags can return.
    values = cast(list[float], mc.xform(node, query=True, matrix=True, worldSpace=True))
    return Gf.Matrix4d(*values)


def _candidate_groups() -> list[str]:
    groups: list[str] = []
    for node in _top_level_nodes():
        if mc.listRelatives(node, shapes=True, fullPath=True):
            continue
        if not renderable_meshes(node):
            continue
        groups.append(node)
    return groups


def _top_level_nodes() -> list[str]:
    return [
        node
        for node in mc.ls(assemblies=True, long=True) or []
        if node.rsplit("|", 1)[-1] not in (*_DEFAULT_CAMERAS, _PULL_ROOT)
    ]
