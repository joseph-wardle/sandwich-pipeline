"""Where a split child's geometry sits, and where the assembly puts it back.

A child's pivot should be at origin, and also not move in the assembly
Writing the child at `N` and the placement at `P` satisfies that when

    p . N . P  ==  p . W

for every point `p` of the piece, which is why `placement_for` is `N⁻¹ . W` and
nothing else. `Gf` composes for row vectors, so `A * B` reads "apply A, then B".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from pxr import Gf, Sdf, Usd, UsdGeom

SOURCE_LAYER_LINEAR_UNIT = "cm"
SOURCE_LAYER_UP_AXIS = "Y"

_UP_AXIS_INDEX = 1

_MESH_SCHEMA = "Mesh"

_SCALE_TOLERANCE = 1e-4

_RIGHT_ANGLE_TOLERANCE = 1e-4

_XFORM_OP_PREFIX = "xformOp:"


@dataclass(frozen=True)
class ScaleCheck:
    """Whether a piece's scale can be baked into its child layer."""

    factors: tuple[float, float, float]
    uniform: bool
    positive: bool
    orthogonal: bool

    @property
    def bakeable(self) -> bool:
        return self.uniform and self.positive and self.orthogonal

    @property
    def factor(self) -> float:
        return self.factors[0]


def inspect_scale(world_matrix: Gf.Matrix4d) -> ScaleCheck:
    """Decompose the scale of `world_matrix` and report whether it can be baked."""
    rows = [world_matrix.GetRow3(i) for i in range(3)]
    factors = (
        float(rows[0].GetLength()),
        float(rows[1].GetLength()),
        float(rows[2].GetLength()),
    )

    positive = min(factors) > _SCALE_TOLERANCE and world_matrix.GetDeterminant() > 0.0
    uniform = positive and (max(factors) - min(factors)) <= _SCALE_TOLERANCE * max(
        factors
    )

    orthogonal = positive and all(
        abs(Gf.Dot(rows[i].GetNormalized(), rows[j].GetNormalized()))
        <= _RIGHT_ANGLE_TOLERANCE
        for i, j in ((0, 1), (0, 2), (1, 2))
    )

    return ScaleCheck(
        factors=factors, uniform=uniform, positive=positive, orthogonal=orthogonal
    )


def base_centre(bbox: Gf.Range3d) -> Gf.Vec3d:
    """Return the point a child's origin is moved to: centred, resting on the ground."""
    centre = bbox.GetMidpoint()
    centre[_UP_AXIS_INDEX] = bbox.GetMin()[_UP_AXIS_INDEX]
    return centre


def normalization_matrix(bbox: Gf.Range3d, scale: float) -> Gf.Matrix4d:
    """Return `N`: the transform the child layer's contents are written under."""
    return Gf.Matrix4d().SetTranslate(-base_centre(bbox)) * Gf.Matrix4d().SetScale(
        Gf.Vec3d(scale, scale, scale)
    )


def placement_for(world_matrix: Gf.Matrix4d, normalization: Gf.Matrix4d) -> Gf.Matrix4d:
    """Return `P`: the transform the assembly authors on the piece prim."""
    return normalization.GetInverse() * world_matrix


def clear_transform(spec: Sdf.PrimSpec) -> bool:
    """Remove every transform op authored on `spec`, reporting whether there was one."""
    ops = [
        prop
        for prop in spec.properties
        if prop.name == UsdGeom.Tokens.xformOpOrder
        or prop.name.startswith(_XFORM_OP_PREFIX)
    ]
    for prop in ops:
        spec.RemoveProperty(prop)
    return bool(ops)


def prim_point_bounds(
    prim: Usd.Prim, transform: Gf.Matrix4d | None = None
) -> Gf.Range3d:
    """World-space bound of every point under `prim`, optionally under `transform`."""
    to_world = transform or Gf.Matrix4d(1.0)
    bounds = Gf.Range3d()
    cache = UsdGeom.XformCache()
    for mesh in Usd.PrimRange(prim):
        if not mesh.IsA(_MESH_SCHEMA):
            continue
        matrix = cache.GetLocalToWorldTransform(mesh) * to_world
        for point in UsdGeom.Mesh(mesh).GetPointsAttr().Get() or []:
            bounds.UnionWith(matrix.Transform(Gf.Vec3d(point)))
    return bounds


def bounds_match(before: Gf.Range3d, after: Gf.Range3d, tolerance: float) -> bool:
    """Whether a split left the piece where it was, in world space."""
    return all(
        abs(_component(before.GetMin(), i) - _component(after.GetMin(), i)) <= tolerance
        and abs(_component(before.GetMax(), i) - _component(after.GetMax(), i))
        <= tolerance
        for i in range(3)
    )


def _component(vec: Gf.Vec3d, index: int) -> float:
    # The pxr stubs' first `Vec3d.__getitem__` overload claims a list; it is a float.
    return cast(float, vec[index])
