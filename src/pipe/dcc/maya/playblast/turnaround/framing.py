from __future__ import annotations

import math
from dataclasses import dataclass

import maya.api.OpenMaya as om
import maya.cmds as mc

Vec3 = tuple[float, float, float]

# An asset may pin its own spin axis by adding a locator with this name.
PIVOT_LOCATOR_NAME = "turnaround_pivot"

_MIN_EXTENT = 0.001

# Height bands used to measure how wide the asset sweeps at each level.
_PROFILE_BANDS = 64


@dataclass(frozen=True)
class SurfaceSamples:
    """World-space surface of the review geometry, sampled once up front."""

    points: tuple[Vec3, ...]
    triangle_centroids: tuple[Vec3, ...]
    triangle_areas: tuple[float, ...]


@dataclass(frozen=True)
class SweptProfile:
    """How wide the asset sweeps at each height as it spins about the axis.

    `bands` holds `(height_offset_from_center, swept_radius)` pairs. Projecting
    this profile frames rounded or tapered assets tighter than a uniform
    cylinder would, which is where the wasted space at a tilted view comes from.
    The asset is centered on `center_y`; the camera aims there.
    """

    center_y: float
    bands: tuple[tuple[float, float], ...]


def sample_review_surface(roots: tuple[str, ...]) -> SurfaceSamples:
    """Read every review mesh's world-space points and per-triangle centroids."""

    points: list[Vec3] = []
    centroids: list[Vec3] = []
    areas: list[float] = []

    for mesh_path in _review_mesh_shapes(roots):
        mesh = om.MFnMesh(_dag_path(mesh_path))
        world_points = mesh.getPoints(om.MSpace.kWorld)
        points.extend((p.x, p.y, p.z) for p in world_points)

        _, triangle_vertices = mesh.getTriangles()
        for i in range(0, len(triangle_vertices), 3):
            a = world_points[triangle_vertices[i]]
            b = world_points[triangle_vertices[i + 1]]
            c = world_points[triangle_vertices[i + 2]]
            centroids.append(
                (
                    (a.x + b.x + c.x) / 3.0,
                    (a.y + b.y + c.y) / 3.0,
                    (a.z + b.z + c.z) / 3.0,
                )
            )
            areas.append(0.5 * ((b - a) ^ (c - a)).length())

    if not points:
        raise ValueError("No reviewable mesh geometry was found to frame.")

    return SurfaceSamples(tuple(points), tuple(centroids), tuple(areas))


def pivot_override() -> tuple[float, float] | None:
    """Read the XZ spin axis from a `turnaround_pivot` locator, if one exists."""

    if not mc.objExists(PIVOT_LOCATOR_NAME):
        return None
    # maya-stubs types the query form as `bool`; it returns a 3-float list.
    x, _, z = mc.xform(  # type: ignore
        PIVOT_LOCATOR_NAME, query=True, worldSpace=True, translation=True
    )
    return (float(x), float(z))


def area_weighted_centroid(samples: SurfaceSamples) -> tuple[float, float]:
    """The surface's XZ center of area — the default spin axis."""

    total_area = sum(samples.triangle_areas)
    if total_area <= 0.0:
        return _mean_xz(samples.points)

    x = sum(
        c[0] * a for c, a in zip(samples.triangle_centroids, samples.triangle_areas)
    )
    z = sum(
        c[2] * a for c, a in zip(samples.triangle_centroids, samples.triangle_areas)
    )
    return (x / total_area, z / total_area)


def swept_profile(samples: SurfaceSamples, pivot: tuple[float, float]) -> SweptProfile:
    """Measure the swept radius in each height band about the spin axis."""

    pivot_x, pivot_z = pivot
    min_y = min(y for _, y, _ in samples.points)
    max_y = max(y for _, y, _ in samples.points)
    center_y = (min_y + max_y) * 0.5
    span = max(max_y - min_y, _MIN_EXTENT)

    band_radius = [0.0] * _PROFILE_BANDS
    for x, y, z in samples.points:
        index = int((y - min_y) / span * (_PROFILE_BANDS - 1))
        band_radius[index] = max(
            band_radius[index], math.hypot(x - pivot_x, z - pivot_z)
        )

    band_height = span / _PROFILE_BANDS
    bands = tuple(
        (min_y + (index + 0.5) * band_height - center_y, radius)
        for index, radius in enumerate(band_radius)
        if radius > 0.0
    )
    return SweptProfile(center_y=center_y, bands=bands or ((0.0, _MIN_EXTENT),))


def projected_frame(profile: SweptProfile, elevation: float) -> tuple[float, float]:
    """On-screen width and height of the swept silhouette.

    `elevation` is the camera's angle above level, in radians. Width is the swept
    diameter; height is the silhouette's vertical extent about the spin center.
    """

    cos_e, sin_e = math.cos(elevation), math.sin(elevation)
    max_radius = max(radius for _, radius in profile.bands)
    top = max(y * cos_e + radius * sin_e for y, radius in profile.bands)
    bottom = min(y * cos_e - radius * sin_e for y, radius in profile.bands)

    width = 2.0 * max_radius
    height = max(top - bottom, _MIN_EXTENT)
    return (width, height)


def fit_distance(
    width: float,
    height: float,
    *,
    vertical_fov: float,
    aspect: float,
    padding: float,
) -> float:
    """Distance at which a width x height rectangle just fills the frame."""

    half_vertical = vertical_fov * 0.5
    half_horizontal = math.atan(math.tan(half_vertical) * aspect)
    fit_vertical = (height * 0.5) / math.tan(half_vertical)
    fit_horizontal = (width * 0.5) / math.tan(half_horizontal)
    return max(fit_vertical, fit_horizontal) * padding


def _review_mesh_shapes(roots: tuple[str, ...]) -> tuple[str, ...]:
    shapes_by_uuid: dict[str, str] = {}
    for root in roots:
        for mesh in mc.ls(root, dagObjects=True, long=True, type="mesh") or []:
            if mc.getAttr(f"{mesh}.intermediateObject"):
                continue
            shapes_by_uuid[mc.ls(mesh, uuid=True)[0]] = str(mesh)
    return tuple(shapes_by_uuid.values())


def _dag_path(node: str) -> om.MDagPath:
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _mean_xz(points: tuple[Vec3, ...]) -> tuple[float, float]:
    count = len(points)
    x = sum(p[0] for p in points) / count
    z = sum(p[2] for p in points) / count
    return (x, z)


__all__ = [
    "PIVOT_LOCATOR_NAME",
    "SurfaceSamples",
    "SweptProfile",
    "area_weighted_centroid",
    "fit_distance",
    "pivot_override",
    "projected_frame",
    "sample_review_surface",
    "swept_profile",
]
