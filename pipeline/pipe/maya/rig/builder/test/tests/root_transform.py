from ctypes import c_float
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from maya import OpenMaya as om
from maya import cmds
from maya.api import OpenMaya as om2
from numpy.typing import NDArray

from .. import RigBuildTest
from ..common import format_max_items, get_all_visible_meshes, get_dag_path, is_control

MAX_ERROR_PERCENTAGE: float = 0.1


def _get_root_control() -> str | None:
    it = om2.MItDag(om2.MItDag.kBreadthFirst)
    while not it.isDone():
        dag: om2.MDagPath = it.getPath()
        name = dag.partialPathName()
        if not name:
            it.next()
            continue
        if is_control(name, strict=False):
            return name
        it.next()
    return None


def _get_visible_mesh_shapes() -> list[str]:
    visible_mesh_transforms = get_all_visible_meshes()
    visible_mesh_shapes: list[str] = [
        shape
        for mesh in visible_mesh_transforms
        for shape in (
            cmds.listRelatives(mesh, shapes=True, type="mesh", noIntermediate=True)
            or []
        )
    ]
    return visible_mesh_shapes


def _get_mesh_points(mesh_shape: str) -> NDArray[np.float64]:
    # Thanks to this article this isn't a buns slow loop through points to build the numpy array
    # https://python.polas.net/fast-data-conversion-from-maya-to-numpy/
    msel: om.MSelectionList = om.MSelectionList()
    msel.add(mesh_shape)
    mesh_dag: om.MDagPath = om.MDagPath()
    msel.getDagPath(0, mesh_dag)

    # make the function set and get the points
    fn_mesh: om.MFnMesh = om.MFnMesh(mesh_dag)

    mesh_points = om.MPointArray()
    fn_mesh.getPoints(mesh_points, om.MSpace.kWorld)

    num_points = mesh_points.length()
    array_size = num_points * 4
    util = om.MScriptUtil()
    util.createFromList([float()] * array_size, array_size)
    ptr = om.MScriptUtil.asFloat4Ptr(util)
    mesh_points.get(ptr)  # copy points to ptr
    # x,y,z,w per point
    c_float_array = ((c_float * 4) * num_points).from_address(int(ptr))
    np_array = np.ctypeslib.as_array(c_float_array)
    np_array = np_array.copy()
    return np_array


def _transform_mesh_points(
    points: NDArray[np.float64], matrix: om2.MMatrix
) -> NDArray[np.float64]:
    np_matrix = np.array(matrix, dtype=np.float64).reshape(4, 4)
    return points @ np_matrix


@dataclass
class ErrorMetrics:
    max_error: float
    mean_error: float
    failed_count: int
    failed_indices: NDArray[np.signedinteger]

    def __str__(self):
        string = (
            f"Max Error = {self.max_error}, Mean Error = {self.mean_error}, Failed Count = {self.failed_count}, "
            f"Failed Indices = {format_max_items(self.failed_indices)}"
        )
        return string


def _compare_points(
    current_points: NDArray[np.float64],
    expected_points: NDArray[np.float64],
    max_error_percentage: float = MAX_ERROR_PERCENTAGE,
) -> ErrorMetrics:
    diff = np.abs(current_points - expected_points)
    diff_xyz = diff[:, :3]

    # Compute mesh scale (bounding box diagonal)
    mins = np.min(expected_points[:, :3], axis=0)
    maxs = np.max(expected_points[:, :3], axis=0)
    bbox_diag = np.linalg.norm(maxs - mins)

    max_error = np.max(diff_xyz)
    mean_error = np.mean(diff_xyz)

    max_error_scaled = (max_error_percentage / 100) * bbox_diag
    failed_mask = np.any(diff_xyz > max_error_scaled, axis=1)
    failed_indices = np.where(failed_mask)[0]

    return ErrorMetrics(max_error, mean_error, int(len(failed_indices)), failed_indices)


