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

    `bands` holds `(height_offset_from_center, swept_radius)` pairs. Fitting
    this profile frames rounded or tapered assets tighter than a uniform
    cylinder would, which is where the wasted space at a tilted view comes from.
    The asset is vertically centered on `center_y`.
    """

    center_y: float
    bands: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class CameraFit:
    """Camera distance and world-space aim height for one turnaround pass."""

    distance: float
    aim_y: float


def sample_review_surface(roots: tuple[str, ...]) -> SurfaceSamples:
    """Read every review mesh's world-space points and per-triangle centroids."""

    to_ui = om.MDistance.internalToUI(1.0)

    points: list[Vec3] = []
    centroids: list[Vec3] = []
    areas: list[float] = []

    for mesh_path in _review_mesh_shapes(roots):
        mesh = om.MFnMesh(_dag_path(mesh_path))
        world_points = mesh.getPoints(om.MSpace.kWorld)
        points.extend((p.x * to_ui, p.y * to_ui, p.z * to_ui) for p in world_points)

        _, triangle_vertices = mesh.getTriangles()
        for i in range(0, len(triangle_vertices), 3):
            a = world_points[triangle_vertices[i]]
            b = world_points[triangle_vertices[i + 1]]
            c = world_points[triangle_vertices[i + 2]]
            centroids.append(
                (
                    (a.x + b.x + c.x) / 3.0 * to_ui,
                    (a.y + b.y + c.y) / 3.0 * to_ui,
                    (a.z + b.z + c.z) / 3.0 * to_ui,
                )
            )
            areas.append(0.5 * ((b - a) ^ (c - a)).length() * to_ui * to_ui)

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


def fit_camera(
    profile: SweptProfile,
    *,
    elevation: float,
    vertical_fov: float,
    aspect: float,
    padding: float,
) -> CameraFit:
    """Closest camera that keeps every swept point in frame across the spin.

    `elevation` is the camera's angle above level, in radians.
    """

    tan_v = math.tan(vertical_fov * 0.5)
    tan_h = tan_v * aspect
    cos_e, sin_e = math.cos(elevation), math.sin(elevation)

    # Tightest distance-from-aim demanded by each screen edge, per band.
    top = max(
        y * (cos_e / tan_v + sin_e) + r * abs(sin_e / tan_v - cos_e)
        for y, r in profile.bands
    )
    bottom = max(
        y * (sin_e - cos_e / tan_v) + r * (sin_e / tan_v + cos_e)
        for y, r in profile.bands
    )
    side = max(y * sin_e + r * math.hypot(cos_e, 1.0 / tan_h) for y, r in profile.bands)

    # Raising the aim loosens the top constraint and tightens the bottom, both
    # linearly, so the aim height that centers a lopsided silhouette (a squat
    # asset seen from above is bottom-heavy) is also closed-form. Looking
    # straight down, aim height only slides the camera along its own axis and
    # cannot recenter
    aim_offset = 0.0 if cos_e < 1e-6 else (top - bottom) * tan_v / (2.0 * cos_e)

    distance = padding * max(
        top - aim_offset * (cos_e / tan_v + sin_e),
        bottom + aim_offset * (cos_e / tan_v - sin_e),
        side - aim_offset * sin_e,
        _MIN_EXTENT,
    )
    return CameraFit(distance=distance, aim_y=profile.center_y + aim_offset)


def bounding_radius(profile: SweptProfile, aim_y: float) -> float:
    """Radius of a sphere around the aim point containing all swept geometry."""

    offset = profile.center_y - aim_y
    return max(math.hypot(y + offset, r) for y, r in profile.bands)


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
    "CameraFit",
    "SurfaceSamples",
    "SweptProfile",
    "area_weighted_centroid",
    "bounding_radius",
    "fit_camera",
    "pivot_override",
    "sample_review_surface",
    "swept_profile",
]
