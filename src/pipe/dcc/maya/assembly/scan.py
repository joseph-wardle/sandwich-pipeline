"""Finding the pieces in the assembly's DAG, and reading where they stand."""

from __future__ import annotations

from typing import cast

import maya.api.OpenMaya as om
from maya import cmds as mc
from pxr import Gf

from pipe.core.assembly.model import Piece

# Maya's startup cameras are assemblies too, and are never pieces.
_DEFAULT_CAMERAS = ("persp", "top", "front", "side")


def scan_pieces() -> list[Piece]:
    """Return every top-level group in the scene that could become a child asset."""
    return [piece_for_node(node) for node in _candidate_groups()]


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
    for node in mc.ls(assemblies=True, long=True) or []:
        if node.rsplit("|", 1)[-1] in _DEFAULT_CAMERAS:
            continue
        if mc.listRelatives(node, shapes=True, fullPath=True):
            continue
        if not renderable_meshes(node):
            continue
        groups.append(node)
    return groups