def _compare_before_after_transform(
    control: str,
    meshes: Iterable[str],
    translation: tuple[float, float, float] | None = None,
    rotation: tuple[float, float, float] | None = None,
    scale: tuple[float, float, float] | None = None,
) -> tuple[bool, dict[str, ErrorMetrics]]:
    mesh_points_mapping = {mesh: _get_mesh_points(mesh) for mesh in meshes}

    control_dag = get_dag_path(control)
    transform_mfn = om2.MFnTransform(control_dag)
    original_world_matrix: om2.MMatrix = control_dag.inclusiveMatrix()

    original_translation = transform_mfn.translation(om2.MSpace.kTransform)
    original_rotation = transform_mfn.rotation(om2.MSpace.kTransform)
    original_scale = transform_mfn.scale()

    # Apply our transform
    if translation is not None:
        transform_mfn.translateBy(om2.MVector(translation), om2.MSpace.kTransform)
    if rotation is not None:
        transform_mfn.rotateBy(om2.MEulerRotation(rotation), om2.MSpace.kTransform)
    if scale is not None:
        transform_mfn.scaleBy(scale)

    new_world_matrix: om2.MMatrix = control_dag.inclusiveMatrix()
    offset_matrix = original_world_matrix.inverse() * new_world_matrix
    all_match = True
    mesh_error_metrics: dict[str, ErrorMetrics] = {}
    for mesh, original_points in mesh_points_mapping.items():
        # Transform the original points by the offset
        transformed_points = _transform_mesh_points(original_points, offset_matrix)
        new_points = _get_mesh_points(mesh)
        error_metrics = _compare_points(transformed_points, new_points)
        if error_metrics.failed_count > 0:
            all_match = False
            mesh_error_metrics[mesh] = error_metrics

    # Reset transform
    if translation is not None:
        transform_mfn.setTranslation(original_translation, om2.MSpace.kTransform)
    if rotation is not None:
        transform_mfn.setRotation(original_rotation, om2.MSpace.kTransform)
    if scale is not None:
        transform_mfn.setScale(original_scale)

    return all_match, mesh_error_metrics


def _error_metrics_string(error_metrics: dict[str, ErrorMetrics]):
    return ", \n".join(f"{item}: {value}" for item, value in error_metrics.items())


class TestRootTranslation(RigBuildTest):
    """
    Checks that translating the root control behaves as expected (a rigid transformation of the points).
    """

    def __init__(self):
        super().__init__("Root control translation")

    def run(self) -> bool:
        root_control = _get_root_control()
        if root_control is None:
            self.log_warn("Rig had no root control")
            return False
        visible_mesh_shapes = _get_visible_mesh_shapes()
        translation_value = (1, 1, 1)
        passed, error_metrics = _compare_before_after_transform(
            control=root_control,
            meshes=visible_mesh_shapes,
            translation=translation_value,
        )
        if not passed:
            self.log_warn(
                f"Rig did not behave properly when {root_control} was translated by {translation_value} "
                f"Problem meshes: \n{_error_metrics_string(error_metrics)}"
            )
            return False
        else:
            self.log_success()
            return True


class TestRootRotate(RigBuildTest):
    """
    Checks that rotating the root control behaves as expected (a rigid transformation of the points).
    """

    def __init__(self):
        super().__init__("Root control rotate")

    def run(self) -> bool:
        root_control = _get_root_control()
        if root_control is None:
            self.log_warn("Rig had no root control")
            return False
        visible_mesh_shapes = _get_visible_mesh_shapes()
        rotation_value = (0, 90, 0)
        passed, error_metrics = _compare_before_after_transform(
            control=root_control,
            meshes=visible_mesh_shapes,
            rotation=rotation_value,
        )
        if not passed:
            self.log_warn(
                f"Rig did not behave properly when {root_control} was rotated by {rotation_value} "
                f"Problem meshes: \n{_error_metrics_string(error_metrics)}"
            )
            return False
        else:
            self.log_success()
            return True


class TestRootScale(RigBuildTest):
    """
    Checks that scaling the root control behaves as expected (a rigid transformation of the points).
    """

    def __init__(self):
        super().__init__("Root control scale")

    def run(self) -> bool:
        root_control = _get_root_control()
        if root_control is None:
            self.log_warn("Rig had no root control")
            return False
        visible_mesh_shapes = _get_visible_mesh_shapes()
        scale_value = (5, 5, 5)
        passed, error_metrics = _compare_before_after_transform(
            control=root_control, meshes=visible_mesh_shapes, scale=scale_value
        )
        if not passed:
            self.log_warn(
                f"Rig did not behave properly when {root_control} was scaled by {scale_value} "
                f"Problem meshes: \n{_error_metrics_string(error_metrics)}"
            )
            return False
        else:
            self.log_success()
            return True
