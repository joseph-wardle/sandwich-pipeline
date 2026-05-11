from typing import Literal, Optional, Sequence, Union

import maya.api.OpenMaya as om2
import maya.cmds as cmds
from maya.api.OpenMaya import (
    MColor,
    MColorArray,
    MDagPath,
    MFnMesh,
    MMatrix,
    MMeshIntersector,
    MObject,
    MPoint,
    MPointArray,
    MPointOnMesh,
    MSelectionList,
    MSpace,
    MVector,
)

from dcc.maya.command import maya_command

from .color import lch_to_lab, oklab_to_linear_srgb
from .gradient import (
    OKLCH_HEATMAP_GRADIENT,
    Gradient,
    fast_sample_lch_gradient_as_linear_srgb,
    sample_spline_gradient,
)
from .math import remap

X_MIRROR = MMatrix(((-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)))
Y_MIRROR = MMatrix(((1, 0, 0, 0), (0, -1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)))
Z_MIRROR = MMatrix(((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, -1, 0), (0, 0, 0, 1)))


def get_shapes(transform: str) -> Optional[list[str]]:
    # list the shapes of node
    shape_list: list[str] = cmds.listRelatives(
        transform, shapes=True, noIntermediate=True, children=True
    )

    if shape_list:
        return shape_list
    else:
        return None


def get_shape(node: str) -> Optional[str]:
    if cmds.nodeType(node) == "mesh":
        return node
    else:
        shapes = get_shapes(node)
        if shapes is not None:
            return shapes[0]
        else:
            return None
    return None


def get_mirror_matrix(
    symmetry_axis: Union[Literal["x", "y", "z"], int] = "x",
) -> MMatrix:
    if symmetry_axis == "x":
        return X_MIRROR
    elif symmetry_axis == "y":
        return Y_MIRROR
    elif symmetry_axis == "z":
        return Z_MIRROR
    else:
        raise ValueError(f"{symmetry_axis} is not a valid symmetry axis (x, y, z)")


def get_closest_vertex_id(
    mesh_intersector: MMeshIntersector,
    mfn_mesh: MFnMesh,
    mesh_points: Sequence[MPoint],
    point: MPoint,
) -> int:
    closest_point: MPointOnMesh = mesh_intersector.getClosestPoint(point)
    face_vertices = mfn_mesh.getPolygonVertices(closest_point.face)

    return min(
        face_vertices,
        key=lambda vertex_id: mesh_points[vertex_id].distanceTo(point),
    )


def color_from_symmetry_error(
    mesh_transform: str,
    symmetry_axis: Union[Literal["x", "y", "z"], int] = "x",
    max_error: float = 0.01,
):
    # handle undo with janky duplication
    viz_mesh = cmds.duplicate(mesh_transform, name=f"{mesh_transform}_SYM")[0]

    shape = get_shape(viz_mesh)
    if shape is None:
        return
    msel: MSelectionList = om2.MSelectionList()
    msel.add(shape)
    shape_obj: MObject = msel.getDependNode(0)
    mfn_mesh: MFnMesh = om2.MFnMesh(shape_obj)
    mesh_intersector: MMeshIntersector = om2.MMeshIntersector().create(shape_obj)
    mesh_points: MPointArray = mfn_mesh.getPoints(MSpace.kObject)
    mirror_matrix = get_mirror_matrix(symmetry_axis)
    vertex_symmetry_error: list[float] = []
    vertex_indices: list[int] = []

    viz_errors: list[float] = []
    point: MPoint
    for i, point in enumerate(mesh_points):  # type: ignore
        mirrored_point: MPoint = point * mirror_matrix
        mirrored_position: MPoint = MPoint(
            mesh_intersector.getClosestPoint(mirrored_point).point
        )
        error_vector: MVector = mirrored_position - mirrored_point
        error: float = error_vector.length()
        remapped_error = remap(
            input=error, input_range=(0, max_error), output_range=(0, 1)
        )
        vertex_symmetry_error.append(error)
        viz_errors.append(remapped_error if remapped_error < 1 else 1)
        vertex_indices.append(i)

    vertex_colors: MColorArray = fast_sample_lch_gradient_as_linear_srgb(
        positions=viz_errors, gradient=OKLCH_HEATMAP_GRADIENT
    )

    msel.add(shape)
    # make sure the target shape can show vertex colors

    cmds.sets(viz_mesh, edit=True, forceElement="initialShadingGroup")

    cmds.setAttr(f"{shape}.displayColors", 1)  # type: ignore
    cmds.setAttr(f"{shape}.displayColorChannel", "Diffuse", type="string")

    mfn_mesh.setVertexColors(vertex_colors, vertex_indices)

    # handle undo with duplicated mesh and deletion of original
    cmds.hide(mesh_transform)
    return


@maya_command(
    name="visualize_symmetry_of_selected",
    label="Visualize Symmetry Of Selected",
    icon="symmetry.svg",
    category="asset",
)
def visualize_symmetry_of_selected():
    """
    Creates a temporary heat-map visualization of how close each point on the mesh is to being perfectly symmetrical.
    If the mesh is all black, it is perfectly symmetrical!
    """
    selection = cmds.ls(selection=True)
    for object in selection:
        if cmds.nodeType(object) == "transform":
            color_from_symmetry_error(object)
    return


def color_by_gradient(shape: str, gradient: Gradient = OKLCH_HEATMAP_GRADIENT):
    msel: MSelectionList = om2.MSelectionList()
    msel.add(shape)
    shape_dag: MDagPath = msel.getDagPath(0)
    mfn_mesh: MFnMesh = om2.MFnMesh(shape_dag)
    mesh_points: MPointArray = mfn_mesh.getPoints(MSpace.kWorld)

    vertex_indices: list[int] = []
    vertex_colors: list[MColor] = []
    point: MPoint
    for i, point in enumerate(mesh_points):  # type: ignore
        heatmap_color_oklch = sample_spline_gradient(
            gradient=gradient,
            position=point.x,
        )
        heatmap_color_oklab = lch_to_lab(heatmap_color_oklch)
        heatmap_color_linear_srgb = oklab_to_linear_srgb(heatmap_color_oklab)
        vertex_colors.append(MColor(heatmap_color_linear_srgb))
        vertex_indices.append(i)

    # make sure the target shape can show vertex colors
    cmds.setAttr(f"{shape}.displayColors", 1)  # type: ignore
    cmds.setAttr(f"{shape}.displayColorChannel", "Diffuse", type="string")

    mfn_mesh.setVertexColors(vertex_colors, vertex_indices)
    return
